# Plataforma de Gestión de Asignación de Recursos

Aplicación web interna para asignar ingenieros a proyectos, calcular fechas de fin considerando feriados Colombia, y controlar la ocupación del equipo.

## Requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) >= 4.x
- Git

> **No necesitás instalar Python ni PostgreSQL localmente.** Todo corre dentro de Docker.

## Inicio rápido (4 pasos)

```bash
# 1. Clonar
git clone https://github.com/jobrrerac/plataforma-gestion.git
cd plataforma-gestion

# 2. Crear archivo de entorno
cp .env.example .env
# Editar .env: cambiar DJANGO_SECRET_KEY y POSTGRES_PASSWORD

# 3. Levantar (primera vez construye la imagen, tarda ~2 min)
docker compose up -d --build

# 4. Primera vez: migraciones + superusuario admin
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Abrir en el navegador:
- **Dashboard de ocupación**: http://localhost:8000/dashboard/
- **Admin Django** (cargar datos): http://localhost:8000/admin/
- **API REST**: http://localhost:8000/api/

## Comandos frecuentes

| Acción | Comando |
|---|---|
| Levantar | `docker compose up -d` |
| Detener | `docker compose down` |
| Reconstruir imagen | `docker compose build` |
| Migraciones | `docker compose exec web python manage.py migrate` |
| Nueva migración | `docker compose exec web python manage.py makemigrations` |
| Crear admin | `docker compose exec web python manage.py createsuperuser` |
| Correr tests | `docker compose exec web python manage.py test --verbosity=2` |
| Shell Django | `docker compose exec web python manage.py shell` |
| Ver logs | `docker compose logs -f web` |
| Borrar todo (DB incluida) | `docker compose down -v` |

## Estructura del proyecto

```
.
├── CLAUDE.md                  ← Reglas y convenciones del proyecto
├── docs/SPEC.md               ← Especificación completa del sistema
├── docker-compose.yml         ← Servicios: web (Django) + db (PostgreSQL)
├── .env.example               ← Plantilla de variables de entorno
├── .github/workflows/ci.yml   ← CI con GitHub Actions
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── config/                ← Settings (base + local), URLs, WSGI
    ├── templates/dashboard/   ← Template del dashboard visual
    └── apps/
        ├── core/              ← Modelos: Recurso, Proyecto
        ├── calendar_engine/   ← Feriados Colombia, días no laborables, indisponibilidades
        ├── assignments/       ← Asignación, cálculo fecha fin, capacidad, auditoría
        ├── accounts/          ← RBAC (grupos: Admin, PM, Ingeniero)
        └── dashboard/         ← API + vista visual de ocupación / bench
```

## API — Endpoints principales

```
GET  /api/recursos/                    Lista ingenieros
POST /api/recursos/                    Crear ingeniero
GET  /api/proyectos/                   Lista proyectos
POST /api/proyectos/                   Crear proyecto
GET  /api/asignaciones/                Lista asignaciones
POST /api/asignaciones/                Crear asignación (calcula fecha_fin automáticamente)
POST /api/asignaciones/{id}/aprobar/   Aprobar (valida capacidad)
POST /api/asignaciones/{id}/rechazar/  Rechazar
POST /api/asignaciones/{id}/revocar/   Revocar
GET  /api/asignaciones/{id}/log/       Log de auditoría
GET  /api/calendario/feriados/?year=   Feriados Colombia
POST /api/calendario/dias-no-laborables/  Registrar día no laborable
GET  /api/dashboard/ocupacion/         Datos de ocupación (fecha_inicio, fecha_fin)
```

## Despliegue en Azure

Infraestructura como código en [`terraform/`](terraform/). Guía completa:
**[docs/DESPLIEGUE_AZURE.md](docs/DESPLIEGUE_AZURE.md)**.

Azure Container Apps (scale-to-zero) + PostgreSQL Flexible B1ms + SSO de Entra ID,
por ~26-29 USD/mes. Cada push a `main` construye la imagen, corre las migraciones
y publica la revisión.

```bash
cd terraform && terraform init && terraform apply
```

El despliegue está restringido por Terraform a una única suscripción
(ver `terraform/guard.tf`).

### Otros servidores (VM / intranet)

El stack Docker sigue siendo portable. Para producción fuera de Azure:
1. Copiar `.env.example` → `.env` con valores reales
2. Setear `DJANGO_DEBUG=False`
3. Setear `DJANGO_ALLOWED_HOSTS` con el dominio o IP del servidor
4. Usar una `DJANGO_SECRET_KEY` larga y aleatoria (50+ caracteres)
5. Usar `docker-compose.prod.yml`, que incluye nginx como reverse proxy

> **Ojo con TLS.** `docker-compose.prod.yml` publica **solo el puerto 80** — la
> configuracion de 443 y los certificados estan comentados. Y en produccion las
> cookies van con `Secure`, asi que sobre HTTP plano **la sesion no se llega a
> establecer**: se entra al login y se vuelve al login, sin ningun error que lo
> explique.
>
> Antes de usarlo hace falta o bien montar certificados y descomentar el 443, o
> bien poner delante un proxy corporativo que termine TLS y reenvie
> `X-Forwarded-Proto: https`.
>
> El despliegue actual es Azure Container Apps, que ya termina TLS en el
> ingress; este camino es solo para VM o intranet.
