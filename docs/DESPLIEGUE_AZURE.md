# Despliegue en Azure

Infraestructura como código para llevar la plataforma a Azure Container Apps con
PostgreSQL gestionado y SSO de Microsoft Entra ID.

Todo lo que hay en `terraform/` está pensado con un criterio: **empezar por lo
más barato y escalar solo cuando haga falta**, nunca al revés. Donde se
descartó una opción más cara, el motivo está escrito en el propio `.tf`.

---

## 1. Qué se crea

| Recurso | SKU / plan | Para qué | USD/mes |
|---|---|---|---|
| PostgreSQL Flexible Server | Burstable **B1ms**, 32 GiB | Base de datos | ~16 |
| Container Apps Environment | Consumption | Entorno de ejecución | 0 |
| Container App | 0.5 vCPU / 1 GiB, min 0 | La aplicación | ~5-8 |
| Container Apps Job | bajo demanda | Migraciones | ~0 |
| Container Registry | Basic | Imágenes Docker | ~5 |
| Log Analytics | PerGB2018, tope 0,5 GB/día | Logs | 0 |
| Managed Identity ×2 | — | Pull del ACR y despliegue | 0 |
| Entra ID App Registration | Free | SSO | 0 |

**Total estimado: ~26-29 USD/mes.**

El grueso es la base de datos, que está encendida siempre. La aplicación, con
`min_replicas = 0`, se apaga cuando nadie la usa y solo consume mientras hay
tráfico; además los primeros 180.000 vCPU-s y 360.000 GiB-s de cada mes son
gratuitos por suscripción.

### Nomenclatura

Se sigue la convención ya usada en la suscripción, para que el nuevo grupo se
distinga de los existentes en el seguimiento de costos:

```
<tipo>-<workload>-<entorno>-<región>-<secuencia>

rg-sdlcagents-dev-eus2-001      ← ya existía
rg-prodbench-dev-eus2-001       ← ya existía
rg-platgestion-prod-eus2-001    ← este proyecto
```

Todo vive en **su propio grupo de recursos**, separado del resto: filtrar el
coste del proyecto es mirar un solo grupo.

Dos excepciones deliberadas: el ACR y el servidor de PostgreSQL llevan un sufijo
aleatorio en vez del número de secuencia, porque sus nombres compiten en un
espacio de nombres global con todo Azure. Y el job de migraciones no lleva
sufijo descriptivo: los nombres de Container App y de job están topados en 32
caracteres, y `caj-` ya dice que es un job.

### Lo que NO se crea, y por qué

- **Alta disponibilidad de la base**: no existe en el tier Burstable. Habrá
  ventanas de mantenimiento con reinicio.
- **NAT Gateway / IP de salida fija**: ~32 USD/mes, más que todo lo demás junto.
  Consecuencia: el firewall de PostgreSQL usa la regla "servicios de Azure".
  Ver [Endurecer la red](#endurecer-la-red).
- **Reservas de capacidad**: sin compromiso anual, por decisión explícita.
- **MFA / Acceso Condicional**: requiere licencia Entra ID P1 (~6 USD/usuario/mes)
  y el objetivo aquí es no memorizar varias contraseñas, no añadir un segundo
  factor.
- **Redis para el cache**: `production.py` usa `DatabaseCache`, que va sobre la
  base que ya se paga. Con ~50 usuarios sobra.

---

## 2. El guard de suscripción

En la cuenta hay cuatro suscripciones, y tres viven en tenants distintos:

| Suscripción | Tenant | ¿Permitida? |
|---|---|---|
| **Azure subscription 1** | `inetumoffshore.onmicrosoft.com` | **Sí** |
| Azure for Students | `577fc1d8…` | No |
| CV_AZURE_PRD | `d8acf8f6…` | No |
| Microsoft Azure Enterprise | `d8acf8f6…` | No |

Un `az account set` despistado desplegaría en la cuenta equivocada. Hay tres
capas para que eso no pase:

1. **`providers.tf`** fija `subscription_id` y `tenant_id` explícitamente en vez
   de heredar lo que tenga el CLI. Terraform apunta a la suscripción correcta
   aunque `az` esté mirando otra.
2. **`variables.tf`** valida por comparación literal, para que nadie sobreescriba
   esos IDs desde un `.tfvars` o un `-var` en línea de comandos.
3. **`guard.tf`** comprueba, en cada `plan`, que las credenciales resueltas
   apuntan realmente donde creemos. Es la única capa que detecta el caso "las
   variables dicen A pero el token vale para B". Todo cuelga del grupo de
   recursos, que depende del guard: si el guard falla, no se crea nada.

Comprobado que funciona:

```console
$ terraform plan -var="subscription_id=00000000-0000-0000-0000-000000000000"
Error: Invalid value for variable
  Suscripcion no permitida.
  Este proyecto SOLO puede desplegarse en 'Azure subscription 1' ...
```

El pipeline de despliegue repite la comprobación con `az account show` antes de
tocar nada.

---

## 3. Desplegar por primera vez

### Requisitos

```bash
az version          # 2.87+
terraform version   # 1.9+
docker --version
```

### Paso 1 — Autenticarse en el tenant correcto

```bash
az login --tenant fdb323c6-1c3c-47a4-9144-2cabbc82699c
az account set --subscription b383e51f-9354-4d6a-8d3b-cc9abb1b9743
az account show --query "{sub:name, tenant:tenantDefaultDomain}" -o table
```

Debe decir `Azure subscription 1` / `inetumoffshore.onmicrosoft.com`.

> Crear el App Registration del SSO requiere rol **Global Administrator** o
> **Application Developer** en Entra. Ser Owner de la suscripción no basta: son
> dos sistemas de permisos distintos.

### Paso 2 — Abrir el firewall para tu máquina

Necesario para restaurar el backup inicial desde tu equipo.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
echo "ip_desarrollador = \"$(curl -s https://api.ipify.org)\"" >> terraform.tfvars
```

### Paso 3 — Crear la infraestructura

```bash
terraform init
terraform plan      # revisar: ~30 recursos, ninguno destruido
terraform apply
```

Tarda unos 10-15 minutos; casi todo es la creación del servidor PostgreSQL.

En este punto la Container App existe pero corre una **imagen de arranque
pública**, no la aplicación: el registro todavía está vacío. Es normal.

### Paso 4 — Restaurar la base de datos

> **El orden importa.** El backup se restaura *después* de crear la
> infraestructura y *antes* del job de migraciones:
> `apply` → restaurar → job. El dump trae `django_migrations`, así que el
> `migrate` posterior detecta que ya está todo aplicado y no hace nada. Al
> revés —`migrate` sobre una base vacía y luego el dump encima— el dump choca
> con las tablas recién creadas.

> **Generar el dump en el momento, nunca reutilizar uno del repositorio.**
> En el despliegue inicial el `.sql` versionado tenía dos meses y 27
> migraciones; la base viva tenía 38. Restaurarlo habría dejado fuera los
> módulos de cesión, liberación y cambio de contraseña obligatorio, y el
> esquema no habría coincidido con la imagen. Un dump viejo no da ningún error
> al restaurarse: simplemente faltan cosas, y se descubre tarde.

Volcar la base de desarrollo actual (no hace falta `psql` en la máquina):

```bash
docker run --rm --add-host=host.docker.internal:host-gateway \
  -e PGPASSWORD="$POSTGRES_PASSWORD" postgres:16-alpine \
  pg_dump -h host.docker.internal -p 5434 -U postgres -d plataforma_gestion \
  --no-owner --no-acl --clean --if-exists > backup_$(date +%Y%m%d).sql
```

`--no-owner --no-acl` importa: en Azure el administrador es `pgadmin`, no
`postgres`, y sin esas banderas el restore falla en cada `ALTER ... OWNER TO`.

Restaurarlo en Azure:

```bash
PGPASS=$(terraform -chdir=terraform output -raw postgres_password)
PGHOST=$(terraform -chdir=terraform output -raw postgres_fqdn)

docker run --rm -i -e PGPASSWORD="$PGPASS" postgres:16-alpine \
  psql "host=$PGHOST port=5432 dbname=plataforma_gestion user=pgadmin sslmode=require" \
  < backup_$(date +%Y%m%d).sql
```

Comprobar que el número de migraciones coincide con el de la base de origen
antes de dar el paso por bueno:

```bash
docker run --rm -e PGPASSWORD="$PGPASS" postgres:16-alpine \
  psql "host=$PGHOST port=5432 dbname=plataforma_gestion user=pgadmin sslmode=require" \
  -tAc "select count(*) from django_migrations"
```

Si empiezas de cero y no hay datos que traer, salta este paso: el job de
migraciones crea el esquema.

### Paso 5 — Construir y publicar la imagen

```bash
ACR=$(terraform output -raw acr_nombre)
az acr login --name "$ACR"

docker build -t "$ACR.azurecr.io/plataforma-gestion:v1" ../backend
docker push "$ACR.azurecr.io/plataforma-gestion:v1"
```

### Paso 6 — Migraciones y publicación

```bash
RG=$(terraform output -raw resource_group)
JOB=$(terraform output -raw job_migraciones_nombre)
APP=$(terraform output -raw container_app_nombre)
IMAGEN="$ACR.azurecr.io/plataforma-gestion:v1"

# Migraciones + tabla de cache + grupos Admin/PM/Ingeniero
az containerapp job update -n "$JOB" -g "$RG" --image "$IMAGEN"
az containerapp job start  -n "$JOB" -g "$RG"

# Publicar la aplicación
az containerapp update -n "$APP" -g "$RG" --image "$IMAGEN"

terraform output -raw url_aplicacion
```

### Paso 7 — Crear el superusuario de emergencia

Una cuenta local que funcione aunque Entra falle o el secreto caduque:

```bash
az containerapp exec -n "$APP" -g "$RG" --command "python manage.py createsuperuser"
```

### Paso 8 — Asignar roles en Entra

Las asignaciones **ya están declaradas en Terraform** (variable `roles_entra`),
reproduciendo los grupos que cada persona tiene en la plataforma. Para añadir a
alguien, se edita esa variable y se aplica: queda revisable en el repositorio en
vez de depender de que alguien recuerde una serie de clics en el portal.

Sin un rol asignado nadie puede iniciar sesión por SSO
(`app_role_assignment_required = true`). Es el único punto de control de acceso.

Quien ejecuta el `terraform apply` queda asignado como **Admin**
automáticamente, para que nunca haya un bloqueo total.

Para hacerlo desde el portal en un caso puntual:

```bash
terraform output -raw entra_url_asignar_roles
```

### Paso 9 — Activar el despliegue automático

```bash
terraform output -raw cicd_comandos_gh | bash
```

Carga los secrets y variables en GitHub. A partir de ahí cada push a `main`
construye la imagen, corre las migraciones y publica la revisión.

---

## 4. Cómo funciona el SSO

### Los dos métodos conviven, siempre

`AUTHENTICATION_BACKENDS` prueba primero `ModelBackend` (usuario y contraseña) y
después el backend de Entra. La pantalla de login muestra el botón de Microsoft
**y** el formulario de siempre.

Esto no es indecisión: es el plan de contingencia. El secreto de cliente caduca
al año, Entra puede tener una incidencia, y el día que eso pase alguien tiene que
poder entrar. La variable `OIDC_HABILITADO=False` apaga el SSO por completo sin
tocar código ni redesplegar la infraestructura.

### De roles de Entra a grupos de Django

Todo el RBAC del proyecto se apoya en `user.groups` (ver
[roles.py](../backend/apps/accounts/roles.py)). El backend de SSO
([oidc.py](../backend/apps/accounts/oidc.py)) sincroniza esos grupos en **cada
inicio de sesión** a partir del claim `roles` del `id_token`:

```
App Role en Entra    →    Grupo de Django    →    Qué ve
─────────────────────────────────────────────────────────────────
Admin                →    Admin              →    todo, incl. costos
PM                   →    PM                 →    costos y tarifas
Ingeniero            →    Ingeniero          →    NUNCA costos
(ninguno)            →    (ninguno)          →    NUNCA costos
```

Decisiones que conviene conocer:

- **Quitar un rol en Entra lo quita en Django** en el siguiente login. La fuente
  de verdad es el directorio, no la base de datos de la app.
- **Los grupos ajenos no se tocan.** Si alguien creó un grupo a mano para otra
  cosa, sobrevive a la sincronización.
- **`is_staff` sí, `is_superuser` nunca.** El rol Admin abre el `/admin/` de
  Django, pero saltarse todos los permisos es una decisión deliberada que se
  toma a mano, no algo que conceda un token.
- **Sin rol se entra, pero sin permisos.** Es el fallo seguro: la allowlist de
  `roles.py` hace que un usuario sin grupo no vea costos ni datos personales.
- **Los roles se leen del `id_token`, no de `/userinfo`.** Entra solo emite los
  app roles en el token, y así se ahorra una llamada a Graph por login.

### Dos dominios, una identidad

Este es el detalle menos obvio del despliegue, y sin él el SSO haría un
estropicio silencioso.

El tenant `inetumoffshore.onmicrosoft.com` **no tiene verificado el dominio
corporativo**, así que los UPN de Entra son `nombre@inetumoffshore.onmicrosoft.com`.
Pero en la plataforma —y en `Recurso.email`— las 33 personas existentes son
`nombre@inetum.com`. El nombre coincide; el dominio no.

Sin traducción, el primer inicio de sesión por SSO de cada persona habría creado
una **cuenta nueva y vacía**, dejando huérfana la existente con todo su historial
de asignaciones. No habría dado ningún error: simplemente habría 33 cuentas
duplicadas.

`OIDC_DOMINIO_ALIAS` resuelve eso traduciendo el dominio al entrar:

```
login:                luisa.acosta-pelaez@inetumoffshore.onmicrosoft.com
identidad en la app:  luisa.acosta-pelaez@inetum.com
```

El login va por Entra; **todo lo demás** —cuenta de Django, `Recurso`,
asignaciones, auditoría— sigue usando el email corporativo. Nadie cambia de
cuenta.

### Cuentas cuyo UPN no deriva de un email

`admin@inetumoffshore.onmicrosoft.com` es la cuenta administrativa del tenant:
no corresponde a ningún `nombre@inetum.com`, así que el alias de dominio no
basta. `OIDC_USUARIO_ALIAS` mapea identidades concretas:

```
admin@inetumoffshore.onmicrosoft.com  →  inetum_admin
```

Una cuenta enlazada así **conserva su identidad de negocio**: el SSO no le
sobreescribe email ni nombre. El alias significa "esta identidad de Entra *es*
esta cuenta", no "cópiale los datos del token". Sin esa protección, entrar como
`admin@` habría pisado el email real del superusuario.

Dos salvaguardas más en este camino:

- Si el alias apunta a una cuenta que no existe, **se deniega el acceso** en vez
  de crear un superusuario a partir de un token.
- A un superusuario **no se le retira `is_staff`** aunque pierda el rol en
  Entra: lo dejaría fuera de su propio `/admin/`, y recuperarlo exigiría acceso
  directo a la base de datos.

### Enlace con `Recurso`

Al entrar por SSO, si existe un `Recurso` con el mismo email y sin usuario
asignado, se enlaza automáticamente. Un `Recurso` ya enlazado a otra cuenta no se
pisa nunca.

### Cuentas del importador masivo

Una cuenta creada con credencial temporal que después entra por SSO tiene su
`CambioPasswordPendiente` borrado al iniciar sesión: no tiene sentido pedirle
cambiar una contraseña que no va a usar, y el formulario le pediría la anterior,
que no conoce. El middleware además deja pasar a cualquier cuenta sin contraseña
utilizable.

---

## 5. Cambios que hubo que hacer en la aplicación

Resumen de por qué el código no servía tal cual para Container Apps.

| Qué | Por qué |
|---|---|
| `CMD` en el [Dockerfile](../backend/Dockerfile) | No tenía: el comando venía del `docker-compose`. En Container Apps el contenedor arrancaba y moría. |
| `collectstatic` en el build | La imagen sale lista y el arranque no depende de nada externo. |
| **WhiteNoise** | En `docker-compose.prod.yml` los estáticos los servía nginx desde un volumen compartido. En Container Apps hay un solo contenedor y el ingress habla directo con gunicorn. |
| `sslmode` configurable | Azure exige TLS (`require_secure_transport = ON`). La conexión fallaba al primer intento. Por defecto `prefer`, para que el postgres local sin certificado siga funcionando. |
| `/healthz/` y `/readyz/` | Sondas de la plataforma. Exentas de `SECURE_SSL_REDIRECT`: llegan por HTTP desde dentro del entorno y un 301 las haría fallar. `/healthz/` además comprueba la base de datos. |
| Job de migraciones | No había dónde correrlas. Con varias réplicas arrancando a la vez, todas ejecutarían `migrate` en paralelo sobre la misma base. |
| `createcachetable` en el job | `production.py` usa `DatabaseCache` sobre `django_cache`, y esa tabla **no existía**. Sin ella el rate limiting del login revienta al primer POST. |
| `LOGGING` a stdout | Container Apps recoge la salida estándar. Escribir a un archivo dentro del contenedor perdería los registros en cada reinicio. |

> **Nota sobre WhiteNoise**: el almacenamiento con manifiesto vive solo en
> `production.py`, no en `base.py`. Puesto en `base.py` rompe los tests y el
> desarrollo local, porque exige un `collectstatic` previo que ahí no se hace.

El `docker-compose.yml` local sigue funcionando exactamente igual.

---

## 6. Operación

### Ver logs

```bash
az containerapp logs show -n "$APP" -g "$RG" --follow
```

### Entrar al contenedor

```bash
az containerapp exec -n "$APP" -g "$RG" --command bash
```

### Consultar los secretos

```bash
cd terraform
terraform output -raw postgres_password
terraform output -raw entra_client_secret
```

### Rotar el secreto del SSO

**Azure no avisa de esto por ningún canal.** No manda correo, no genera alerta,
y en el portal solo se ve si alguien va a mirarlo a propósito. El día que
caduca, el botón "Iniciar sesión con Microsoft" deja de funcionar sin ningún
mensaje que explique por qué.

Por eso avisa la propia aplicación: a los **Admin** les aparece una franja en
la cabecera desde 60 días antes, con el comando de rotación. Está pensada para
quien herede esto y no sepa que esa fecha existe.

Vigencia: **24 meses** (el máximo que admite Azure para un secreto de cliente).
La fecha exacta:

```bash
terraform -chdir=terraform output entra_caducidad_secreto
```

Rotarlo:

```bash
cd terraform
terraform apply -replace=azuread_application_password.sso
```

El secreto nuevo se crea **antes** de borrar el viejo (`create_before_destroy`),
así que no hay ventana sin servicio, y el mismo apply actualiza el secreto de la
Container App.

Mientras se rota, el login local sigue funcionando: es la razón de que no se
haya eliminado nunca.

> **Para eliminar la caducidad del todo** haría falta sustituir el secreto por
> una credencial federada contra la managed identity de la Container App. Es
> viable, pero `mozilla-django-oidc` envía `client_secret` al token endpoint, así
> que habría que implementar el intercambio con *client assertion* a mano.

### Apagar todo temporalmente

La base es el 60% de la factura y se puede parar hasta 7 días seguidos:

```bash
az postgres flexible-server stop  -n <servidor> -g "$RG"
az postgres flexible-server start -n <servidor> -g "$RG"
```

La Container App con `min_replicas = 0` ya no cuesta nada cuando nadie la usa.

### Destruir todo

```bash
terraform destroy
```

La base de datos tiene `prevent_destroy`: hay que quitarlo a mano en
`database.tf`. Es deliberado — evita que un `destroy` distraído se lleve los
datos.

---

## 7. Cuando haga falta escalar

Por orden de cuánto duele antes de necesitarlo:

| Síntoma | Cambio | Coste extra |
|---|---|---|
| El arranque en frío molesta | `min_replicas = 1` | ~15 USD/mes |
| La app va lenta con varios usuarios | `cpu = 1`, `memoria = "2Gi"` | ~2x el cómputo |
| La base se queda corta | `postgres_sku = "GP_Standard_D2ds_v5"` | ~4x la base |
| Se acaban las conexiones | Bajar `max_replicas` o `gunicorn_workers` | 0 |
| Hace falta MFA | Licencias Entra ID P1 | ~6 USD/usuario/mes |

Cada uno es cambiar una variable y `terraform apply`.

### Endurecer la red

El firewall de PostgreSQL usa hoy la regla "permitir servicios de Azure"
(`0.0.0.0`), porque la IP de salida de un entorno Consumption **no es estática**:
Microsoft documenta que puede cambiar, así que no se puede fijar una regla por
IP. Mientras tanto protegen el TLS obligatorio y una contraseña aleatoria de 32
caracteres.

Las dos salidas reales, cuando compense:

1. **Inyección en VNet** (0 USD): el entorno de Container Apps en una subred
   delegada y PostgreSQL con acceso privado. Contrapartida: la base deja de ser
   alcanzable desde tu máquina, y harían falta un bastión o una VPN para
   restaurar dumps.
2. **Workload profiles + NAT Gateway** (~32 USD/mes): IP de salida fija y regla
   de firewall por IP, manteniendo el acceso público.

### Bajar a ghcr.io

Si los ~5 USD/mes del ACR llegan a importar, GitHub Container Registry es
gratuito. Contrapartida: hay que guardar un PAT de GitHub como secreto en la
Container App, que es exactamente la credencial de larga vida que el ACR con
managed identity evita.

---

## 8. Problemas conocidos

**La revisión no arranca.** Casi siempre es la sonda: si `SECURE_SSL_REDIRECT`
devuelve 301 a `/healthz/`, la plataforma da la revisión por no sana. La exención
está en `SECURE_REDIRECT_EXEMPT` de `production.py`; comprobar que sigue ahí.

**`ValueError: Missing staticfiles manifest entry`.** Se corrió con
`config.settings.production` sin haber hecho `collectstatic`. En la imagen se
hace en el build; en local, usar `config.settings.local`.

**No se puede iniciar sesión por SSO.** Comprobar, por este orden: que la persona
tiene un rol asignado en Entra (`terraform output entra_url_asignar_roles`), que
el secreto no ha caducado, y que el redirect URI del App Registration coincide
con `terraform output entra_redirect_uri`.

**Los tests fallan en CI por `ruff`.** Hay 8 errores de lint **preexistentes** en
`apps/assignments/` (imports sin usar, `;` de más) que no tienen que ver con el
despliegue. Están desde antes; conviene limpiarlos para que CI vuelva a verde.
