"""
- /health                      -> sin prefijo (para el health check del Target Group,
                                   que el ALB consulta directo a la instancia, sin pasar
                                   por las reglas de path del listener).
- /ocr/api/procesar-archivo/   -> con prefijo /ocr/ (para la regla de path-based
                                   routing del ALB: "/ocr/* -> Target Group de Django").
"""

from django.urls import path

from processor import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("ocr/api/procesar-archivo/", views.procesar_archivo, name="procesar_archivo"),
]
