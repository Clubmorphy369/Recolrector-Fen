import os
import tempfile
import requests
import base64
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pdf2image import convert_from_bytes
from PIL import Image
import io

# ⚠️ ESTA LÍNEA ES OBLIGATORIA
app = Flask(__name__)

# Configuración
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
    """Envía la imagen a Chessvision.ai y devuelve el FEN."""
    try:
        # Redimensionar si la imagen es muy grande (> 2 MB)
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
                # 🔥 MEJORA: Convertir con mayor resolución (300 DPI)
                images = convert_from_bytes(file_bytes, dpi=300)
                if len(images) > 20:
                    images = images[:20]
                for page_num, img in enumerate(images):
                    # 🔥 MEJORA: Guardar como JPEG de alta calidad
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='JPEG', quality=95)
                    img_bytes.seek(0)
                    fen = process_image_bytes(img_bytes.getvalue())
                    results.append({
                        'file': filename,
                        'page': page_num + 1,
                        'fen': fen if fen else None,
                        'error': None if fen else 'No se pudo obtener FEN'
                    })
            except Exception as e:
                results.append({'file': filename, 'error': f'Error PDF: {str(e)}'})
        elif ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp']:
            fen = process_image_bytes(file_bytes)
            results.append({
                'file': filename,
                'fen': fen if fen else None,
                'error': None if fen else 'No se pudo obtener FEN'
            })
        else:
            results.append({'file': filename, 'error': 'Formato no soportado'})

    return jsonify({'results': results, 'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
