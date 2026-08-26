FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Estáticos recolectados y comprimidos en tiempo de build: la imagen sale lista
# y el arranque del contenedor no depende de nada externo. Las variables son
# ficticias porque settings las exige al importar, pero collectstatic no toca
# la base de datos ni usa la SECRET_KEY para nada persistente.
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=solo-para-collectstatic-no-se-usa-en-runtime \
    POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
    python manage.py collectstatic --noinput --clear

# Usuario sin privilegios: si comprometen la app, no obtienen root en el contenedor
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# El ingress de Container Apps termina TLS y habla HTTP con este puerto.
# GUNICORN_WORKERS lo inyecta Terraform; 2 es el valor prudente para un B1ms.
CMD ["sh", "-c", "gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-2} \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -"]
