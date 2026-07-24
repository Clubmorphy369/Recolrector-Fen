import os
import tempfile
import requests
import base64
import cv2
import numpy as np
from flask import Flask, request, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename
from pdf2image import convert_from_bytes
from PIL import Image
import io
import traceback
import shutil
from datetime import datetime, timezone
import re

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024
UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend')

def clean_fen(raw_fen):
    if not raw_fen:
        return None
    fen = raw_fen.replace('_', ' ')
    parts = fen.split()
    if len(parts) >= 6:
        return ' '.join(parts[:6])
    return None

@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)

# ---------- DIVISIÓN POR CUADRÍCULA ----------
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
                    cropped.append(crop)
        return cropped
    except Exception as e:
        print(f"[ERROR] split_grid: {e}")
        return []

# ---------- DETECCIÓN DE TABLERO MEJORADA ----------
def crop_board_center(image, crop_percent=0.70):
    """
    Recorta un cuadrado central de la imagen.
    crop_percent: proporción del lado más pequeño que se recortará (0.70 = 70%).
    """
    h, w = image.shape[:2]
    size = min(h, w)
    crop_size = int(size * crop_percent)
    # Calcular centro
    center_x = w // 2
    center_y = h // 2
    half = crop_size // 2
    x1 = max(0, center_x - half)
    y1 = max(0, center_y - half)
    x2 = min(w, center_x + half)
    y2 = min(h, center_y + half)
    return image[y1:y2, x1:x2]

def detect_board(image):
    """
    Intenta detectar el tablero de ajedrez en la imagen.
    Retorna un recorte del tablero o None si falla.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 1. Usar findChessboardCorners (específico para tableros)
    ret, corners = cv2.findChessboardCorners(gray, (7, 7), None)
    if ret:
        # Obtener rectángulo delimitador de las esquinas
        pts = corners.reshape(-1, 2)
        x_min = int(np.min(pts[:, 0]))
        x_max = int(np.max(pts[:, 0]))
        y_min = int(np.min(pts[:, 1]))
        y_max = int(np.max(pts[:, 1]))
        margin = 10
        x1 = max(0, x_min - margin)
        y1 = max(0, y_min - margin)
        x2 = min(w, x_max + margin)
        y2 = min(h, y_max + margin)
        return image[y1:y2, x1:x2]

    # 2. Detección por contornos (más tolerante)
    # Mejorar contraste con CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    gray_eq = clahe.apply(gray)
    # Umbral adaptativo
    thresh = cv2.adaptiveThreshold(gray_eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 15, 2)
    # Morfología para conectar bordes
    kernel = np.ones((5, 5), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Buscar el contorno más grande que sea aproximadamente cuadrado
    best_rect = None
    max_area = 0
    min_area = 5000  # área mínima para considerar

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4:
            x, y, w_box, h_box = cv2.boundingRect(cnt)
            aspect = w_box / h_box
            if 0.7 < aspect < 1.3:  # cuadrado
                if area > max_area:
                    max_area = area
                    best_rect = (x, y, w_box, h_box)

    if best_rect is not None:
        x, y, w_box, h_box = best_rect
        margin = 10
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(w, x + w_box + margin)
        y2 = min(h, y + h_box + margin)
        return image[y1:y2, x1:x2]

    # 3. Fallback: recorte central cuadrado
    print("[INFO] No se detectó tablero, usando recorte central.")
    return crop_board_center(image, crop_percent=0.75)

# ---------- DETECCIÓN PRINCIPAL ----------
def detect_boards_in_image(image_bytes, use_grid=False):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return [image_bytes]

        if use_grid:
            result = split_grid(img, rows=3, cols=2, margin=10)
            if result:
                return result
            # Si grid falla, intentar detección individual
            board = detect_board(img)
            if board is not None:
                return [board]
            return [img]

        # Para imágenes sueltas, detectar un solo tablero
        board = detect_board(img)
        if board is not None:
            return [board]

        # Fallback: recorte central
        central_crop = crop_board_center(img, crop_percent=0.75)
        return [central_crop]
    except Exception as e:
        print(f"[ERROR] detect_boards_in_image: {e}")
        return [image_bytes]

# ---------- PROCESAR UN TABLERO (FEN + MINIATURA) ----------
def process_board_image(board_img, original_img_bytes=None):
    fen = None
    thumbnail_b64 = None
    error_msg = None

    try:
        if board_img is None:
            if original_img_bytes is not None:
                nparr = np.frombuffer(original_img_bytes, np.uint8)
                board_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if board_img is None:
                return None, None, "No se pudo obtener imagen de tablero"

        # --- Obtener FEN ---
        _, board_bytes = cv2.imencode('.jpg', board_img)
        board_bytes = board_bytes.tobytes()

        img = Image.open(io.BytesIO(board_bytes))
        if img.size[0] > 1000 or img.size[1] > 1000:
            img.thumbnail((1000, 1000))
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=75)
            board_bytes = buffer.getvalue()

        encoded_string = base64.b64encode(board_bytes).decode('utf-8')
        payload = {
            "board_orientation": "predict",
            "cropped": False,
            "current_player": "white",
            "image": f"data:image/jpeg;base64,{encoded_string}",
            "predict_turn": True
        }
        response = requests.post('http://app.chessvision.ai/predict', json=payload, timeout=15)
        print(f"[DEBUG] Chessvision.ai status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                raw_fen = data.get('result')
                fen = clean_fen(raw_fen)
                if fen:
                    print(f"[INFO] FEN obtenido: {fen}")
                else:
                    error_msg = f"FEN inválido: {raw_fen}"
                    print(f"[WARN] {error_msg}")
            else:
                error_msg = f"Chessvision.ai success=false: {data.get('message', '')}"
                print(f"[WARN] {error_msg}")
        else:
            error_msg = f"Chessvision.ai HTTP {response.status_code}: {response.text[:100]}"
            print(f"[ERROR] {error_msg}")

        # --- Generar miniatura (200x200) ---
        if board_img is not None and len(board_img.shape) == 3:
            h, w = board_img.shape[:2]
            size = 200
            scale = min(size / w, size / h) if w > 0 and h > 0 else 1.0
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resized = cv2.resize(board_img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            canvas = np.ones((size, size, 3), dtype=np.uint8) * 255
            x_offset = (size - new_w) // 2
            y_offset = (size - new_h) // 2
            canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized
            _, buffer = cv2.imencode('.jpg', canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            thumbnail_b64 = base64.b64encode(buffer).decode('utf-8')
        else:
            error_msg = "Imagen de tablero no válida para miniatura"

        return fen, thumbnail_b64, error_msg
    except Exception as e:
        err = str(e)
        print(f"[ERROR] process_board_image: {err}")
        return None, None, err

# ---------- ENDPOINT DE SUBIDA ----------
@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No se enviaron archivos'}), 400
        files = request.files.getlist('files')
        if not files:
            return jsonify({'error': 'No se seleccionaron archivos'}), 400

        pdf_count = 0
        image_count = 0
        for f in files:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext == 'pdf':
                pdf_count += 1
            elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
                image_count += 1

        if pdf_count > 3:
            return jsonify({'error': 'Máximo 3 archivos PDF'}), 400
        if image_count > 10:
            return jsonify({'error': 'Máximo 10 imágenes'}), 400
        if len(files) > 10:
            return jsonify({'error': 'Máximo 10 archivos en total'}), 400

        pages_str = request.form.get('pages', '')
        selected_pages = []
        if pages_str:
            try:
                selected_pages = [int(p.strip()) for p in pages_str.split(',') if p.strip().isdigit()]
            except:
                selected_pages = []

        results = []
        for file in files:
            original_filename = file.filename
            filename = secure_filename(original_filename)
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            file_bytes = file.read()
            print(f"[INFO] Procesando: {original_filename} ({len(file_bytes)} bytes)")

            if ext == 'pdf':
                try:
                    total_pages = None
                    try:
                        from PyPDF2 import PdfReader
                        reader = PdfReader(io.BytesIO(file_bytes))
                        total_pages = len(reader.pages)
                        print(f"[INFO] PDF tiene {total_pages} páginas.")
                    except:
                        print(f"[WARN] No se pudo contar páginas, asumiendo 1.")
                    if total_pages is None:
                        total_pages = 1

                    if not selected_pages:
                        selected_pages = [1]
                    valid_pages = [p for p in selected_pages if 1 <= p <= total_pages]
                    if not valid_pages:
                        return jsonify({'error': f'No hay páginas válidas (PDF tiene {total_pages} páginas)'}), 400
                    if len(valid_pages) > 3:
                        valid_pages = valid_pages[:3]

                    for page_num in valid_pages:
                        try:
                            img = convert_from_bytes(file_bytes, dpi=150, first_page=page_num, last_page=page_num)[0]
                            img_bytes = io.BytesIO()
                            img.save(img_bytes, format='JPEG', quality=75)
                            img_bytes.seek(0)
                            board_images = detect_boards_in_image(img_bytes.getvalue(), use_grid=True)
                            for board_img in board_images:
                                fen, thumbnail, error = process_board_image(board_img, original_img_bytes=file_bytes)
                                if fen and thumbnail:
                                    results.append({
                                        'original_filename': original_filename,
                                        'file': filename,
                                        'page': page_num,
                                        'fen': fen,
                                        'thumbnail': thumbnail,
                                        'error': None
                                    })
                                else:
                                    results.append({
                                        'original_filename': original_filename,
                                        'file': filename,
                                        'page': page_num,
                                        'fen': None,
                                        'thumbnail': thumbnail if thumbnail else None,
                                        'error': error or 'No se pudo obtener FEN'
                                    })
                        except Exception as e:
                            print(f"[ERROR] Página {page_num}: {traceback.format_exc()}")
                            results.append({'original_filename': original_filename, 'file': filename, 'page': page_num, 'error': f'Error en página {page_num}: {str(e)[:80]}'})
                except Exception as e:
                    print(f"[ERROR] PDF {filename}: {traceback.format_exc()}")
                    results.append({'original_filename': original_filename, 'file': filename, 'error': f'Error PDF: {str(e)[:80]}'})
            elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
                try:
                    board_images = detect_boards_in_image(file_bytes, use_grid=False)
                    for board_img in board_images:
                        fen, thumbnail, error = process_board_image(board_img, original_img_bytes=file_bytes)
                        if fen and thumbnail:
                            results.append({
                                'original_filename': original_filename,
                                'file': filename,
                                'fen': fen,
                                'thumbnail': thumbnail,
                                'error': None
                            })
                        else:
                            results.append({
                                'original_filename': original_filename,
                                'file': filename,
                                'fen': None,
                                'thumbnail': thumbnail if thumbnail else None,
                                'error': error or 'No se pudo obtener FEN'
                            })
                except Exception as e:
                    print(f"[ERROR] Imagen {filename}: {traceback.format_exc()}")
                    results.append({'original_filename': original_filename, 'file': filename, 'error': f'Error: {str(e)[:80]}'})
            else:
                results.append({'original_filename': original_filename, 'file': filename, 'error': 'Formato no soportado'})

        shutil.rmtree(app.config['UPLOAD_FOLDER'], ignore_errors=True)
        app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()

        return jsonify({'results': results, 'success': True})
    except Exception as e:
        print(f"[ERROR] upload_files: {traceback.format_exc()}")
        return jsonify({'error': f'Error interno: {str(e)[:100]}'}), 500

# ---------- EXPORTAR PGN ----------
@app.route('/export-pgn', methods=['POST'])
def export_pgn():
    try:
        data = request.get_json()
        fens = data.get('fens', [])
        study_name = data.get('study_name', 'Mi Estudio de Ajedrez')
        user = data.get('user', 'Anónimo')
        
        if not fens:
            return jsonify({'error': 'No se proporcionaron FEN'}), 400
        
        if len(fens) > 64:
            fens = fens[:64]
        
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y.%m.%d")
        time_str = now.strftime("%H:%M:%S")
        
        pgn_lines = []
        for idx, fen in enumerate(fens, 1):
            chapter_name = f"Capítulo {idx}"
            
            pgn_lines.append(f'[Event "{study_name}: {chapter_name}"]')
            pgn_lines.append(f'[Date "{date_str}"]')
            pgn_lines.append('[Result "*"]')
            pgn_lines.append('[Variant "Standard"]')
            pgn_lines.append('[ECO "?"]')
            pgn_lines.append('[Opening "?"]')
            pgn_lines.append(f'[StudyName "{study_name}"]')
            pgn_lines.append(f'[ChapterName "{chapter_name}"]')
            pgn_lines.append(f'[Annotator "https://lichess.org/@/{user}"]')
            pgn_lines.append(f'[FEN "{fen}"]')
            pgn_lines.append('[SetUp "1"]')
            pgn_lines.append(f'[UTCDate "{date_str}"]')
            pgn_lines.append(f'[UTCTime "{time_str}"]')
            pgn_lines.append('[ChapterMode "gamebook"]')
            pgn_lines.append("")
            pgn_lines.append(" *")
            pgn_lines.append("")
        
        pgn_text = "\n".join(pgn_lines)
        
        safe_study_name = re.sub(r'[^a-zA-Z0-9-]', '-', study_name).lower()
        safe_user = re.sub(r'[^a-zA-Z0-9-]', '-', user).lower()
        filename = f"lichess_study_{safe_study_name}_by_{safe_user}_{date_str.replace('.', '-')}.pgn"
        
        response = Response(pgn_text, mimetype='text/plain')
        response.headers.set("Content-Disposition", "attachment", filename=filename)
        return response
    except Exception as e:
        print(f"[ERROR] export_pgn: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
