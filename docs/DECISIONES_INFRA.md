# Decisiones de infraestructura

Por qué cada valor de `terraform/` es el que es.

Vive aparte para que los `.tf` se puedan leer de un vistazo: en un fichero de
configuración lo que se busca es *dónde está una variable*, no el ensayo sobre
por qué vale 3. Cada apartado se enlaza desde el `.tf` correspondiente con un
comentario de una línea.

Si cambias un valor, actualiza aquí el motivo. Un valor sin motivo escrito es un
valor que alguien va a "optimizar" dentro de seis meses.

---

## Índice

**Identidad y guardas**
- [Por qué la suscripción y el tenant están fijados](#suscripcion-y-tenant-fijados)

**Ubicación y tamaño**
- [Por qué `eastus2`](#region-eastus2)
- [Por qué el PostgreSQL más pequeño que existe](#postgres-b1ms)
- [Por qué `ip_desarrollador` puede quedar vacía](#ip-desarrollador)
- [Por qué el firewall de la base acepta "servicios de Azure"](#firewall-de-la-base)

**Container App**
- [Por qué la imagen inicial es una de Microsoft](#imagen-de-arranque)
- [Por qué `max_replicas = 1` y no hay autoescalado](#sin-autoescalado)
- [Por qué 3 workers de gunicorn](#gunicorn-workers)

**Coste**
- [Por qué los logs tienen un tope duro de 0.1 GB/día](#cuota-de-logs)
- [Qué hace y qué no hace el presupuesto](#presupuesto)
- [Por qué las alertas pueden no llegar](#emails-de-alerta)

**SSO y Entra ID**
- [Dos dominios, una identidad](#dos-dominios)
- [Por qué existe `usuario_alias`](#usuario-alias)
- [Por qué el secreto dura 24 meses y no 12](#vigencia-del-secreto)

**Acceso**
- [`roles_entra` es el control de acceso, no una lista de permisos](#roles-entra)
- [Por qué los invitados B2B necesitan su propia lista](#invitados-b2b)

**CI/CD**
- [Por qué `github_environment` tiene que coincidir con el workflow](#github-environment)

---

## Suscripción y tenant fijados

`subscription_id` y `tenant_id` llevan una `validation` que solo acepta un
valor. No es paranoia: la cuenta ve **cuatro suscripciones repartidas en tres
tenants** (Azure for Students, CV_AZURE_PRD, Microsoft Azure Enterprise).

Desplegar en la equivocada no falla — crea recursos que alguien paga y que nadie
encuentra. La validación de las variables es la primera de las tres capas; las
otras dos están en `guard.tf` y comprueban las credenciales resueltas en tiempo
de ejecución, no solo lo que dice el fichero.

Si algún día hace falta otra suscripción, hay que editar la validación
deliberadamente. Que cueste es el punto.

## Región eastus2

La más barata con Container Apps + PostgreSQL Burstable y con latencia razonable
desde Colombia.

`brazilsouth` está más cerca pero cuesta un 30–50 % más. Para una aplicación
interna donde nadie nota 40 ms, no compensa.

## Postgres B1ms

`B_Standard_B1ms` (1 vCPU / 2 GiB) es el escalón más pequeño que vende Azure, y
`32768` MB el almacenamiento mínimo que acepta Flexible Server — poner menos da
error, no ahorro.

`backup_retention_days = 7` es también el mínimo, y está **incluido en el
precio**: subirlo empieza a costar.

Criterio general del proyecto: empezar por el escalón más barato y escalar solo
cuando haga falta de verdad.

## IP desarrollador

Abre una regla de firewall para poder correr `psql` o `pg_dump` contra el
servidor desde la máquina local (restaurar un backup, mirar datos).

Vacía = no se abre nada. Es lo correcto por defecto: una regla de firewall
abierta a una IP doméstica que ya no usa nadie es una puerta olvidada.

Obtenerla con `curl -s https://api.ipify.org`.

## Firewall de la base

La regla `0.0.0.0-0.0.0.0` no es "abierto a internet": es el valor mágico de
Azure para *permitir servicios de Azure*.

Hace falta porque la IP de salida de un entorno de Container Apps en plan
Consumption **no es estática** — Microsoft documenta que puede cambiar — así que
no se puede fijar una regla por IP. Las alternativas con IP fija (NAT Gateway
sobre workload profiles, plan Dedicated) cuestan más de 30 USD/mes: más que todo
el resto de la infraestructura junta.

Lo que protege la base mientras tanto:

- `require_secure_transport = ON`, TLS obligatorio
- contraseña aleatoria de 32 caracteres, nunca escrita a mano
- la base no expone nada sin autenticar

El endurecimiento de verdad, cuando el presupuesto lo permita, es inyección en
VNet.

## Imagen de arranque

En el **primer** `apply` el registro está vacío, así que no hay ninguna imagen
propia que desplegar y la Container App no puede arrancar sin una.

Se usa una imagen pública de Microsoft solo para ese primer arranque. A partir
de ahí la gestiona el pipeline, y Terraform **ignora los cambios en este campo**
(`lifecycle.ignore_changes` en `containerapp.tf`). Si no lo ignorara, cada
`terraform apply` devolvería la aplicación a la imagen de ejemplo.

## Sin autoescalado

`min_replicas = 0` y `max_replicas = 1`. Con esos dos valores **no hay
autoescalado**: la aplicación está apagada o hay exactamente una réplica.
Capacidad fija y factura predecible.

Es una decisión consciente de esperar a ver rendimiento real antes de escalar.
El coste del scale-to-zero es un arranque en frío de 10–30 s tras inactividad,
que para una herramienta interna es preferible a pagar ~15 USD/mes por tenerla
despierta de madrugada.

Subir `max_replicas` a 2 es un cambio de una línea, pero reintroduce el
autoescalado — Azure añade réplica a las 10 peticiones concurrentes — y con él
la parte variable de la factura.

## Gunicorn workers

3 por réplica, para compensar la réplica que ya no existe.

No cuesta nada: mismo CPU y memoria. Los workers sync pasan casi todo el tiempo
bloqueados esperando a PostgreSQL, así que tener más workers que vCPUs es lo
correcto para una aplicación de este tipo.

El límite real es `workers × max_replicas` frente a las ~50 conexiones del
B1ms. 3 × 1 = 3. Hay muchísimo margen.

## Cuota de logs

`log_cuota_diaria_gb = 0.1` es un **tope duro** de ingesta.

0.1 GB/día son como mucho 3 GB/mes, por debajo de los 5 GB/mes gratuitos de
Azure Monitor. Con eso los logs **no pueden costar dinero**, ni siquiera si la
aplicación entra en un bucle de errores a las tres de la mañana.

Sigue siendo enorme para esta aplicación: 100 MB de log al día son decenas de
miles de líneas. Si algún día se alcanza el tope, la ingesta se detiene hasta el
día siguiente — y eso ya sería síntoma de un problema que mirar, no de una cuota
mal puesta.

## Presupuesto

`presupuesto_mensual_usd` **no corta nada**. Azure no apaga recursos por
superarlo: solo avisa. Sirve para enterarse de una desviación cuando empieza, no
al recibir la factura.

Estimación real: ~21 USD fijos (PostgreSQL + ACR) más 4–8 de uso. El valor por
defecto deja margen para no disparar avisos por ruido.

## Emails de alerta

Por defecto la cuenta administrativa del tenant.

**Cuidado:** un tenant `.onmicrosoft.com` sin licencia de Exchange **no recibe
correo**. Si ese es el caso, hay que poner aquí una dirección real o los avisos
de presupuesto se pierden sin que nadie lo note — que es la peor forma de
perderlos.

## Dos dominios

El tenant no tiene verificado el dominio corporativo, así que los UPN de Entra
son `nombre@inetumoffshore.onmicrosoft.com` mientras que en la plataforma la
persona es `nombre@inetum.com`.

La aplicación traduce entre ambos al iniciar sesión (`OIDC_DOMINIO_ALIAS`). Sin
esa traducción el SSO **no reconocería ninguna cuenta existente**: crearía una
nueva y vacía por cada persona, dejando huérfano todo su historial de
asignaciones. Y no daría ningún error — simplemente habría 33 duplicados.

Explicado en detalle en [DESPLIEGUE_AZURE.md](DESPLIEGUE_AZURE.md).

## Usuario alias

Equivalencias explícitas entre una identidad de Entra y una cuenta concreta de
Django, para los UPN que no derivan de ningún email de negocio.

La cuenta administrativa del tenant (`admin@…onmicrosoft.com`) tiene que entrar
como el superusuario que ya existe, no como una cuenta nueva llamada "admin". El
alias significa *"esta identidad **es** esta cuenta"*, no *"cópiale los datos del
token"*: una cuenta enlazada así conserva su email y su nombre.

Formato: `upn_completo => username_de_django`.

## Vigencia del secreto

24 meses, no 12, por una razón concreta: **cuando caduque, es probable que quien
montó esto ya no esté.**

Azure no avisa de la caducidad. Ni correo, ni alerta, ni nada: el botón de
Microsoft simplemente deja de funcionar un martes. El aviso lo da la propia
aplicación (`OIDC_SECRETO_CADUCA`, con `dias_aviso_caducidad_secreto` de
antelación), y el login local sigue siendo la vía de entrada mientras se rota.

Rotarlo a propósito:

```bash
terraform apply -replace=azuread_application_password.sso
```

## Roles Entra

Con `app_role_assignment_required = true`, **quien no aparezca en `roles_entra`
no puede iniciar sesión por SSO**. No es una lista de permisos: es el control de
acceso.

Los valores por defecto reproducen los grupos que esas personas ya tenían en la
base de datos, para que activar el SSO no alterara permisos existentes. Quien no
esté en Django todavía se deja fuera a propósito: dar un rol es una decisión, no
un efecto secundario del despliegue.

`admin@` no va en la lista — se asigna por separado en
`azuread_app_role_assignment.admin_inicial`, para que nunca pueda haber un
bloqueo total por un error en el mapa.

Un detalle que sorprende: quitar a alguien de aquí **cierra el SSO pero no el
login local**. Quien tenga contraseña local sigue entrando. Para revocar de
verdad hay que además desmarcar `Activo` en Django.

## Invitados B2B

Un invitado tiene **dos identidades** en el tenant: su cuenta local
`nombre@inetumoffshore.onmicrosoft.com` y un objeto Guest cuyo UPN es una
deformación de su correo real:

```
erika.castiblanco-monroy_inetum.com#EXT#@inetumoffshore.onmicrosoft.com
```

El app role vive en cada objeto por separado, y `roles_entra` solo alcanza la
cuenta local. Sin esta lista pasa lo siguiente: cambias el rol, aplicas,
Terraform dice que todo está al día, y la persona sigue entrando con el rol
viejo. **Sin ningún aviso**, porque el objeto que Terraform mira sí está
correcto.

El rol sale de `roles_entra`, no de aquí: no puede haber dos sitios donde decir
lo mismo. La `validation` lo exige.

## GitHub environment

Cuando un job declara `environment:`, GitHub **cambia el subject del token
OIDC**:

```
sin environment:  repo:owner/repo:ref:refs/heads/main
con environment:  repo:owner/repo:environment:produccion
```

Si este valor no coincide con el del workflow, el despliegue falla con
`AADSTS700213` en el primer paso, antes de tocar nada. Por eso hay dos
credenciales federadas en `cicd.tf` y no una.
