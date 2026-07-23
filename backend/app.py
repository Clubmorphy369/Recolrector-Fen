import os
import tempfile
import requests
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='../frontend', static_url_path='')

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No se envió ningún archivo'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nombre de archivo vacío'}), 400
    
    # Validar extensión
    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
        return jsonify({'error': 'El archivo debe ser una imagen'}), 400
    
    try:
        # Guardar la imagen temporalmente
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Llamar a la API de Lichess
        with open(filepath, 'rb') as f:
            response = requests.post(
                'https://lichess.org/api/image-to-fen',
                files={'image': f}
            )
        
        if response.status_code == 200:
            fen = response.json().get('fen')
            return jsonify({'fen': fen, 'success': True})
        else:
            return jsonify({'error': 'Error al procesar la imagen con Lichess'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Error interno: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
