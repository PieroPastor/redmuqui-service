"""
Webhook que Spring Boot llama (de forma asíncrona, "fire and forget") justo
después de guardar un nuevo Archivo, para que se procese casi al instante.

Payload esperado (JSON), enviado por OcrNotificationService:
{
    "documentoId": 12,
    "archivoId": 45,
    "nombre": "foto.jpg",
    "url": "https://bucket.s3.region.amazonaws.com/documentos/12/uuid-foto.jpg",
    "extension": "jpg",
    "tipoContenido": "image/jpeg"
}

Seguridad: si OCR_WEBHOOK_TOKEN está configurado, se exige el header
`X-OCR-Webhook-Token` con ese mismo valor.

La respuesta se envía de inmediato (202) y el procesamiento (descarga + OCR)
ocurre en un hilo en segundo plano, para no bloquear a Spring Boot ni
arriesgar timeouts si el OCR tarda varios segundos.
"""

import json
import logging
import threading

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from processor.services import pipeline, s3_client
from processor.services.backend_client import SpringBackendClient

logger = logging.getLogger(__name__)

CAMPOS_REQUERIDOS = ["documentoId", "url", "nombre", "extension"]


@csrf_exempt
@require_POST
def archivo_nuevo(request):
    token_esperado = settings.OCR_WEBHOOK_TOKEN
    if token_esperado:
        token_recibido = request.headers.get("X-OCR-Webhook-Token", "")
        if token_recibido != token_esperado:
            return JsonResponse({"detail": "Token inválido."}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "JSON inválido."}, status=400)

    faltantes = [c for c in CAMPOS_REQUERIDOS if not data.get(c)]
    if faltantes:
        return JsonResponse({"detail": f"Faltan campos: {', '.join(faltantes)}"}, status=400)

    try:
        key = s3_client.extraer_key_de_url(data["url"])
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    documento_id = data["documentoId"]
    nombre_archivo = data["nombre"]
    extension = (data.get("extension") or "").lower()
    content_type = data.get("tipoContenido")

    logger.info("Webhook: archivo nuevo documento=%s key=%s", documento_id, key)

    def _procesar():
        backend = SpringBackendClient()
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
