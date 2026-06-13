"""
Polling de RESPALDO. El procesamiento "rápido" ocurre vía webhook
(processor/views.py), que Spring Boot llama justo después de subir un
archivo. Este comando existe para procesar cualquier archivo que el webhook
no haya alcanzado a notificar (Django caído, error de red, etc.).

Uso:
    python manage.py poll_s3                       # corre un ciclo y termina
    python manage.py poll_s3 --loop                # bucle, cada POLL_INTERVAL_SECONDS
    python manage.py poll_s3 --loop --interval 300 # cada 5 minutos

Flujo de cada ciclo:
1. Lista los objetos de S3 bajo `documentos/` (S3_DOCUMENTOS_PREFIX).
2. Salta los que ya estén registrados en ArchivoProcesado (esto incluye los
   que el webhook ya procesó).
3. Para cada objeto nuevo, delega en processor.services.pipeline.procesar_archivo:
   decide si necesita OCR y, si es así, sube el PDF convertido como un nuevo
   Archivo del mismo documento.
"""

import logging
import re
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from processor.models import ArchivoProcesado
from processor.services import pipeline, s3_client
from processor.services.backend_client import SpringBackendClient

logger = logging.getLogger(__name__)

# Coincide con el patrón usado por S3StorageService en el backend:
# "documentos/" + documentoId + "/" + uuid + "-" + nombreSeguro
PATRON_KEY = re.compile(r"^documentos/(?P<documento_id>\d+)/")


class Command(BaseCommand):
    help = "Polling de respaldo: procesa archivos de S3 que el webhook no haya alcanzado a notificar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Ejecutar en bucle infinito, esperando POLL_INTERVAL_SECONDS entre ciclos.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Segundos entre ciclos (sobreescribe POLL_INTERVAL_SECONDS).",
        )

    def handle(self, *args, **options):
        intervalo = options["interval"] or settings.POLL_INTERVAL_SECONDS
        backend = SpringBackendClient()

        if options["loop"]:
            self.stdout.write(self.style.SUCCESS(f"Iniciando poller de respaldo (cada {intervalo}s)..."))
            while True:
                try:
                    self.ejecutar_ciclo(backend)
                except Exception:
                    logger.exception("Error inesperado en el ciclo del poller")
                time.sleep(intervalo)
        else:
            self.ejecutar_ciclo(backend)

    # ------------------------------------------------------------------

    def ejecutar_ciclo(self, backend: SpringBackendClient) -> None:
        logger.info("Revisando s3://%s/%s ...", settings.AWS_S3_BUCKET, settings.S3_DOCUMENTOS_PREFIX)

        procesados_ya = set(
            ArchivoProcesado.objects.values_list("s3_key", flat=True)
        )

        nuevos = 0
        for obj in s3_client.listar_objetos(settings.S3_DOCUMENTOS_PREFIX):
            key = obj["Key"]
            if key in procesados_ya:
                continue

            nuevos += 1
            self.procesar_objeto(key, backend)

        if nuevos == 0:
            logger.info("No hay archivos nuevos.")

    def procesar_objeto(self, key: str, backend: SpringBackendClient) -> None:
        match = PATRON_KEY.match(key)
        if not match:
            logger.warning("Key con formato inesperado, se omite: %s", key)
            ArchivoProcesado.objects.create(
                s3_key=key,
                estado=ArchivoProcesado.OMITIDO,
                detalle="La key no coincide con el patrón documentos/{id}/...",
            )
            return

        documento_id = int(match.group("documento_id"))
        nombre_archivo = key.rsplit("/", 1)[-1]
        extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
        content_type = s3_client.obtener_content_type(key)

        pipeline.procesar_archivo(
            documento_id=documento_id,
            key=key,
            nombre_archivo=nombre_archivo,
            extension=extension,
            content_type=content_type,
            backend=backend,
        )
