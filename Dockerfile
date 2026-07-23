FROM python:3.10-slim

WORKDIR /app

# Instalar dependencias del sistema para OpenCV y pdf2image
RUN apt-get update && apt-get install -y \
    poppler-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "backend.app:app"]
