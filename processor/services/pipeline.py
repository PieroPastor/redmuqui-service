"""
Lógica central de procesamiento, compartida por:
- la vista del webhook (processor/views.py), que la llama casi al instante
  cuando Spring Boot notifica un archivo nuevo, y
- el comando `poll_s3` (polling de respaldo), que la llama para cualquier
  archivo que el webhook no haya llegado a procesar.

Es idempotente: si `key` ya está registrada en ArchivoProcesado (sin
importar el estado), no hace nada. Así, si el webhook ya procesó un
archivo, el polling de respaldo lo ve y lo ignora.
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from django.conf import settings

from processor.models import ArchivoProcesado
from processor.services import converter, s3_client
from processor.services.backend_client import SpringBackendClient

logger = logging.getLogger(__name__)


def ya_procesado(key: str) -> bool:
    return ArchivoProcesado.objects.filter(s3_key=key).exists()


def procesar_archivo(
    documento_id: int,
    key: str,
    nombre_archivo: str,
    extension: str,
    content_type: Optional[str],
    backend: SpringBackendClient,
) -> None:
    """
    Descarga `key` desde S3, decide si necesita OCR y, si es así, genera un
    PDF con texto seleccionable y lo sube como un nuevo Archivo del MISMO
    documento (vía SpringBackendClient.subir_pdf_resultado). Registra el
    resultado en ArchivoProcesado.
    """
    if ya_procesado(key):
        logger.info("Ya procesado anteriormente, se omite: %s", key)
        return

    directorio_tmp = Path(settings.WORKDIR) / str(uuid.uuid4())
    ruta_local = directorio_tmp / nombre_archivo

    try:
        s3_client.descargar_objeto(key, ruta_local)

        if not converter.necesita_conversion(ruta_local, extension, content_type):
            logger.info("No requiere OCR, se omite: %s", key)
            ArchivoProcesado.objects.create(
                s3_key=key,
                documento_id=documento_id,
                estado=ArchivoProcesado.OMITIDO,
                detalle=f"extension={extension}, content_type={content_type}",
            )
            return

        logger.info("Convirtiendo a PDF editable: %s", key)
        ruta_pdf = directorio_tmp / "resultado.pdf"
        converter.convertir_a_pdf_editable(ruta_local, ruta_pdf, extension)

        nombre_pdf = Path(nombre_archivo).stem + "_ocr.pdf"
        ruta_pdf_final = directorio_tmp / nombre_pdf
        shutil.move(str(ruta_pdf), str(ruta_pdf_final))

        descripcion = (
            f"Versión OCR (PDF con texto seleccionable) generada automáticamente "
            f"a partir de: {nombre_archivo}"
        )

        resultado = backend.subir_pdf_resultado(
            documento_id=documento_id,
            pdf_path=ruta_pdf_final,
            descripcion=descripcion,
        )

        logger.info(
            "Subido al backend como Archivo id=%s del mismo documento %s",
            resultado.get("id"), documento_id,
        )

        ArchivoProcesado.objects.create(
            s3_key=key,
            documento_id=documento_id,
            estado=ArchivoProcesado.PROCESADO,
            archivo_resultado_id=resultado.get("id"),
            detalle=f"Archivo generado: {nombre_pdf}",
        )

    except Exception as exc:
        logger.exception("Error procesando %s", key)
        ArchivoProcesado.objects.create(
            s3_key=key,
            documento_id=documento_id,
            estado=ArchivoProcesado.ERROR,
            detalle=str(exc)[:2000],
        )
    finally:
        if directorio_tmp.exists():
            shutil.rmtree(directorio_tmp, ignore_errors=True)
