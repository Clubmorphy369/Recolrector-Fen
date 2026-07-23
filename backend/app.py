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

# Intentar importar PyPDF2 para contar páginas
try:
    from PyPDF2 import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("[WARN] PyPDF2 no instalado.")

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024
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

# ---------- DIVISIÓN POR CUADRÍCULA (para PDFs) ----------
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

# ---------- DETECCIÓN POR CONTORNOS (para imágenes sueltas) ----------
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

        if filtered:
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
            return [image_bytes]

        contour_result = detect_boards_contours(img)
        if contour_result:
            return contour_result

        _, buffer = cv2.imencode('.jpg', img)
        return [buffer.tobytes()]
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
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            file_bytes = file.read()
            print(f"[INFO] Procesando: {filename} ({len(file_bytes)} bytes)")

            if ext == 'pdf':
                try:
                    total_pages = None
                    if PDF_SUPPORT:
                        try:
                            reader = PdfReader(io.BytesIO(file_bytes))
                            total_pages = len(reader.pages)
                            print(f"[INFO] PDF tiene {total_pages} páginas.")
                        except Exception as e:
                            print(f"[WARN] No se pudo contar páginas: {e}")
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
                            print(f"[ERROR] Página {page_num}: {traceback.format_exc()}")
                            results.append({'file': filename, 'page': page_num, 'error': f'Error en página {page_num}: {str(e)[:80]}'})
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

        shutil.rmtree(app.config['UPLOAD_FOLDER'], ignore_errors=True)
        app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()

        return jsonify({'results': results, 'success': True})
    except Exception as e:
        print(f"[ERROR] upload_files: {traceback.format_exc()}")
        return jsonify({'error': f'Error interno: {str(e)[:100]}'}), 500

# ---------- EXPORTAR PGN (con formato correcto para Lichess) ----------
@app.route('/export-pgn', methods=['POST'])
def export_pgn():
    try:
        data = request.get_json()
        fens = data.get('fens', [])
        if not fens:
            return jsonify({'error': 'No se proporcionaron FEN'}), 400
        
        # Limitar a 64 capítulos (máximo permitido por Lichess)
        if len(fens) > 64:
            fens = fens[:64]
        
        # Construir el PGN con formato válido para Lichess
        pgn_lines = []
        for fen in fens:
            # Cada capítulo debe tener [SetUp "1"] y [FEN "..."]
            pgn_lines.append('[SetUp "1"]')
            pgn_lines.append(f'[FEN "{fen}"]')
            pgn_lines.append("*")  # Marcador de final de juego
            pgn_lines.append("")   # Línea en blanco entre capítulos
        pgn_text = "\n".join(pgn_lines)
        
        # Crear respuesta como archivo descargable
        response = Response(pgn_text, mimetype='text/plain')
        response.headers.set("Content-Disposition", "attachment", filename="fen_study.pgn")
        return response
    except Exception as e:
        print(f"[ERROR] export_pgn: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
