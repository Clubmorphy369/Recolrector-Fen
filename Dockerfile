FROM python:3.10-slim

WORKDIR /app

# Instalar dependencias del sistema (poppler para PDF, libgl para OpenCV)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Crear directorio temporal
RUN mkdir -p /app/tmp && chmod 777 /app/tmp

# Copiar e instalar dependencias Python
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "backend.app:app"]
