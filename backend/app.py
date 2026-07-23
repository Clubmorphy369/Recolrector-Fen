import os
import tempfile
import requests
import base64
import cv2
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_bytes
from PIL import Image
import io
import traceback
import sys

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024  # 30 MB (reducido para evitar timeouts)
UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend')

@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)

def split_grid(image, rows=3, cols=2, margin=15):
    try:
        h, w = image.shape[:2]
        cell_h = h // rows
        cell_w = w // cols
        cropped = []
        for r in range(rows):
            for c in range(cols):
                x1 = c * cell_w
                y1 = r * cell_h
                x2 = (c + 1) * cell_w
                y2 = (r + 1) * cell_h
                x1c = max(0, x1 + margin)
                y1c = max(0, y1 + margin)
                x2c = min(w, x2 - margin)
                y2c = min(h, y2 - margin)
                if x2c > x1c and y2c > y1c:
                    crop = image[y1c:y2c, x1c:x2c]
                    _, buffer = cv2.imencode('.jpg', crop)
                    cropped.append(buffer.tobytes())
        return cropped
    except Exception as e:
        print(f"[ERROR] split_grid: {e}")
        return []

def detect_boards_contours(image, min_area=3000):
    try:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray = clahe.apply(gray)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 15, 2)
        kernel = np.ones((5, 5), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
        
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        board_rects = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > (h * w * 0.8):
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if 4 <= len(approx) <= 8:
                x, y, w_box, h_box = cv2.boundingRect(cnt)
                aspect = w_box / h_box
                if 0.5 < aspect < 1.5:
                    board_rects.append((x, y, w_box, h_box, area))
        
        board_rects.sort(key=lambda r: r[4], reverse=True)
        filtered = []
        for rect in board_rects:
            x1, y1, w1, h1, _ = rect
            overlap = False
            for existing in filtered:
                x2, y2, w2, h2, _ = existing
                if (x1 < x2 + w2 and x1 + w1 > x2 and
                    y1 < y2 + h2 and y1 + h1 > y2):
                    if rect[4] < existing[4]:
                        overlap = True
                        break
            if not overlap:
                filtered.append(rect)
        
        if len(filtered) >= 4:
            cropped = []
            for (x, y, w_box, h_box, _) in filtered:
                margin = 10
                x1 = max(0, x - margin)
                y1 = max(0, y - margin)
                x2 = min(w, x + w_box + margin)
                y2 = min(h, y + h_box + margin)
                crop = image[y1:y2, x1:x2]
                _, buffer = cv2.imencode('.jpg', crop)
                cropped.append(buffer.tobytes())
            return cropped
        return None
    except Exception as e:
        print(f"[ERROR] detect_boards_contours: {e}")
        return None

def detect_boards_in_image(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return [image_bytes]
        
        # Intentar detección por contornos
        contour_results = detect_boards_contours(img)
        if contour_results is not None:
            return contour_results
        
        # Fallback: cuadrícula fija (3x2)
        grid_results = split_grid(img, rows=3, cols=2, margin=20)
        if grid_results:
            return grid_results
        
        return [image_bytes]
    except Exception as e:
        print(f"[ERROR] detect_boards_in_image: {e}")
        return [image_bytes]

def process_image_bytes(image_bytes, max_retries=1):
    """Envía a Chessvision.ai con reintentos."""
    for attempt in range(max_retries + 1):
        try:
            img = Image.open(io.BytesIO(image_bytes))
            if img.size[0] > 1500 or img.size[1] > 1500:
                img.thumbnail((1500, 1500))
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                image_bytes = buffer.getvalue()
            elif img.size[0] < 50 or img.size[1] < 50:
                return "Imagen demasiado pequeña"

            encoded_string = base64.b64encode(image_bytes).decode('utf-8')
            payload = {
                "board_orientation": "predict",
                "cropped": False,
                "current_player": "white",
                "image": f"data:image/jpeg;base64,{encoded_string}",
                "predict_turn": True
            }
            response = requests.post(
                'http://app.chessvision.ai/predict',
                json=payload,
                timeout=15  # timeout reducido a 15 segundos
            )
            print(f"[DEBUG] Chessvision.ai status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('result')
                else:
                    return f"Error: {data.get('message', 'Error desconocido')}"
            else:
                return f"Error HTTP {response.status_code}"
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                continue
            return "Timeout Chessvision.ai"
        except Exception as e:
            if attempt < max_retries:
                continue
            return f"Error: {str(e)[:50]}"
    return "Error: falló después de reintentos"

@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No se enviaron archivos'}), 400
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No se seleccionaron archivos'}), 400
        if len(files) > 10:
            return jsonify({'error': 'Máximo 10 archivos'}), 400

        results = []
        for file in files:
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            file_bytes = file.read()
            print(f"[INFO] Procesando: {filename} (tamaño: {len(file_bytes)} bytes)")

            if ext == 'pdf':
                try:
                    images = convert_from_bytes(file_bytes, dpi=200)  # Reducir DPI para ahorrar memoria
                    if len(images) > 10:
                        images = images[:10]
                    for page_num, img in enumerate(images):
                        img_bytes = io.BytesIO()
                        img.save(img_bytes, format='JPEG', quality=80)
                        img_bytes.seek(0)
                        board_images = detect_boards_in_image(img_bytes.getvalue())
                        for board_idx, board_bytes in enumerate(board_images):
                            fen = process_image_bytes(board_bytes)
                            results.append({
                                'file': filename,
                                'page': page_num + 1,
                                'board': board_idx + 1,
                                'fen': fen if fen else None,
                                'error': None if fen else 'No se pudo obtener FEN'
                            })
                except Exception as e:
                    print(f"[ERROR] PDF {filename}: {traceback.format_exc()}")
                    results.append({'file': filename, 'error': f'Error PDF: {str(e)[:100]}'})
            elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
                try:
                    board_images = detect_boards_in_image(file_bytes)
                    for board_idx, board_bytes in enumerate(board_images):
                        fen = process_image_bytes(board_bytes)
                        results.append({
                            'file': filename,
                            'board': board_idx + 1,
                            'fen': fen if fen else None,
                            'error': None if fen else 'No se pudo obtener FEN'
                        })
                except Exception as e:
                    print(f"[ERROR] Imagen {filename}: {traceback.format_exc()}")
                    results.append({'file': filename, 'error': f'Error: {str(e)[:100]}'})
            else:
                results.append({'file': filename, 'error': 'Formato no soportado'})

        return jsonify({'results': results, 'success': True})
    except Exception as e:
        print(f"[ERROR] upload_files: {traceback.format_exc()}")
        return jsonify({'error': f'Error interno: {str(e)[:100]}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
