import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "redmuqui_ocr.settings")

application = get_wsgi_application()
