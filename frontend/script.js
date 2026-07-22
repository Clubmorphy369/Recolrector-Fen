document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('uploadForm');
    const fileInput = document.getElementById('pdfFile');
    const submitBtn = document.getElementById('submitBtn');
    const statusDiv = document.getElementById('status');
    const progressDiv = document.getElementById('progress');
    const progressFill = document.getElementById('progressFill');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const file = fileInput.files[0];
        if (!file) {
            statusDiv.innerText = '⚠️ Por favor, selecciona un archivo PDF.';
            return;
        }

        if (!file.name.toLowerCase().endsWith('.pdf')) {
            statusDiv.innerText = '⚠️ El archivo debe ser un PDF.';
            return;
        }

        const formData = new FormData();
        formData.append('pdf', file);

        submitBtn.disabled = true;
        submitBtn.innerText = '⏳ Procesando...';
        statusDiv.innerText = '📤 Subiendo y procesando... Esto puede tardar varios minutos.';
        progressDiv.style.display = 'block';
        progressFill.style.width = '0%';

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            // Simular progreso (porque no tenemos websocket)
            let progress = 0;
            const interval = setInterval(() => {
                progress += Math.random() * 10;
                if (progress > 90) clearInterval(interval);
                progressFill.style.width = Math.min(progress, 90) + '%';
            }, 500);

            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                // Intenta obtener el nombre de la cabecera
                const disposition = response.headers.get('Content-Disposition');
                let filename = 'fen_annotated.pdf';
                if (disposition && disposition.includes('filename=')) {
                    const parts = disposition.split('filename=');
                    filename = parts[1].replace(/["']/g, '');
                }
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);

                statusDiv.innerText = '✅ ¡PDF procesado y descargado!';
                progressFill.style.width = '100%';
            } else {
                const errorData = await response.json();
                statusDiv.innerText = `❌ Error: ${errorData.error || 'Error desconocido'}`;
                progressFill.style.width = '0%';
            }
        } catch (error) {
            statusDiv.innerText = `❌ Error de red: ${error.message}`;
            progressFill.style.width = '0%';
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerText = '🚀 Procesar PDF';
            setTimeout(() => {
                progressDiv.style.display = 'none';
                progressFill.style.width = '0%';
            }, 3000);
        }
    });

    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        document.querySelector('.file-label').textContent = file ? file.name : '📂 Seleccionar PDF';
    });
});