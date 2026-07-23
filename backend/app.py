import os
import tempfile
import requests
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

def detect_and_crop_board(image_bytes):
    """Detecta el tablero de ajedrez y devuelve la imagen recortada."""
    try:
        # Convertir bytes a imagen OpenCV
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return image_bytes  # Si no se puede leer, devolver original
        
        # Convertir a escala de grises
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Aplicar umbral adaptativo para resaltar bordes
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        
        # Encontrar contornos
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Buscar el contorno más grande que sea aproximadamente cuadrado
        board_contour = None
        max_area = 0
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 1000:  # Ignorar contornos muy pequeños
                continue
            
            # Aproximar el contorno a un polígono
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            
            # Si tiene 4 vértices, probablemente es un rectángulo
            if len(approx) == 4:
                # Calcular la relación de aspecto
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h
                if 0.8 < aspect_ratio < 1.2:  # Casi cuadrado
                    if area > max_area:
                        max_area = area
                        board_contour = approx
        
        # Si encontramos el tablero, recortar
        if board_contour is not None:
            # Obtener el rectángulo delimitador
            x, y, w, h = cv2.boundingRect(board_contour)
            # Añadir un pequeño margen
            margin = int(min(w, h) * 0.02)
            x = max(0, x - margin)
            y = max(0, y - margin)
            w = min(img.shape[1] - x, w + 2 * margin)
            h = min(img.shape[0] - y, h + 2 * margin)
            
            # Recortar la imagen
            cropped = img[y:y+h, x:x+w]
            
            # Convertir de vuelta a bytes
            _, buffer = cv2.imencode('.jpg', cropped)
            return buffer.tobytes()
        
        # Si no se detectó tablero, devolver la imagen original
        return image_bytes
        
    except Exception as e:
        print(f"[DEBUG] Error en detección de tablero: {e}")
        return image_bytes

def process_image_bytes(image_bytes):
    """Envía la imagen a la API de Lichess (con recorte previo)."""
    try:
        # Primero intentar recortar el tablero
        cropped_bytes = detect_and_crop_board(image_bytes)
        
        response = requests.post(
            'https://lichess.org/api/image-to-fen',
            files={'image': ('image.jpg', cropped_bytes, 'image/jpeg')}
        )
        
        print(f"[DEBUG] Lichess status: {response.status_code}")
        print(f"[DEBUG] Lichess response: {response.text[:200]}")
        
        if response.status_code == 200:
            data = response.json()
            return data.get('fen')
        else:
            return f"Error {response.status_code}: {response.text[:100]}"
    except Exception as e:
        return f"Excepción: {str(e)}"

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'No se enviaron archivos'}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No se seleccionaron archivos'}), 400

    if len(files) > 10:
        return jsonify({'error': 'Máximo 10 archivos por solicitud'}), 400

    results = []

    for file in files:
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        file_bytes = file.read()

        if ext == 'pdf':
            try:
                images = convert_from_bytes(file_bytes)
                if len(images) > 20:
                    images = images[:20]
                for page_num, img in enumerate(images):
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    fen = process_image_bytes(img_bytes.getvalue())
                    results.append({
                        'file': filename,
                        'page': page_num + 1,
                        'fen': fen if fen else None,
                        'error': None if fen else 'No se pudo obtener FEN'
                    })
            except Exception as e:
                results.append({
                    'file': filename,
                    'error': f'Error al convertir PDF: {str(e)}'
                })
        elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
            fen = process_image_bytes(file_bytes)
            results.append({
                'file': filename,
                'fen': fen if fen else None,
                'error': None if fen else 'No se pudo obtener FEN'
            })
        else:
            results.append({
                'file': filename,
                'error': 'Formato no soportado (usa PNG, JPG, GIF, BMP o PDF)'
            })

    return jsonify({'results': results, 'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
