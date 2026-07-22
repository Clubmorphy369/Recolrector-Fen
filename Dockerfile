FROM python:3.10-slim

WORKDIR /app

# Copiar e instalar dependencias
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la app
COPY backend/ .

# Exponer el puerto que usará Cloud Run (siempre 8080)
EXPOSE 8080

# Comando para ejecutar la app con Gunicorn (servidor HTTP para producción)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
