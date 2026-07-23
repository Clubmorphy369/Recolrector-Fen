FROM python:3.10-slim

WORKDIR /app

# Dependencias para OpenCV y pdf2image
RUN apt-get update && apt-get install -y \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Forzar invalidación de caché
RUN echo "Invalidating cache 2026-07-23"

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "backend.app:app"]
