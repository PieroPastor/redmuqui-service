"""
Endpoint que el FRONTEND (React/Next.js) llama directamente, justo después
de que `POST /api/v1/documentos/{id}/archivos` responde exitosamente, para
que Django procese el archivo casi al instante (en vez de esperar al
siguiente ciclo del polling de respaldo).

Payload esperado (JSON), enviado desde el navegador:
{
    "documentoId": 12,
    "archivoId": 45,
    "nombre": "foto.jpg",
    "url": "https://bucket.s3.region.amazonaws.com/documentos/12/uuid-foto.jpg",
    "extension": "jpg"
}

Seguridad: como cualquiera podría llamar a este endpoint desde el navegador
con datos arbitrarios, ANTES de procesar nada Django verifica (con su propio
usuario de servicio) que el documento `documentoId` realmente tenga un
Archivo `archivoId` con esa `url`. Si no, responde 404 y no hace nada.

La respuesta se envía apenas termina esa verificación (rápida) y el
procesamiento pesado (descarga + OCR + subida del resultado) ocurre en un
hilo en segundo plano, para no dejar al navegador esperando.
"""

import json
import logging
import threading

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from processor.services import pipeline, s3_client
from processor.services.backend_client import SpringBackendClient

logger = logging.getLogger(__name__)

CAMPOS_REQUERIDOS = ["documentoId", "archivoId", "url", "nombre", "extension"]


@require_GET
def health(request):
    """
    Endpoint simple para el health check del Target Group del ALB.
    No verifica S3 ni el backend (para que sea rápido y no falle por causas
    ajenas a "¿está vivo el proceso de Django?"); responde 200 "OK" siempre
    que gunicorn esté arriba.
    """
    return HttpResponse("OK")


@csrf_exempt
@require_POST
def procesar_archivo(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "JSON inválido."}, status=400)

    faltantes = [c for c in CAMPOS_REQUERIDOS if data.get(c) in (None, "")]
    if faltantes:
        return JsonResponse({"detail": f"Faltan campos: {', '.join(faltantes)}"}, status=400)

    documento_id = data["documentoId"]
    archivo_id = data["archivoId"]
    nombre_archivo = data["nombre"]
    url_s3 = data["url"]
    extension = (data.get("extension") or "").lower()

    try:
        key = s3_client.extraer_key_de_url(url_s3)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    auth = request.headers.get("Authorization")

    backend = SpringBackendClient(
        authorization=auth
    )

    # Verificación: el documento debe tener realmente ese archivo con esa URL.
    try:
        existe = backend.archivo_existe(documento_id, archivo_id, url_s3)
    except Exception:
        logger.exception("No se pudo verificar el archivo contra el backend")
        return JsonResponse({"detail": "No se pudo verificar el archivo contra el backend."}, status=502)

    if not existe:
        logger.warning(
            "Solicitud rechazada: no existe archivo id=%s con esa url en documento %s",
            archivo_id, documento_id,
        )
        return JsonResponse({"detail": "El archivo indicado no existe en ese documento."}, status=404)

    if pipeline.ya_procesado(key):
        return JsonResponse({"detail": "Este archivo ya fue procesado anteriormente."}, status=200)

    logger.info("Procesando archivo solicitado por el frontend: documento=%s key=%s", documento_id, key)

    def _procesar():
        content_type = None
        try:
            content_type = s3_client.obtener_content_type(key)
        except Exception:
            logger.exception("No se pudo obtener el content-type de %s", key)

        pipeline.procesar_archivo(
            documento_id=documento_id,
            key=key,
            nombre_archivo=nombre_archivo,
            extension=extension,
            content_type=content_type,
            backend=backend,
        )

    threading.Thread(target=_procesar, daemon=True).start()

    return JsonResponse({"detail": "Recibido, procesando en segundo plano."}, status=202)
