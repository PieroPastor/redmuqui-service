"""
Único endpoint expuesto: el webhook que Spring Boot llama tras subir un
archivo. El resto del servicio funciona vía management commands
(`poll_s3`), no como una API completa.
"""

from django.urls import path

from processor import views

urlpatterns = [
    path("webhook/archivo-nuevo/", views.archivo_nuevo, name="archivo_nuevo"),
]
