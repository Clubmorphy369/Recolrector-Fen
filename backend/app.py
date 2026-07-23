import os
import tempfile
from flask import Flask, request, send_file, jsonify, render_template_string
from werkzeug.utils import secure_filename
from PIL import Image
import chess_diagram_to_fen

app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Configuración
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
    
    # Verificar que sea una imagen
    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
        return jsonify({'error': 'El archivo debe ser una imagen'}), 400
    
    try:
        # Guardar la imagen temporalmente
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Procesar la imagen con chess_diagram_to_fen
        img = Image.open(filepath)
        result = chess_diagram_to_fen.get_fen(
            img=img,
            game="chess",
            auto_rotate_image=True,
            auto_rotate_board=True
        )
        
        fen = result.fen
        return jsonify({'fen': fen, 'success': True})
        
    except Exception as e:
        return jsonify({'error': f'Error al procesar la imagen: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
