"""
server.py — SC/BC Downloader Cloud Server v1.0
Deployable en Railway, Render, o cualquier VPS.

Recibe una URL de SoundCloud o Bandcamp,
descarga el audio con yt-dlp y lo devuelve
como descarga directa al navegador (streaming).

Requisitos (ver requirements.txt):
  flask, yt-dlp

Variables de entorno opcionales:
  PORT        — puerto (default: 8080)
  API_KEY     — clave secreta para proteger el endpoint (opcional)
  MAX_SIZE_MB — tamaño máximo de descarga en MB (default: 200)
"""

import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, stream_with_context

app = Flask(__name__)

PORT       = int(os.environ.get("PORT", 8080))
API_KEY    = os.environ.get("API_KEY", "")          # vacío = sin autenticación
MAX_SIZE   = int(os.environ.get("MAX_SIZE_MB", 200)) * 1024 * 1024
VERSION    = "1.0.0"

# ─── CORS ────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options(_path):
    return Response(status=204)

# ─── Auth ─────────────────────────────────────────────────────

def check_auth():
    if not API_KEY:
        return True
    return request.headers.get("X-API-Key") == API_KEY

# ─── Validación ───────────────────────────────────────────────

ALLOWED_HOSTS = {
    "soundcloud.com", "www.soundcloud.com",
    "bandcamp.com",   "www.bandcamp.com",
}

def is_valid_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return host in ALLOWED_HOSTS or host.endswith(".bandcamp.com")
    except Exception:
        return False

def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return name or "track"

# ─── Rutas ────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": VERSION})

@app.route("/download", methods=["POST"])
def download():
    if not check_auth():
        return jsonify({"status": "error", "message": "No autorizado"}), 401

    data = request.get_json(silent=True) or {}
    url  = data.get("url",  "").strip()
    mode = data.get("mode", "track").strip()

    if not url:
        return jsonify({"status": "error", "message": "Falta el campo 'url'"}), 400
    if not is_valid_url(url):
        return jsonify({"status": "error", "message": "URL no válida"}), 400
    if mode not in ("track", "album", "playlist"):
        mode = "track"

    # Para álbumes devolvemos un ZIP con todos los tracks
    if mode in ("album", "playlist"):
        return download_collection(url, mode)
    else:
        return download_single(url)


def download_single(url: str):
    """Descarga un track y lo devuelve como stream al navegador."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "%(uploader)s - %(title)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",  "mp3",
            "--audio-quality", "320K",
            "--embed-thumbnail",
            "--embed-metadata",
            "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "ffmpeg:-id3v2_version 3 -b:a 320k",
            "--no-playlist",
            "--output", out_template,
            "--no-overwrites",
            url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Buscar el archivo generado
        mp3_files = list(Path(tmpdir).glob("*.mp3"))
        if not mp3_files:
            error = (result.stderr.strip().split("\n") or ["Error desconocido"])[-1]
            return jsonify({"status": "error", "message": error[:300]}), 500

        mp3_path = mp3_files[0]
        filename = safe_filename(mp3_path.name)

        # Leer y devolver como descarga directa
        file_data = mp3_path.read_bytes()

        return Response(
            file_data,
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length":      str(len(file_data)),
                "X-Filename":          filename,
            }
        )


def download_collection(url: str, mode: str):
    """Descarga un álbum/playlist completo y lo devuelve como ZIP."""
    import zipfile
    import io

    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(
            tmpdir,
            "%(playlist_index|00)s - %(uploader)s - %(title)s.%(ext)s"
        )

        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",  "mp3",
            "--audio-quality", "320K",
            "--embed-thumbnail",
            "--embed-metadata",
            "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "ffmpeg:-id3v2_version 3 -b:a 320k",
            "--yes-playlist",
            "--ignore-errors",
            "--output", out_template,
            url,
        ]

        subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        mp3_files = sorted(Path(tmpdir).glob("*.mp3"))
        if not mp3_files:
            return jsonify({"status": "error", "message": "No se encontraron tracks"}), 500

        # Empaquetar en ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in mp3_files:
                zf.write(f, f.name)
        zip_buffer.seek(0)

        # Nombre del ZIP basado en el primer archivo
        zip_name = safe_filename(mp3_files[0].stem.split(" - ", 2)[-1] if mp3_files else "album") + ".zip"

        return Response(
            zip_buffer.read(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name}"',
                "X-Filename": zip_name,
            }
        )


# ─── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
