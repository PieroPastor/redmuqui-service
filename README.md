# redmuqui-ocr-service

Servicio Django que convierte a **PDF con texto seleccionable (OCR)** las
imágenes y PDFs escaneados que se suben a los documentos de redmuqui, y sube
el resultado como un nuevo `Archivo` del **mismo `Documento`** (gracias a la
relación `@OneToMany`/`@ManyToOne` que ya existe en el backend — no se toca
ninguna entidad ni archivo de Spring Boot).

## Arquitectura (frontend llama directo + polling de respaldo)

1. **Llamada directa desde React (vía rápida)**: cuando alguien sube un
   archivo, el frontend llama primero a
   `POST /api/v1/documentos/{id}/archivos` (Spring Boot, como siempre). Si
   responde OK, el frontend hace, de forma "fire and forget" (sin esperar ni
   bloquear la UI), un segundo `POST` a
   `http://<alb-o-django>/ocr/api/procesar-archivo/` con
   `{ documentoId, archivoId, nombre, url, extension }`.

2. **Verificación en Django**: antes de procesar nada, Django consulta
   `GET /api/v1/documentos/{documentoId}/archivos` con su propio usuario de
   servicio y confirma que exista un `Archivo` con ese `id` y esa `url`. Si
   no coincide, responde `404` y no hace nada. Esto evita que cualquiera,
   llamando a este endpoint desde el navegador con datos arbitrarios, haga
   que Django adjunte archivos a documentos ajenos.

3. Si la verificación pasa, Django responde `202` de inmediato y procesa en
   un hilo en segundo plano (descarga de S3 + OCR + subida del resultado).

4. **Polling de respaldo**: el comando `poll_s3 --loop --interval 300`
   (cada 5 min por defecto) revisa el bucket S3 y procesa cualquier archivo
   que la llamada del frontend no haya alcanzado a notificar (el usuario
   cerró la pestaña, el `fetch` falló, etc.). Es **idempotente**: si ya fue
   procesado, lo ignora.

5. Ambos caminos comparten la misma lógica (`processor/services/pipeline.py`):
   - **Imagen** (jpg/png/tiff/bmp...) -> siempre se convierte.
   - **PDF sin texto seleccionable** (escaneado) -> se le agrega OCR.
   - **PDF con texto, DOCX, XLSX** -> se omite.
   - El resultado se sube con `POST /api/v1/documentos/{documentoId}/archivos`
     (mismo endpoint que usa React), con una `descripcion` que indica de qué
     archivo original viene.

## 1. Instalación de dependencias (en el EC2, vía apt)

```bash
sudo apt-get update
sudo apt-get install -y \
    ocrmypdf \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    python3-django \
    python3-django-cors-headers \
    python3-boto3 \
    python3-requests \
    python3-pypdf \
    python3-dotenv \
    gunicorn
```

Todo queda instalado a nivel de sistema (sin entorno virtual): `python3` y
`gunicorn` ya quedan en `/usr/bin/`, listos para usar.

## 2. Configuración

```bash
cp .env.example .env
# edita .env con tus valores reales
```

Django carga `.env` automáticamente al arrancar (con `python-dotenv`), tanto
para `python manage.py ...` como para `gunicorn`. No necesitas hacer
`export` manual ni `source`.

- **S3**: si el EC2 tiene el `LabRole` de AWS Academy, deja
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` vacíos (boto3 usa el rol
  automáticamente). Asegúrate de que el rol tenga `s3:GetObject` y
  `s3:ListBucket` sobre el bucket.
- **Backend Spring Boot**: crea un usuario "de servicio" en la BD del
  backend con un rol que tenga `DOCUMENTOS_UPDATE` (ej. `TECNICO`), y pon sus
  credenciales en `SPRING_SERVICE_EMAIL` / `SPRING_SERVICE_PASSWORD`.
- **CORS**: define `OCR_CORS_ALLOWED_ORIGINS` con la URL del frontend
  (ej. `https://redmuqui.miuniversidad.edu` o `http://localhost:3000`), para
  que el navegador pueda llamar a este servicio.

## 3. Migraciones (solo la primera vez)

```bash
python manage.py migrate
```

## 4. Ejecución

### a) Servidor del endpoint (vía rápida, llamado por el frontend)

```bash
gunicorn redmuqui_ocr.wsgi:application --bind 0.0.0.0:8001 --workers 2
```

En el frontend (Next.js), configura:

```bash
NEXT_PUBLIC_OCR_API_URL=   (vacio si Django esta enrutado via ALB en /ocr/*, o http://<ip-django>:8001 para pruebas directas)
```

Si esa variable se deja vacía, la llamada se hace al mismo origen
(`/ocr/api/procesar-archivo/`), que es lo correcto cuando el ALB enruta
`/ocr/*` hacia esta EC2. Si Django no está desplegado o no responde, el
`fetch` simplemente falla (se ignora) y el polling de respaldo procesará el
archivo más tarde.

### b) Polling de respaldo

```bash
python manage.py poll_s3 --loop --interval 300
```

### Como servicios systemd (recomendado)

`/etc/systemd/system/redmuqui-ocr-api.service`:

```ini
[Unit]
Description=redmuqui OCR API (llamada desde el frontend)
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/redmuqui-ocr-service
ExecStart=/usr/bin/gunicorn redmuqui_ocr.wsgi:application --bind 0.0.0.0:8001 --workers 2
User=ubuntu
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
ExecStart=/usr/bin/python3 manage.py poll_s3 --loop
User=ubuntu
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now redmuqui-ocr-api.service redmuqui-ocr-poller.service
sudo journalctl -u redmuqui-ocr-api.service -u redmuqui-ocr-poller.service -f
```

### Exponer este servicio vía el ALB (path-based routing)

Para que `/ocr/*` del ALB llegue a esta EC2:

1. **Security Group de esta EC2**: regla de entrada `TCP 8001`, origen =
   Security Group del ALB (no un CIDR).
2. **Target Group nuevo** (ej. `redmuqui-ocr`): tipo Instancia, protocolo
   `HTTP`, puerto `8001`, health check path `/health`. Registra esta EC2
   ahí (y solo ahí — no la mezcles con el Target Group del backend Spring
   Boot, o el ALB repartirá tráfico al azar entre ambos).
3. **Regla del listener** del ALB: *"si el path empieza con `/ocr/*` ->
   forward a `redmuqui-ocr`"*, con prioridad mayor que la regla por defecto.

Verifica primero localmente:
- `curl http://localhost:8001/health` -> `OK` (usado por el health check del Target Group, sin prefijo).
- `curl -X POST http://localhost:8001/ocr/api/procesar-archivo/ -d '{}' -H 'Content-Type: application/json'` -> debería responder `400` (faltan campos), confirmando que el endpoint con prefijo `/ocr/` también responde.

## Idempotencia / cómo evita duplicados

- `processor.services.pipeline.procesar_archivo` revisa primero
  `ArchivoProcesado` por `s3_key`: si ya existe (sin importar el estado), no
  hace nada. Esto cubre el caso "el frontend ya lo notificó y luego el
  poller de respaldo también lo revisa".
- Las imágenes se convierten a `*_ocr.pdf` (cambia de extensión) y los PDFs
  escaneados quedan con texto seleccionable: si por algún motivo se
  reanalizaran, ya no calificarían para OCR de nuevo.

## Notas de seguridad

- El endpoint `/ocr/api/procesar-archivo/` es público (lo llama el navegador), pero
  Django **siempre verifica contra el backend** que el `archivoId` + `url`
  recibidos correspondan realmente a un archivo de ese `documentoId` antes de
  hacer nada. Una solicitud con datos inventados recibe `404`.
- Aun así, este endpoint puede recibir tráfico de internet en general. Si
  quieres limitarlo más, considera ponerlo detrás de un reverse proxy
  (nginx) con rate limiting, o restringir el Security Group del EC2 a rangos
  de IP conocidos.
