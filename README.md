# redmuqui-ocr-service

Servicio Django que convierte a **PDF con texto seleccionable (OCR)** las
imágenes y PDFs escaneados que se suben a los documentos de redmuqui, y sube
el resultado como un nuevo `Archivo` del **mismo `Documento`** (gracias a la
relación `@OneToMany`/`@ManyToOne` que ya existe en el backend — no se toca
ninguna entidad de Spring Boot).

## Arquitectura (híbrida: webhook + polling de respaldo)

1. **Webhook (vía rápida)**: cuando alguien sube un archivo desde React,
   Spring Boot lo guarda en S3 y en la BD, y de forma **asíncrona** (sin
   bloquear la respuesta al usuario) hace `POST` a
   `http://<django>/webhook/archivo-nuevo/` con los datos del archivo
   (`OcrNotificationService`). Django responde `202` de inmediato y procesa
   en un hilo en segundo plano.

2. **Polling de respaldo**: el comando `poll_s3 --loop --interval 300`
   (cada 5 min por defecto) revisa el bucket S3 y procesa cualquier archivo
   que el webhook no haya llegado a notificar (si Django estaba caído, error
   de red, etc.). Es **idempotente**: si el webhook ya procesó la key, el
   poller la ve registrada y la ignora.

3. Ambos caminos comparten la misma lógica (`processor/services/pipeline.py`):
   - **Imagen** (jpg/png/tiff/bmp...) -> siempre se convierte.
   - **PDF sin texto seleccionable** (escaneado) -> se le agrega OCR.
   - **PDF con texto, DOCX, XLSX** -> se omite.
   - El resultado se sube con `POST /api/v1/documentos/{documentoId}/archivos`
     (mismo endpoint que usa React), con una `descripcion` que indica de qué
     archivo original viene.

## 1. Requisitos del sistema (en el EC2)

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv \
    tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
    ghostscript qpdf unpaper
```

## 2. Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configuración

```bash
cp .env.example .env
# edita .env con tus valores reales
export $(grep -v '^#' .env | xargs)
```

- **S3**: si el EC2 tiene el `LabRole` de AWS Academy, deja
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` vacíos (boto3 usa el rol
  automáticamente). Asegúrate de que el rol tenga `s3:GetObject` y
  `s3:ListBucket` sobre el bucket.
- **Backend Spring Boot**: crea un usuario "de servicio" en la BD del
  backend con un rol que tenga `DOCUMENTOS_UPDATE` (ej. `TECNICO`), y pon sus
  credenciales en `SPRING_SERVICE_EMAIL` / `SPRING_SERVICE_PASSWORD`.
- **Webhook**: define `OCR_WEBHOOK_TOKEN` (un string aleatorio cualquiera) y
  pon ese mismo valor en el backend como `OCR_WEBHOOK_TOKEN` (env var que lee
  `ocr.webhook.token` en `application.yml`).

## 4. Migraciones (solo la primera vez)

```bash
python manage.py migrate
```

## 5. Ejecución

### a) Servidor del webhook (vía rápida)

```bash
gunicorn redmuqui_ocr.wsgi:application --bind 0.0.0.0:8001 --workers 2
```

Y en el backend (Spring Boot), configura las variables de entorno:

```bash
OCR_WEBHOOK_URL=http://<ip-o-host-del-ec2-django>:8001/webhook/archivo-nuevo/
OCR_WEBHOOK_TOKEN=<el-mismo-token-que-puso-django>
```

Si `OCR_WEBHOOK_URL` no se define en el backend, simplemente no se envía
notificación y todo el trabajo lo hace el polling de respaldo (más lento,
pero funciona igual).

### b) Polling de respaldo

```bash
python manage.py poll_s3 --loop --interval 300
```

### Como servicios systemd (recomendado)

`/etc/systemd/system/redmuqui-ocr-webhook.service`:

```ini
[Unit]
Description=redmuqui OCR webhook
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/redmuqui-ocr-service
EnvironmentFile=/home/ubuntu/redmuqui-ocr-service/.env
ExecStart=/home/ubuntu/redmuqui-ocr-service/.venv/bin/gunicorn redmuqui_ocr.wsgi:application --bind 0.0.0.0:8001 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/redmuqui-ocr-poller.service`:

```ini
[Unit]
Description=redmuqui OCR poller de respaldo
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/redmuqui-ocr-service
EnvironmentFile=/home/ubuntu/redmuqui-ocr-service/.env
ExecStart=/home/ubuntu/redmuqui-ocr-service/.venv/bin/python manage.py poll_s3 --loop
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now redmuqui-ocr-webhook.service redmuqui-ocr-poller.service
sudo journalctl -u redmuqui-ocr-webhook.service -u redmuqui-ocr-poller.service -f
```

Recuerda abrir el puerto 8001 (Security Group) entre el EC2 del backend y el
EC2 de Django.

## Idempotencia / cómo evita duplicados

- `processor.services.pipeline.procesar_archivo` revisa primero
  `ArchivoProcesado` por `s3_key`: si ya existe (sin importar el estado), no
  hace nada. Esto cubre el caso "webhook ya lo procesó y luego el poller de
  respaldo también lo revisa".
- Las imágenes se convierten a `*_ocr.pdf` (cambia de extensión) y los PDFs
  escaneados quedan con texto seleccionable: si por algún motivo se
  reanalizaran, ya no calificarían para OCR de nuevo.
