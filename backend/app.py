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
import shutil
from PyPDF2 import PdfReader

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024  # 30 MB
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

# ---------- DIVISIÓN POR CUADRÍCULA (3 filas x 2 columnas) ----------
def split_grid(image, rows=3, cols=2, margin=10):
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

# ---------- DETECCIÓN HÍBRIDA ----------
def detect_boards_in_image(image_bytes, use_grid=True):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return [image_bytes]

        if use_grid:
            result = split_grid(img, rows=3, cols=2, margin=10)
            if result:
                return result
            return [image_bytes]

        result = split_grid(img, rows=3, cols=2, margin=10)
        if result:
            return result
        return [image_bytes]
    except Exception as e:
        print(f"[ERROR] detect_boards_in_image: {e}")
        return [image_bytes]

# ---------- PROCESAR CON CHESSVISION.AI ----------
def process_image_bytes(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.size[0] > 1000 or img.size[1] > 1000:
            img.thumbnail((1000, 1000))
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=75)
            image_bytes = buffer.getvalue()
        elif img.size[0] < 40 or img.size[1] < 40:
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
            timeout=12
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
        return "Timeout"
    except Exception as e:
        return f"Error: {str(e)[:50]}"

# ---------- ENDPOINT DE SUBIDA ----------
@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No se enviaron archivos'}), 400
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No se seleccionaron archivos'}), 400
        if len(files) > 3:
            return jsonify({'error': 'Máximo 3 archivos por solicitud'}), 400

        # Obtener lista de páginas (desde el frontend)
        pages_str = request.form.get('pages', '')
        selected_pages = []
        if pages_str:
            try:
                # Ejemplo: "1,3,5" -> [1,3,5]
                selected_pages = [int(p.strip()) for p in pages_str.split(',') if p.strip().isdigit()]
            except:
                selected_pages = []

        results = []
        for file in files:
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            file_bytes = file.read()
            print(f"[INFO] Procesando: {filename} ({len(file_bytes)} bytes)")

            if ext == 'pdf':
                try:
                    # Contar páginas del PDF
                    reader = PdfReader(io.BytesIO(file_bytes))
                    total_pages = len(reader.pages)
                    print(f"[INFO] PDF tiene {total_pages} páginas.")

                    # Si el usuario no especificó páginas, usar la primera página
                    if not selected_pages:
                        selected_pages = [1]

                    # Filtrar páginas que existen
                    valid_pages = [p for p in selected_pages if 1 <= p <= total_pages]

                    if not valid_pages:
                        return jsonify({'error': 'No hay páginas válidas para procesar'}), 400

                    # Limitar a 3 páginas (por seguridad)
                    if len(valid_pages) > 3:
                        valid_pages = valid_pages[:3]

                    # Procesar cada página seleccionada
                    for page_num in valid_pages:
                        # Convertir solo esa página (bajo DPI para ahorrar memoria)
                        img = convert_from_bytes(file_bytes, dpi=150, first_page=page_num, last_page=page_num)[0]
                        img_bytes = io.BytesIO()
                        img.save(img_bytes, format='JPEG', quality=75)
                        img_bytes.seek(0)
                        board_images = detect_boards_in_image(img_bytes.getvalue(), use_grid=True)
                        for board_idx, board_bytes in enumerate(board_images):
                            fen = process_image_bytes(board_bytes)
                            results.append({
                                'file': filename,
                                'page': page_num,
                                'board': board_idx + 1,
                                'fen': fen if fen else None,
                                'error': None if fen else 'No se pudo obtener FEN'
                            })
                except Exception as e:
                    print(f"[ERROR] PDF {filename}: {traceback.format_exc()}")
                    results.append({'file': filename, 'error': f'Error PDF: {str(e)[:80]}'})
            elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
                try:
                    board_images = detect_boards_in_image(file_bytes, use_grid=False)
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
                    results.append({'file': filename, 'error': f'Error: {str(e)[:80]}'})
            else:
                results.append({'file': filename, 'error': 'Formato no soportado'})

        # Limpiar temporales
        shutil.rmtree(app.config['UPLOAD_FOLDER'], ignore_errors=True)
        app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()

        return jsonify({'results': results, 'success': True})
    except Exception as e:
        print(f"[ERROR] upload_files: {traceback.format_exc()}")
        return jsonify({'error': f'Error interno: {str(e)[:80]}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
