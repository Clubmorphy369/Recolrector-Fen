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

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
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
    """Divide una imagen en una cuadrícula de rows x cols con margen ajustable."""
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
            # Margen para evitar bordes negros y texto
            x1c = max(0, x1 + margin)
            y1c = max(0, y1 + margin)
            x2c = min(w, x2 - margin)
            y2c = min(h, y2 - margin)
            if x2c > x1c and y2c > y1c:
                crop = image[y1c:y2c, x1c:x2c]
                _, buffer = cv2.imencode('.jpg', crop)
                cropped.append(buffer.tobytes())
    return cropped

def detect_boards_by_contours(image, min_area=3000):
    """Detecta tableros de ajedrez por contornos."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Mejorar contraste
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    gray = clahe.apply(gray)
    
    # Umbral adaptativo más sensible
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 2)
    
    # Operaciones morfológicas para cerrar huecos
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
        
        # Aceptar polígonos con 4 a 8 lados (más tolerante)
        if 4 <= len(approx) <= 8:
            x, y, w_box, h_box = cv2.boundingRect(cnt)
            aspect = w_box / h_box
            # Permitir relación de aspecto más amplia (0.5 a 1.5)
            if 0.5 < aspect < 1.5:
                board_rects.append((x, y, w_box, h_box, area))
    
    # Ordenar por área (grande a pequeño)
    board_rects.sort(key=lambda r: r[4], reverse=True)
    
    # Filtrar rectángulos que se solapan (quedarse con los más grandes)
    filtered = []
    for rect in board_rects:
        x1, y1, w1, h1, _ = rect
        overlap = False
        for existing in filtered:
            x2, y2, w2, h2, _ = existing
            # Si los rectángulos se solapan significativamente
            if (x1 < x2 + w2 and x1 + w1 > x2 and
                y1 < y2 + h2 and y1 + h1 > y2):
                # Si el área es menor, descartar
                if rect[4] < existing[4]:
                    overlap = True
                    break
        if not overlap:
            filtered.append(rect)
    
    # Si encontramos al menos 4 tableros, confiamos en la detección
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
    
    return None  # No se detectaron suficientes tableros

def detect_boards_in_image(image_bytes, use_grid=False):
    """Detecta tableros con detección híbrida."""
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return [image_bytes]
        
        # Si se fuerza cuadrícula, usarla directamente
        if use_grid:
            return split_grid(img, rows=3, cols=2, margin=15)
        
        # Primero intentar detección por contornos
        contour_results = detect_boards_by_contours(img)
        if contour_results is not None:
            # Verificar que los recortes sean válidos (no todos errores)
            valid_count = 0
            for board_bytes in contour_results:
                fen = process_image_bytes(board_bytes)
                if fen and not fen.startswith('Error'):
                    valid_count += 1
                # Si al menos 4 son válidos, confiar en la detección
            if valid_count >= 4:
                # Procesar todos los recortes (incluyendo los que fallaron)
                return contour_results
        
        # Si la detección por contornos falla, usar cuadrícula
        return split_grid(img, rows=3, cols=2, margin=15)
        
    except Exception as e:
        print(f"[DEBUG] Error en detección: {e}")
        return [image_bytes]

def process_image_bytes(image_bytes):
    """Envía la imagen a Chessvision.ai y devuelve el FEN."""
    try:
        # Redimensionar si es muy grande
        img = Image.open(io.BytesIO(image_bytes))
        if img.size[0] > 1500 or img.size[1] > 1500:
            img.thumbnail((1500, 1500))
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=90)
            image_bytes = buffer.getvalue()
        elif img.size[0] < 100 or img.size[1] < 100:
            return "Error: Imagen demasiado pequeña"

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
            timeout=30
        )
        print(f"[DEBUG] Chessvision.ai status: {response.status_code}")
        print(f"[DEBUG] Chessvision.ai response: {response.text[:300]}")

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('result')
            else:
                return f"Error: {data.get('message', 'Error desconocido')}"
        else:
            return f"Error HTTP {response.status_code}"
    except requests.exceptions.Timeout:
        return "Error: Tiempo de espera agotado"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/upload', methods=['POST'])
def upload_files():
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

        if ext == 'pdf':
            try:
                images = convert_from_bytes(file_bytes, dpi=300)
                if len(images) > 20:
                    images = images[:20]
                for page_num, img in enumerate(images):
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='JPEG', quality=95)
                    img_bytes.seek(0)
                    # Usar detección híbrida (contornos + grid)
                    board_images = detect_boards_in_image(img_bytes.getvalue(), use_grid=False)
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
                results.append({'file': filename, 'error': f'Error PDF: {str(e)}'})
        elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
            board_images = detect_boards_in_image(file_bytes, use_grid=False)
            for board_idx, board_bytes in enumerate(board_images):
                fen = process_image_bytes(board_bytes)
                results.append({
                    'file': filename,
                    'board': board_idx + 1,
                    'fen': fen if fen else None,
                    'error': None if fen else 'No se pudo obtener FEN'
                })
        else:
            results.append({'file': filename, 'error': 'Formato no soportado'})

    return jsonify({'results': results, 'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
