# Guía de instalación y puesta en marcha

Este documento explica cómo montar el entorno de desarrollo desde cero.  
La aplicación es Django 5.2 corriendo en Docker. La base de datos PostgreSQL corre **fuera** de Docker, instalada directamente en Windows.

---

## Requisitos previos

| Herramienta | Versión mínima | Descarga |
|---|---|---|
| Docker Desktop | 4.x | https://www.docker.com/products/docker-desktop |
| PostgreSQL | 16 | https://www.postgresql.org/download/windows/ |
| Git | cualquiera | https://git-scm.com |

---

## Paso 1 — Clonar el repositorio

```powershell
git clone <URL-del-repositorio>
cd plataforma_gestion
```

---

## Paso 2 — Instalar PostgreSQL 16 en Windows

1. Descarga el instalador desde https://www.postgresql.org/download/windows/
2. Ejecuta el instalador. Durante la instalación:
   - **Usuario superusuario**: `postgres`
   - **Contraseña**: la que te indique el líder del proyecto (nunca hardcodear aquí)
   - **Puerto**: `5434` (el 5432 puede estar ocupado por otra instancia)
   - En Stack Builder al finalizar: click en **Cancel**, no se necesita nada adicional
3. PostgreSQL queda corriendo como servicio de Windows automáticamente.

---

## Paso 3 — Configurar PostgreSQL para aceptar conexiones desde Docker

Docker corre en una red interna separada. Hay que decirle a PostgreSQL que acepte conexiones desde esa red.

### 3a. Editar `postgresql.conf`

Ubicación típica: `C:\Program Files\PostgreSQL\16\data\postgresql.conf`

Busca la línea `listen_addresses` y cámbiala a:

```ini
listen_addresses = '*'
```

### 3b. Editar `pg_hba.conf`

Ubicación típica: `C:\Program Files\PostgreSQL\16\data\pg_hba.conf`

Agrega esta línea al **final** del archivo (antes del EOF):

```
host    all             all             172.16.0.0/12           scram-sha-256
```

Eso cubre el rango de redes internas de Docker.

### 3c. Reiniciar el servicio PostgreSQL

Abre PowerShell **como administrador** y ejecuta:

```powershell
Restart-Service postgresql-x64-16
```

---

## Paso 4 — Crear la base de datos

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -p 5434
```

Te pedirá la contraseña. Luego ejecuta dentro de psql:

```sql
CREATE DATABASE plataforma_gestion;
\q
```

---

## Paso 5 — Configurar variables de entorno

Copia el archivo de ejemplo y rellena los valores reales:

```powershell
copy .env.example .env
```

Edita `.env` con los valores que te indique el líder del proyecto:

```env
POSTGRES_DB=plataforma_gestion
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<contraseña-que-pusiste-en-el-instalador>
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5434

DJANGO_SECRET_KEY=<clave-larga-y-aleatoria>
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

> **Nunca subas el `.env` al repositorio.** Ya está en `.gitignore`.

---

## Paso 6 — Restaurar los datos

Si tienes un archivo de backup (`.sql`) con datos existentes:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -p 5434 -d plataforma_gestion -f backup.sql
```

Si es una instalación limpia sin datos previos, saltea este paso — las migraciones crean las tablas vacías.

---

## Paso 7 — Levantar la aplicación

```powershell
docker compose up web
```

Primera vez que levanta, Django aplica las migraciones automáticamente. Verás en la consola:

```
System check identified no issues (0 silenced).
Starting development server at http://0.0.0.0:8000/
```

Abre el navegador en http://localhost:8000

---

## Paso 8 — Crear superusuario (solo primera vez)

Abre una segunda terminal PowerShell y ejecuta:

```powershell
cd plataforma_gestion
docker compose exec web python manage.py createsuperuser
```

Sigue las instrucciones en pantalla (nombre, email, contraseña).

---

## Verificar que todo funciona

```powershell
docker compose exec web python manage.py showmigrations
```

Todas las migraciones deben aparecer con `[X]`. Si alguna aparece sin marcar, ejecuta:

```powershell
docker compose exec web python manage.py migrate
```

---

## Hacer un backup de los datos

```powershell
docker compose exec web python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission --indent 2 > backup_$(Get-Date -Format "yyyyMMdd").json
```

O directamente con pg_dump:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres -p 5434 plataforma_gestion > backup_$(Get-Date -Format "yyyyMMdd").sql
```

Los archivos `.sql` y `.json` están en `.gitignore` — no se suben al repo.

---

## Pasar a producción (Azure / AWS)

Cuando se decida desplegar en la nube, solo hay que:

1. Crear un servidor PostgreSQL administrado (Azure Database for PostgreSQL o RDS)
2. Restaurar el backup en ese servidor
3. Cambiar en `.env`:
   ```env
   POSTGRES_HOST=tu-servidor.postgres.database.azure.com
   POSTGRES_USER=postgres@tu-servidor
   POSTGRES_PASSWORD=<nueva-contraseña>
   POSTGRES_PORT=5432
   ```
4. El contenedor `web` no cambia nada — solo apunta a otro host.

---

## Comandos útiles del día a día

| Tarea | Comando |
|---|---|
| Levantar la app | `docker compose up web` |
| Levantar en segundo plano | `docker compose up web -d` |
| Ver logs | `docker compose logs web -f` |
| Apagar | `docker compose down` |
| Crear migración | `docker compose exec web python manage.py makemigrations` |
| Aplicar migraciones | `docker compose exec web python manage.py migrate` |
| Abrir shell de Django | `docker compose exec web python manage.py shell` |
