import os
import tempfile
import requests
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_bytes
from PIL import Image
import io

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
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

def process_image_bytes(image_bytes):
    """Envía los bytes de una imagen a la API de Lichess y devuelve el FEN."""
    try:
        response = requests.post(
            'https://lichess.org/api/image-to-fen',
            files={'image': image_bytes}
        )
        if response.status_code == 200:
            return response.json().get('fen')
        else:
            return None
    except Exception:
        return None

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
                # Convertir PDF a imágenes (página por página)
                images = convert_from_bytes(file_bytes)
                if len(images) > 20:
                    # Limitar a 20 páginas por PDF
                    images = images[:20]
                for page_num, img in enumerate(images):
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    fen = process_image_bytes(img_bytes)
                    results.append({
                        'file': filename,
                        'page': page_num + 1,
                        'fen': fen
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
                'fen': fen
            })
        else:
            results.append({
                'file': filename,
                'error': 'Formato no soportado (usa PNG, JPG, GIF, BMP o PDF)'
            })

    return jsonify({'results': results, 'success': True})
