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

def detect_boards_in_image(image_bytes):
    """Detecta múltiples tableros en una imagen y devuelve una lista de recortes (bytes)."""
    try:
        # Convertir bytes a imagen OpenCV
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return [image_bytes]  # Si no se puede leer, devolver original
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Umbral adaptativo para resaltar bordes
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        
        # Encontrar contornos
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        board_rects = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 1000:  # Ignorar contornos muy pequeños
                continue
            
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            
            # Si tiene 4 vértices y es aproximadamente cuadrado
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h
                if 0.8 < aspect_ratio < 1.2:
                    board_rects.append((x, y, w, h))
        
        # Si no se detectaron tableros, devolver la imagen original
        if not board_rects:
            return [image_bytes]
        
        # Ordenar rectángulos de izquierda a derecha y de arriba a abajo
        board_rects.sort(key=lambda r: (r[1], r[0]))
        
        # Recortar cada tablero y guardarlo como bytes
        cropped_images = []
        for (x, y, w, h) in board_rects:
            # Añadir pequeño margen
            margin = 10
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(img.shape[1], x + w + margin)
            y2 = min(img.shape[0], y + h + margin)
            
            cropped = img[y1:y2, x1:x2]
            _, buffer = cv2.imencode('.jpg', cropped)
            cropped_images.append(buffer.tobytes())
        
        return cropped_images
        
    except Exception as e:
        print(f"[DEBUG] Error en detección de tableros: {e}")
        return [image_bytes]  # Si falla, devolver imagen original

def process_image_bytes(image_bytes):
    """Envía la imagen a Chessvision.ai y devuelve el FEN."""
    try:
        # Redimensionar si la imagen es muy grande
        img = Image.open(io.BytesIO(image_bytes))
        if img.size[0] > 1500 or img.size[1] > 1500:
            img.thumbnail((1500, 1500))
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=90)
            image_bytes = buffer.getvalue()
        
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
                # Convertir PDF a imágenes (una por página)
                images = convert_from_bytes(file_bytes, dpi=300)
                if len(images) > 20:
                    images = images[:20]
                for page_num, img in enumerate(images):
                    # Guardar página como JPEG
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='JPEG', quality=95)
                    img_bytes.seek(0)
                    
                    # Detectar y recortar múltiples tableros en la página
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
                results.append({'file': filename, 'error': f'Error PDF: {str(e)}'})
        elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
            # Para imágenes individuales, también detectar múltiples tableros
            board_images = detect_boards_in_image(file_bytes)
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
