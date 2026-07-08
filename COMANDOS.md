# Referencia de Comandos — Plataforma Gestión de Recursos

Todos los comandos se corren desde la raíz del proyecto (`plataforma_gestion/`).

---

## Docker — Levantar y detener

```bash
# Levantar todo (primera vez construye la imagen, ~2 min)
docker compose up -d --build

# Levantar sin reconstruir (arranque normal)
docker compose up -d

# Detener (los datos en la BD se conservan)
docker compose down

# Detener Y borrar la base de datos (cuidado, se pierden los datos)
docker compose down -v

# Ver si los contenedores están corriendo
docker compose ps

# Ver logs del servidor web en tiempo real
docker compose logs -f web

# Ver logs de la base de datos
docker compose logs -f db
```

---

## Base de datos — Migraciones

```bash
# Aplicar migraciones (correr siempre que se traiga código nuevo)
docker compose exec web python manage.py migrate

# Crear migraciones nuevas (después de cambiar un modelo)
# IMPORTANTE: siempre pasar los nombres de las apps explícitamente.
# El contenedor corre como 'appuser' (sin permiso de escritura sobre el código
# montado), así que makemigrations debe correrse como root:
docker compose exec -u root web python manage.py makemigrations core calendar_engine assignments accounts

# 'migrate' sí corre normal (solo escribe en la BD, no en el código):
docker compose exec web python manage.py migrate

# Ver qué migraciones están pendientes
docker compose exec web python manage.py showmigrations
```

---

## Usuarios

```bash
# Crear superusuario administrador
docker compose exec web python manage.py createsuperuser

# Cambiar contraseña de un usuario existente
docker compose exec web python manage.py changepassword <nombre_usuario>
```

---

## Carga masiva de datos (recursos y proyectos)

> Detalle completo de columnas y reglas de negocio en `docs/plantillas/README.md`.
> Reejecutar es seguro (upsert): lo que ya existe se actualiza, no se duplica.

```bash
# 0. Requisito una sola vez: crear los grupos de roles (Admin, PM, Ingeniero)
docker compose exec web python manage.py setup_grupos

# 1. Copiar los CSV al contenedor (la carpeta docs/plantillas NO está montada)
docker compose cp docs/plantillas/recursos.csv web:/tmp/recursos.csv
docker compose cp docs/plantillas/proyectos.csv web:/tmp/proyectos.csv

# 2. Simular sin escribir nada (recomendado la primera vez; muestra errores por fila)
docker compose exec web python manage.py cargar_recursos /tmp/recursos.csv --dry-run

# 3. Cargar recursos/usuarios: crea User + Recurso + tarifa (ingenieros).
#    Datos de SAP vienen como "Apellidos Nombres" -> usar --orden-nombre apellido-nombre.
#    Genera un CSV con las contraseñas de los usuarios NUEVOS.
docker compose exec web python manage.py cargar_recursos /tmp/recursos.csv \
    --orden-nombre apellido-nombre --reporte /tmp/credenciales.csv

# 4. Cargar proyectos (los PM deben existir ya como usuarios: cargar recursos PRIMERO)
docker compose exec web python manage.py cargar_proyectos /tmp/proyectos.csv

# 5. Sacar el reporte de credenciales del contenedor a tu máquina
docker compose cp web:/tmp/credenciales.csv ./credenciales_generadas.csv
```

> El reporte de credenciales tiene contraseñas en texto plano: entregalo por canal
> seguro, pedí cambio de clave en el primer ingreso y borralo. No lo subas a git.

---

## Tests

```bash
# Correr todos los tests
docker compose exec web python manage.py test --verbosity=2

# Correr solo los tests de una app específica
docker compose exec web python manage.py test apps.calendar_engine
docker compose exec web python manage.py test apps.assignments
docker compose exec web python manage.py test apps.core
```

---

## Shell interactivo

```bash
# Shell de Django (para consultas y scripts rápidos)
docker compose exec web python manage.py shell

# Shell de PostgreSQL
docker compose exec db psql -U postgres -d plataforma_gestion
```

### Ejemplos útiles en el shell de Django

```python
# Listar todos los recursos activos
from apps.core.models import Recurso
Recurso.objects.filter(activo=True)

# Ver asignaciones aprobadas
from apps.assignments.models import Asignacion
Asignacion.objects.filter(estado='APROBADA')

# Ver feriados de Colombia para un año
from apps.calendar_engine.services import feriados_en_rango
from datetime import date
feriados_en_rango(date(2026, 1, 1), date(2026, 12, 31))

# Verificar si una fecha es hábil
from apps.calendar_engine.services import es_habil
es_habil(date(2026, 7, 20))  # Día de Independencia → False
```

---

## Base de datos — Backup y restauración

```bash
# Exportar / hacer backup
docker compose exec db pg_dump -U postgres plataforma_gestion > backup_$(date +%Y%m%d).sql

# Restaurar desde un backup
docker compose exec -T db psql -U postgres plataforma_gestion < backup_20260618.sql
```

---

## Conexión con cliente gráfico (DBeaver / TablePlus / pgAdmin)

| Campo       | Valor                             |
|-------------|-----------------------------------|
| Host        | `localhost`                       |
| Puerto      | `5433`                            |
| Base de datos | `plataforma_gestion`            |
| Usuario     | `postgres`                        |
| Contraseña  | la que pusiste en `.env`          |

> Puerto `5433` porque el `5432` local ya estaba ocupado por un PostgreSQL instalado en la máquina.

---

## Git — Conectar a GitHub y subir cambios

```bash
# Primera vez: conectar el repo local con GitHub
git remote add origin https://github.com/jobrrerac/plataforma-gestion.git
git branch -M main
git push -u origin main

# Subir cambios del día a día
git add -A
git commit -m "descripcion del cambio"
git push

# Ver estado del repo
git status
git log --oneline -10
```

---

## URLs del sistema

| Qué                  | URL                                      |
|----------------------|------------------------------------------|
| Dashboard de ocupación | http://localhost:8000/dashboard/       |
| Admin Django           | http://localhost:8000/admin/           |
| API REST (browsable)   | http://localhost:8000/api/             |
| Feriados Colombia 2026 | http://localhost:8000/api/calendario/feriados/?year=2026 |

---

## Secuencia de inicio (primera vez)

```bash
# 1. Copiar y editar variables de entorno
cp .env.example .env
# Editar .env: cambiar DJANGO_SECRET_KEY y POSTGRES_PASSWORD

# 2. Levantar Docker
docker compose up -d --build

# 3. Crear tablas en la base de datos
docker compose exec web python manage.py migrate

# 4. Crear usuario administrador
docker compose exec web python manage.py createsuperuser

# 5. Abrir en el navegador
# http://localhost:8000/dashboard/
```

## Secuencia de inicio (días siguientes)

```bash
docker compose up -d
# Abrir http://localhost:8000/dashboard/
```

---

## Despliegue en servidor nuevo (Azure / VM / intranet)

> Los datos que cargues localmente NO se copian solos al servidor. Hay que llevarlos manualmente.
> Las migraciones (estructura de tablas) sí viajan con el código en Git — no hay que recrearlas.

### Opción A — Llevar toda la base de datos (recomendado al pasar a producción)

```bash
# 1. En tu máquina local: exportar
docker compose exec db pg_dump -U postgres plataforma_gestion > datos_produccion.sql

# 2. Copiar el archivo al servidor (ejemplo con scp)
scp datos_produccion.sql usuario@ip-servidor:/ruta/plataforma_gestion/

# 3. En el servidor: levantar, migrar e importar
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec -T db psql -U postgres plataforma_gestion < datos_produccion.sql
```

### Opción B — Cargar datos maestros desde CSV (recursos y proyectos)

Para poblar el servidor desde los CSV (útil para la carga inicial de personal y
proyectos). A diferencia de `loaddata`, los loaders crean también los usuarios de
login con su rol y generan las credenciales. Detalle en la sección
"Carga masiva de datos" de arriba y en `docs/plantillas/README.md`.

```bash
# 1. En el servidor: levantar, migrar y crear los grupos de roles
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py setup_grupos
docker compose exec web python manage.py createsuperuser   # solo el admin inicial

# 2. Copiar los CSV al contenedor y cargarlos (recursos PRIMERO, luego proyectos)
docker compose cp recursos.csv web:/tmp/recursos.csv
docker compose cp proyectos.csv web:/tmp/proyectos.csv
docker compose exec web python manage.py cargar_recursos /tmp/recursos.csv \
    --orden-nombre apellido-nombre --reporte /tmp/credenciales.csv
docker compose exec web python manage.py cargar_proyectos /tmp/proyectos.csv

# 3. Sacar el reporte de credenciales del contenedor (entregar seguro y borrar)
docker compose cp web:/tmp/credenciales.csv ./credenciales_produccion.csv
```

### Checklist de despliegue en servidor

```
[ ] Clonar el repo en el servidor:
      git clone https://github.com/jobrrerac/plataforma-gestion.git

[ ] Crear .env con valores de PRODUCCIÓN:
      cp .env.example .env
      # Editar:
      #   DJANGO_DEBUG=False
      #   DJANGO_SECRET_KEY=clave-larga-aleatoria-de-50-chars
      #   DJANGO_ALLOWED_HOSTS=ip-del-servidor-o-dominio
      #   POSTGRES_PASSWORD=contraseña-segura

[ ] Levantar Docker:
      docker compose up -d --build

[ ] Aplicar migraciones:
      docker compose exec web python manage.py migrate

[ ] Crear la tabla de cache (solo producción; la usa el rate limiting del login):
      docker compose exec web python manage.py createcachetable

[ ] Crear superusuario admin:
      docker compose exec web python manage.py createsuperuser

[ ] Importar datos (Opción A o B según el caso)

[ ] Verificar que el sistema abre en http://ip-del-servidor:8000/dashboard/
```

### Variables de entorno para producción

| Variable | Desarrollo | Producción |
|---|---|---|
| `DJANGO_DEBUG` | `True` | **`False`** |
| `DJANGO_SECRET_KEY` | cualquiera | **clave larga y única** |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | **IP o dominio del servidor** |
| `POSTGRES_PASSWORD` | `changeme` | **contraseña segura** |
