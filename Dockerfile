FROM python:3.10-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Crear directorio temporal
RUN mkdir -p /app/tmp && chmod 777 /app/tmp

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8080

# ⚠️ TIMEOUT AUMENTADO A 120 SEGUNDOS
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "120", "backend.app:app"]
