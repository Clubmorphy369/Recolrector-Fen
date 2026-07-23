FROM python:3.10-slim

WORKDIR /app

# Copiar backend y frontend
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Instalar dependencias
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "backend.app:app"]
