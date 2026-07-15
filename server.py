import os
import re
import uuid
import shutil
import asyncio
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="YouTube Audio Extractor API")

# Permite peticiones CORS para que funcione en hosts estáticos externos como Neocities.org
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "/tmp/yt_downloads_m4a"
os.makedirs(TEMP_DIR, exist_ok=True)

class DownloadRequest(BaseModel):
    url: HttpUrl

def sanitize_filename(name: str) -> str:
    # Eliminar caracteres inválidos para nombres de archivos
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = " ".join(clean.split())
    return clean if clean else "audio_extraido"

def cleanup_file(filepath: str):
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Error al eliminar archivo temporal: {e}")

@app.get("/", response_class=HTMLResponse)
@app.get("/musica", response_class=HTMLResponse)
async def get_musica_interface():
    # Servir la interfaz música.html de forma directa
    html_path = os.path.join(os.path.dirname(__file__), "musica.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Archivo musica.html no encontrado en el servidor.</h1>", status_code=404)

@app.post("/api/v1/download-m4a")
async def download_m4a(request: DownloadRequest, background_tasks: BackgroundTasks):
    video_id = str(uuid.uuid4())
    output_template = os.path.join(TEMP_DIR, f"{video_id}.%(ext)s")
    final_file_path = os.path.join(TEMP_DIR, f"{video_id}.m4a")

    # Detectar el ejecutable de yt-dlp (local venv vs global)
    venv_yt_dlp = os.path.join(os.path.dirname(__file__), ".venv", "bin", "yt-dlp")
    yt_dlp_bin = venv_yt_dlp if os.path.exists(venv_yt_dlp) else "yt-dlp"

    # Verificar si ffmpeg está instalado en el sistema
    has_ffmpeg = shutil.which("ffmpeg") is not None

    # 1. Obtener el título del video para nombrar el archivo de salida
    video_title = "audio_descargado"
    try:
        title_process = await asyncio.create_subprocess_exec(
            yt_dlp_bin,
            "--print", "title",
            str(request.url),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_title, _ = await title_process.communicate()
        if title_process.returncode == 0 and stdout_title:
            video_title = stdout_title.decode().strip()
    except Exception as e:
        print(f"Advertencia: No se pudo obtener el título: {e}")

    # 2. Descargar el flujo M4A nativo
    command = [
        yt_dlp_bin,
        "-f", "bestaudio[ext=m4a]/best[ext=m4a]",  # Selecciona audio M4A nativo sin recodificar
        "-o", output_template
    ]

    # La incrustación de carátula y metadatos requiere ffmpeg y atomicparsley
    if has_ffmpeg:
        command.extend(["--embed-metadata", "--embed-thumbnail"])
    else:
        print("Advertencia: ffmpeg no detectado. Se omitirá la incrustación de metadatos/portada.")

    command.append(str(request.url))

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            # Si el error fue por incrustación de metadatos (ej: falta atomicparsley) reintentamos sin metadatos
            if "ffmpeg" in error_msg or "AtomicParsley" in error_msg:
                print("Reintentando descarga sin metadatos debido a error de herramientas de sistema...")
                fallback_command = [
                    yt_dlp_bin,
                    "-f", "bestaudio[ext=m4a]/best[ext=m4a]",
                    "-o", output_template,
                    str(request.url)
                ]
                fallback_process = await asyncio.create_subprocess_exec(
                    *fallback_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await fallback_process.communicate()
                if fallback_process.returncode != 0:
                    raise HTTPException(status_code=500, detail="Fallo en la descarga de audio de respaldo.")
            else:
                raise HTTPException(status_code=500, detail=f"Error en yt-dlp: {error_msg}")
            
        if not os.path.exists(final_file_path):
            raise HTTPException(
                status_code=500, 
                detail="No se encontró el archivo de salida M4A en la ruta temporal."
            )

        # Programamos la eliminación del archivo temporal después del envío de la respuesta
        background_tasks.add_task(cleanup_file, final_file_path)

        # Nombre sanitizado para la descarga
        download_name = f"{sanitize_filename(video_title)}.m4a"

        return FileResponse(
            path=final_file_path,
            media_type="audio/mp4",
            filename=download_name
        )

    except HTTPException as http_err:
        raise http_err
    except Exception as err:
        if os.path.exists(final_file_path):
            os.remove(final_file_path)
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(err)}")
