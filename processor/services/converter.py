"""
Funciones para decidir si un archivo necesita convertirse a PDF editable,
y para hacer la conversión con `ocrmypdf` (requiere tener instalados a nivel
de sistema: tesseract-ocr, ghostscript y qpdf).

Reglas:
- Imágenes (jpg, jpeg, png, tif, tiff, bmp): siempre se convierten a PDF con
  capa de texto (OCR).
- PDF: si ya tiene texto extraíble, se asume "editable" y se omite. Si no
  tiene texto (escaneado), se le agrega una capa OCR.
- DOCX / XLSX: ya son editables, se omiten.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from django.conf import settings
from pypdf import PdfReader

logger = logging.getLogger(__name__)

EXTENSIONES_IMAGEN = {"jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp"}

CONTENT_TYPES_IMAGEN = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/webp",
}


def es_imagen(extension: str, content_type: Optional[str]) -> bool:
    extension = (extension or "").lower().lstrip(".")
    if extension in EXTENSIONES_IMAGEN:
        return True
    if content_type and content_type.lower() in CONTENT_TYPES_IMAGEN:
        return True
    return False


def es_pdf(extension: str, content_type: Optional[str]) -> bool:
    extension = (extension or "").lower().lstrip(".")
    return extension == "pdf" or content_type == "application/pdf"


def pdf_tiene_texto(path: Path, min_caracteres: int = 20) -> bool:
    """
    Heurística simple: si al extraer texto de las páginas del PDF se obtienen
    al menos `min_caracteres` caracteres (sin contar espacios), se asume que
    el PDF ya tiene una capa de texto y por tanto ya es "editable"/buscable.
    """
    try:
        lector = PdfReader(str(path))
        total = 0
        for pagina in lector.pages:
            texto = (pagina.extract_text() or "").strip()
            total += len(texto)
            if total >= min_caracteres:
                return True
        return total >= min_caracteres
    except Exception:
        logger.exception("No se pudo leer %s; se asumirá que necesita OCR", path)
        return False


def necesita_conversion(path: Path, extension: str, content_type: Optional[str]) -> bool:
    """Determina si el archivo descargado necesita pasar por OCR."""
    if es_imagen(extension, content_type):
        return True

    if es_pdf(extension, content_type):
        return not pdf_tiene_texto(path)

    # docx, xlsx u otros: no se procesan.
    return False


def convertir_a_pdf_editable(path: Path, output_path: Path, extension: str) -> None:
    """
    Genera `output_path` como un PDF con capa de texto (buscable/editable)
    usando ocrmypdf.

    - Si la entrada es una imagen, ocrmypdf la convierte directamente a PDF
      con OCR (se indica --image-dpi porque muchas imágenes no traen DPI).
    - Si la entrada es un PDF escaneado, se le agrega la capa OCR
      (--skip-text deja intactas las páginas que ya tuvieran texto).
    """
    extension = (extension or "").lower().lstrip(".")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    comando = ["ocrmypdf", "--skip-text", "-l", settings.OCR_LANGUAGES]

    if extension != "pdf":
        comando += ["--image-dpi", "300"]

    comando += [str(path), str(output_path)]

    logger.info("Ejecutando: %s", " ".join(comando))
    resultado = subprocess.run(comando, capture_output=True, text=True)

    if resultado.returncode != 0:
        raise RuntimeError(
            f"ocrmypdf terminó con código {resultado.returncode}.\n"
            f"stdout: {resultado.stdout}\nstderr: {resultado.stderr}"
        )

    if resultado.stderr:
        logger.debug("ocrmypdf stderr: %s", resultado.stderr)
