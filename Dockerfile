FROM python:3.11-slim

# Instalar ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PORT=8080

EXPOSE 8080

CMD gunicorn --bind 0.0.0.0:$PORT --timeout 600 --workers 2 server:app
