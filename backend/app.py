import os
import tempfile
from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
import chesspdftofen

app = Flask(__name__, static_folder='../frontend', static_url_path='')

# Configuración
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

# Carpeta temporal para archivos (se limpia sola al reiniciar)
UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/upload', methods=['POST'])
def upload_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No se envió ningún archivo'}), 400

    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'Nombre de archivo vacío'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'El archivo debe ser un PDF'}), 400

    try:
        # Guardar archivo subido
        filename = secure_filename(file.filename)
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(input_path)

        # Ruta de salida
        output_filename = f"fen_{filename}"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

        # Procesar con chesspdftofen
        for status in chesspdftofen.run(input_path, output_path):
            # En Firebase, los prints aparecen en los logs
            print(status)

        # Verificar que se generó el archivo
        if not os.path.exists(output_path):
            return jsonify({'error': 'Error al procesar el PDF'}), 500

        # Enviar archivo resultante
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/pdf'
        )

    except Exception as e:
        return jsonify({'error': f'Error interno: {str(e)}'}), 500

if __name__ == '__main__':
    # Puerto dinámico para entornos como Cloud Run
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)