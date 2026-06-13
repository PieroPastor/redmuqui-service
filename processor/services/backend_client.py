"""
Cliente para hablar con el backend de Spring Boot.

Hace login contra POST /api/v1/auth/login con un usuario "de servicio"
(creado en la base de datos del backend, con un rol que tenga el permiso
DOCUMENTOS_UPDATE, por ejemplo TECNICO), guarda el access token en memoria,
y lo usa para subir el PDF resultante con
POST /api/v1/documentos/{documentoId}/archivos — el mismo endpoint
multipart que usa el frontend de React.

Como Documento tiene una relación @OneToMany con Archivo (mappedBy =
"documento"), este nuevo Archivo queda automáticamente ligado al MISMO
Documento que el archivo original: no es "un archivo aparte y
desconectado", es una versión/anexo más del mismo documento.
"""

import logging
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BackendAuthError(Exception):
    pass


class SpringBackendClient:
    def __init__(self):
        self._access_token: str | None = None

    def _login(self) -> None:
        url = f"{settings.SPRING_BACKEND_URL}/api/v1/auth/login"
        logger.info("Autenticando contra %s como %s", url, settings.SPRING_SERVICE_EMAIL)

        resp = requests.post(
            url,
            json={
                "email": settings.SPRING_SERVICE_EMAIL,
                "contrasenha": settings.SPRING_SERVICE_PASSWORD,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise BackendAuthError(
                f"Login fallido ({resp.status_code}): {resp.text}"
            )

        data = resp.json()
        self._access_token = data["accessToken"]

    def _headers(self) -> dict:
        if not self._access_token:
            self._login()
        return {"Authorization": f"Bearer {self._access_token}"}

    def subir_pdf_resultado(self, documento_id: int, pdf_path: Path, descripcion: str) -> dict:
        """
        Sube `pdf_path` como un nuevo Archivo del documento `documento_id`,
        igual que lo haría el frontend al adjuntar un archivo.
        Devuelve el ArchivoDTO creado (incluye su id).
        """
        url = f"{settings.SPRING_BACKEND_URL}/api/v1/documentos/{documento_id}/archivos"

        for intento in range(2):
            headers = self._headers()

            with open(pdf_path, "rb") as f:
                files = {"archivo": (pdf_path.name, f, "application/pdf")}
                data = {"descripcion": descripcion}
                resp = requests.post(url, headers=headers, files=files, data=data, timeout=180)

            if resp.status_code == 401 and intento == 0:
                logger.info("Token expirado/inválido, reautenticando...")
                self._access_token = None
                continue

            resp.raise_for_status()
            return resp.json()

        resp.raise_for_status()
        return resp.json()
