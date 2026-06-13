"""
Cliente delgado sobre boto3 para listar, inspeccionar y descargar objetos
del bucket donde el backend de Spring Boot guarda los archivos.

En el EC2 de AWS Academy, si la instancia tiene asociado el LabRole,
boto3 obtiene las credenciales automáticamente del metadata service:
no es necesario configurar AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
"""

import logging
from pathlib import Path
from typing import Iterator, Optional

import boto3
from django.conf import settings

logger = logging.getLogger(__name__)

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        kwargs = {"region_name": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
            if settings.AWS_SESSION_TOKEN:
                kwargs["aws_session_token"] = settings.AWS_SESSION_TOKEN
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def listar_objetos(prefix: str) -> Iterator[dict]:
    """Itera todos los objetos del bucket bajo el prefijo dado (con paginación)."""
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=settings.AWS_S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            # Ignorar "carpetas" (objetos que terminan en "/")
            if obj["Key"].endswith("/"):
                continue
            yield obj


def obtener_content_type(key: str) -> Optional[str]:
    s3 = get_s3_client()
    head = s3.head_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
    return head.get("ContentType")


def descargar_objeto(key: str, destino: Path) -> None:
    s3 = get_s3_client()
    destino.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Descargando s3://%s/%s -> %s", settings.AWS_S3_BUCKET, key, destino)
    s3.download_file(settings.AWS_S3_BUCKET, key, str(destino))


def extraer_key_de_url(url: str) -> str:
    """
    Convierte una URL del estilo
    https://{bucket}.s3.{region}.amazonaws.com/{key}
    (la que genera S3StorageService.construirUrl en el backend) en su `key`.
    """
    marcador = ".amazonaws.com/"
    idx = url.find(marcador)
    if idx == -1:
        raise ValueError(f"La URL no parece ser de S3: {url}")
    return url[idx + len(marcador):]
