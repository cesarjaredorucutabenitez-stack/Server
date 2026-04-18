"""
server.py — SC/BC Downloader Cloud Server v2.0
- Nombre de archivo correcto: Artista - Título.mp3
- Track individual: devuelve MP3 directo
- Álbum/Playlist: devuelve ZIP con todos los tracks
- 320kbps CBR + metadatos + carátula
"""
 
import io
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse, quote
 
from flask import Flask, Response, jsonify, request
 
app = Flask(__name__)
 
PORT    = int(os.environ.get("PORT", 8080))
API_KEY = os.environ.get("API_KEY", "")
VERSION = "2.0.0"
 
# ─── CORS ────────────────────────────────────────────────────
 
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition, X-Filename"
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
 
ALLOWED_HOSTS = {"soundcloud.com", "www.soundcloud.com", "bandcamp.com", "www.bandcamp.com"}
 
def is_valid_url(url):
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return host in ALLOWED_HOSTS or host.endswith(".bandcamp.com")
    except:
        return False
 
def safe_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return name or "track"
 
def content_disposition(filename):
    """Genera header Content-Disposition compatible con todos los navegadores."""
    ascii_name = filename.encode('ascii', 'ignore').decode('ascii') or "download"
    utf8_name  = quote(filename, safe='')
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
 
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
 
    if mode in ("album", "playlist"):
        return download_collection(url)
    else:
        return download_single(url)
 
 
def download_single(url):
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "%(uploader)s - %(title)s.%(ext)s")
 
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",  "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",
            "--embed-metadata",
            "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "EmbedThumbnail+ffmpeg:-id3v2_version 3 ffmpeg:-b:a 320k -ar 44100",
            "--no-playlist",
            "--output", out_template,
            "--no-overwrites",
            url,
        ]
 
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
 
        mp3_files = list(Path(tmpdir).glob("*.mp3"))
        if not mp3_files:
            error = (result.stderr.strip().split("\n") or ["Error desconocido"])[-1]
            return jsonify({"status": "error", "message": error[:300]}), 500
 
        mp3_path = mp3_files[0]
        filename = safe_filename(mp3_path.stem) + ".mp3"
        file_data = mp3_path.read_bytes()
 
        return Response(
            file_data,
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": content_disposition(filename),
                "Content-Length":      str(len(file_data)),
                "X-Filename":          filename,
            }
        )
 
 
def download_collection(url):
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(
            tmpdir,
            "%(playlist_index|00)s - %(uploader)s - %(title)s.%(ext)s"
        )
 
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format",  "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",
            "--embed-metadata",
            "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "EmbedThumbnail+ffmpeg:-id3v2_version 3 ffmpeg:-b:a 320k -ar 44100",
            "--yes-playlist",
            "--ignore-errors",
            "--output", out_template,
            url,
        ]
 
        # Obtener nombre del álbum/playlist desde yt-dlp
        # Intentar múltiples campos en orden de preferencia
        info_cmd = [
            "yt-dlp", "--flat-playlist", "--playlist-items", "1",
            "--print", "%(album,playlist_title,playlist,title)s",
            url
        ]
        info = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        raw_name = (info.stdout.strip().split("\n")[0] or "").strip()
 
        # Si sigue vacío o es genérico, intentar con --dump-json
        if not raw_name or raw_name.lower() in ("na", "none", "album", ""):
            json_cmd = ["yt-dlp", "--flat-playlist", "--playlist-items", "1",
                        "--dump-json", url]
            json_out = subprocess.run(json_cmd, capture_output=True, text=True, timeout=60)
            try:
                import json
                jdata = json.loads(json_out.stdout.strip().split("\n")[0])
                raw_name = (
                    jdata.get("album") or
                    jdata.get("playlist_title") or
                    jdata.get("playlist") or
                    jdata.get("title") or
                    "descarga"
                )
            except Exception:
                raw_name = "descarga"
 
        playlist_name = safe_filename(raw_name)
 
        subprocess.run(cmd, capture_output=True, text=True, timeout=600)
 
        mp3_files = sorted(Path(tmpdir).glob("*.mp3"))
        if not mp3_files:
            return jsonify({"status": "error", "message": "No se encontraron tracks"}), 500
 
        # Empaquetar en ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in mp3_files:
                zf.write(f, f.name)
        zip_buffer.seek(0)
        zip_data = zip_buffer.read()
 
        zip_filename = playlist_name + ".zip"
 
        return Response(
            zip_data,
            mimetype="application/zip",
            headers={
                "Content-Disposition": content_disposition(zip_filename),
                "Content-Length":      str(len(zip_data)),
                "X-Filename":          zip_filename,
            }
        )
 
 
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
