from django.db import models


class ArchivoProcesado(models.Model):
    """
    Registro local (SQLite) de cada objeto de S3 que el poller ya revisó,
    para no volver a descargarlo/analizarlo en la siguiente vuelta.
    """

    PROCESADO = "PROCESADO"
    OMITIDO = "OMITIDO"
    ERROR = "ERROR"

    ESTADOS = [
        (PROCESADO, "Convertido a PDF editable y subido al backend"),
        (OMITIDO, "No requería conversión (ya es PDF/DOCX/XLSX editable)"),
        (ERROR, "Ocurrió un error al procesar el archivo"),
    ]

    s3_key = models.CharField(max_length=1024, unique=True)
    documento_id = models.BigIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS)
    detalle = models.TextField(blank=True, default="")
    archivo_resultado_id = models.BigIntegerField(null=True, blank=True)
    fecha_procesado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "archivos_procesados"
        ordering = ["-fecha_procesado"]

    def __str__(self) -> str:
        return f"{self.s3_key} -> {self.estado}"
