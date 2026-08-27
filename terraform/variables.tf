# ---------------------------------------------------------------------------
# Identidad de la suscripcion - NO cambiar sin leer guard.tf
# ---------------------------------------------------------------------------

variable "subscription_id" {
  description = "Suscripcion destino. Debe ser 'Azure subscription 1' del tenant inetumoffshore."
  type        = string
  default     = "b383e51f-9354-4d6a-8d3b-cc9abb1b9743"

  validation {
    condition     = var.subscription_id == "b383e51f-9354-4d6a-8d3b-cc9abb1b9743"
    error_message = <<-EOT
      Suscripcion no permitida.

      Este proyecto SOLO puede desplegarse en 'Azure subscription 1'
      (b383e51f-9354-4d6a-8d3b-cc9abb1b9743), tenant inetumoffshore.onmicrosoft.com.

      Hay otras suscripciones visibles en la cuenta (Azure for Students,
      CV_AZURE_PRD, Microsoft Azure Enterprise) que viven en tenants distintos.
      Desplegar ahi generaria costos en la cuenta equivocada.

      Si de verdad hace falta otra suscripcion, editar esta validacion de forma
      consciente y deliberada.
    EOT
  }
}

variable "tenant_id" {
  description = "Tenant de Entra ID (inetumoffshore.onmicrosoft.com)."
  type        = string
  default     = "fdb323c6-1c3c-47a4-9144-2cabbc82699c"

  validation {
    condition     = var.tenant_id == "fdb323c6-1c3c-47a4-9144-2cabbc82699c"
    error_message = "Tenant no permitido. Solo inetumoffshore.onmicrosoft.com (fdb323c6-1c3c-47a4-9144-2cabbc82699c)."
  }
}

# ---------------------------------------------------------------------------
# Nombres y ubicacion
# ---------------------------------------------------------------------------

variable "proyecto" {
  description = "Prefijo corto para nombrar recursos."
  type        = string
  default     = "platgestion"

  validation {
    condition     = can(regex("^[a-z0-9]{4,14}$", var.proyecto))
    error_message = "Solo minusculas y digitos, 4-14 caracteres (limitacion de nombres de ACR)."
  }
}

variable "entorno" {
  description = "Entorno logico (prod, dev...). Va en el nombre de los recursos."
  type        = string
  default     = "prod"
}

variable "location" {
  description = <<-EOT
    Region de Azure. eastus2 es la mas barata con Container Apps + PostgreSQL
    Burstable y con latencia razonable desde Colombia. brazilsouth esta mas
    cerca pero cuesta ~30-50% mas.
  EOT
  type        = string
  default     = "eastus2"
}

# ---------------------------------------------------------------------------
# Base de datos - el escalon mas pequeno que existe
# ---------------------------------------------------------------------------

variable "postgres_sku" {
  description = "SKU de PostgreSQL. B1ms = 1 vCPU / 2 GiB, el mas pequeno disponible."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "Almacenamiento en MB. 32768 (32 GiB) es el minimo que acepta Azure."
  type        = number
  default     = 32768

  validation {
    condition     = var.postgres_storage_mb >= 32768
    error_message = "Azure no permite menos de 32768 MB (32 GiB) en Flexible Server."
  }
}

variable "postgres_version" {
  description = "Version mayor de PostgreSQL. 16 para igualar el docker-compose local."
  type        = string
  default     = "16"
}

variable "postgres_admin_user" {
  description = "Usuario admin del servidor. Azure prohibe: azure_superuser, azure_pg_admin, admin, administrator, root, guest, public."
  type        = string
  default     = "pgadmin"
}

variable "postgres_db_name" {
  description = "Nombre de la base de datos de la aplicacion."
  type        = string
  default     = "plataforma_gestion"
}

variable "backup_retention_days" {
  description = "Retencion de backups. 7 dias es el minimo y esta incluido en el precio."
  type        = number
  default     = 7
}

variable "ip_desarrollador" {
  description = <<-EOT
    IP publica del desarrollador, para correr psql/pg_dump contra el servidor
    desde la maquina local (restaurar el backup, inspeccionar datos).
    Vacia = no se abre ninguna regla de firewall extra.
    Obtenerla con: curl -s https://api.ipify.org
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Container App
# ---------------------------------------------------------------------------

variable "imagen_contenedor" {
  description = <<-EOT
    Imagen a desplegar. En el PRIMER apply el ACR esta vacio, asi que se usa una
    imagen publica de arranque. A partir de ahi la imagen la gestiona el pipeline
    de CI/CD y Terraform IGNORA los cambios en este campo (ver containerapp.tf).
  EOT
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "min_replicas" {
  description = "0 = scale-to-zero. Ahorra ~15 USD/mes a cambio de arranque en frio de 10-30s tras inactividad."
  type        = number
  default     = 0
}

variable "max_replicas" {
  description = <<-EOT
    Techo de replicas. En 1 a proposito: con min=0 y max=1 NO hay autoescalado,
    la app esta apagada o hay exactamente una replica. Capacidad fija y factura
    predecible.

    Decision consciente de esperar a ver rendimiento real antes de escalar. Si
    la app se queda corta, subir esto a 2 es un cambio de una linea, pero
    reintroduce el autoescalado (Azure anade replica a las 10 peticiones
    concurrentes) y con el la parte variable de la factura.
  EOT
  type        = number
  default     = 1
}

variable "cpu" {
  description = "vCPU por replica. Combinaciones validas: 0.25/0.5Gi, 0.5/1Gi, 0.75/1.5Gi, 1/2Gi."
  type        = number
  default     = 0.5
}

variable "memoria" {
  description = "Memoria por replica. Debe ser el doble de cpu, en GiB."
  type        = string
  default     = "1Gi"
}

variable "gunicorn_workers" {
  description = <<-EOT
    Workers de gunicorn por replica: cuantas peticiones se atienden a la vez.

    En 3 para compensar la replica que ya no existe (max_replicas = 1). No
    cuesta nada: mismo CPU y memoria. Los workers sync pasan la mayor parte del
    tiempo bloqueados esperando a PostgreSQL, asi que tener mas que vCPUs es
    correcto para una app de este tipo.

    Limite: workers x max_replicas debe caber en las ~50 conexiones del B1ms.
    3 x 1 = 3. Hay muchisimo margen.
  EOT
  type        = number
  default     = 3
}

variable "log_retention_days" {
  description = "Retencion en Log Analytics. 30 dias es el minimo facturable."
  type        = number
  default     = 30
}

variable "log_cuota_diaria_gb" {
  description = <<-EOT
    Tope duro de ingesta diaria en GB.

    0.1 GB/dia = 3 GB/mes como maximo absoluto, por debajo de los 5 GB/mes de
    grant gratuito de Azure Monitor. Con eso los logs NO PUEDEN costar dinero,
    ni siquiera si la app entra en bucle de errores.

    Sigue siendo enorme para esta aplicacion: 100 MB de log al dia son decenas
    de miles de lineas. Si algun dia se alcanza el tope, la ingesta se detiene
    hasta el dia siguiente; eso ya seria sintoma de un problema que hay que
    mirar, no de una cuota mal puesta.
  EOT
  type        = number
  default     = 0.1
}

# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------

variable "django_allowed_hosts_extra" {
  description = "Hosts adicionales ademas del FQDN de la Container App (ej. un dominio propio)."
  type        = list(string)
  default     = []
}

variable "sap_validacion_estricta" {
  description = "Si true, los formatos SAP invalidos se rechazan al guardar en vez de solo avisar."
  type        = bool
  default     = false
}

variable "sso_habilitado" {
  description = <<-EOT
    Activa el boton 'Iniciar sesion con Microsoft'. El login local con
    usuario/contrasena sigue funcionando SIEMPRE: los dos conviven.
  EOT
  type        = bool
  default     = true
}

variable "sso_crear_usuarios" {
  description = <<-EOT
    true  = cualquier usuario del tenant entra y se le crea la cuenta al vuelo.
    false = solo entran usuarios que ya existan en Django (mas control; hay que
            precrear las cuentas o cargarlas con el importador masivo).
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# CI/CD
# ---------------------------------------------------------------------------

variable "github_repo" {
  description = "Repositorio 'owner/nombre' autorizado a desplegar via OIDC. Vacio = no crear la identidad federada."
  type        = string
  default     = "jobrrerac/plataforma-gestion"
}

variable "github_branch" {
  description = "Rama desde la que se permite desplegar."
  type        = string
  default     = "main"
}

variable "tags" {
  description = "Tags aplicados a todos los recursos."
  type        = map(string)
  default = {
    proyecto = "plataforma-gestion"
    gestion  = "terraform"
  }
}

variable "dominio_tenant" {
  description = "Dominio verificado del tenant. Es el que llevan los UPN de Entra."
  type        = string
  default     = "inetumoffshore.onmicrosoft.com"
}

variable "dominio_corporativo" {
  description = <<-EOT
    Dominio con el que la plataforma conoce a las personas (Recurso.email y el
    email de las cuentas de Django).

    El tenant no tiene verificado este dominio, asi que los UPN de Entra son
    `nombre@inetumoffshore.onmicrosoft.com` mientras que aqui la persona es
    `nombre@inetum.com`. La app traduce entre ambos al iniciar sesion
    (OIDC_DOMINIO_ALIAS); sin eso el SSO no reconoceria ninguna cuenta existente
    y crearia duplicados, dejando huerfano el historial de asignaciones.
  EOT
  type        = string
  default     = "inetum.com"
}

variable "roles_entra" {
  description = <<-EOT
    Rol de la aplicacion para cada persona del tenant, indexado por la parte
    local del UPN (sin dominio).

    Con app_role_assignment_required = true, quien no aparezca aqui NO puede
    iniciar sesion por SSO. Es el unico punto de control de acceso.

    Los valores por defecto reproducen los grupos que ya tienen estas personas
    en la base de datos actual, para que el SSO no altere permisos existentes.
    Quien no este en Django todavia se deja fuera a proposito: darle un rol es
    una decision, no un efecto secundario del despliegue.

    admin@ no va aqui: Terraform lo asigna como Admin por separado
    (ver azuread_app_role_assignment.admin_inicial) para que nunca haya bloqueo.
  EOT
  type        = map(string)
  default = {
    # Superusuario de la plataforma. Su UPN traduce a
    # jose.barrera-cocunubo@inetum.com, que es el email de `inetum_admin`, asi
    # que el SSO lo reconoce como esa cuenta y no crea una nueva.
    "jose.barrera-cocunubo" = "Admin"

    # PM (Leon-Rangel Carmen). Mismo rol que ya tiene en la plataforma.
    "carmen.leon" = "PM"

    # Cuenta de pruebas para validar el flujo de novedades con rol Ingeniero.
    # Su Recurso esta marcado como inactivo para no aparecer en la planificacion;
    # las novedades funcionan igual, no dependen de `activo`.
    "test.ingeniero" = "Ingeniero"

    # Cuentas para ejecutar el plan de pruebas (docs/QA_PLAN_PRUEBAS.md). Quien
    # prueba necesita los tres roles para recorrerlo entero, y usar personas
    # reales obligaria a cambiarles el rol de verdad.
    #
    # DESACTIVAR EN ENTRA AL TERMINAR LA RONDA DE QA: qa.admin puede aprobar y
    # revocar asignaciones reales.
    "qa.pm"    = "PM"
    "qa.admin" = "Admin"

    "erika.castiblanco-monroy"  = "Ingeniero"
    "sandra.chavarria-romero"   = "Ingeniero"
    "gustavo.villazon-gonzalez" = "Ingeniero"
    "ingrid.cespedes-diaz"      = "Ingeniero"
    "ivan.rodriguez-parra"      = "Ingeniero"
    "juan.murcia-sanchez"       = "Ingeniero"
    "juan.pena-ayala"           = "Ingeniero"
    "julian.vera-soto"          = "Ingeniero"
    "luisa.acosta-pelaez"       = "Ingeniero"
    "luisa.hernandez-serrato"   = "Ingeniero"
    "michael-steven.pinzon"     = "Ingeniero"
    "nicolas.paez-torres"       = "Ingeniero"
    "santiago.ariza-chinchilla" = "Ingeniero"
    "victor.ortega-soto"        = "Ingeniero"
    "wilson.alarcon-sierra"     = "Ingeniero"
    "yilma.espitia-sanabria"    = "Ingeniero"

    # Sin cuenta en la plataforma todavia. Descomentar cuando se decida su rol.
    # "diego.sautter"    = "Ingeniero"
    # "santiago.hernaiz" = "Ingeniero"
  }

  validation {
    condition     = alltrue([for r in values(var.roles_entra) : contains(["Admin", "PM", "Ingeniero"], r)])
    error_message = "Los roles validos son exactamente: Admin, PM, Ingeniero (deben coincidir con apps/accounts/roles.py)."
  }
}

variable "usuario_alias" {
  description = <<-EOT
    Equivalencias explicitas entre una identidad de Entra y una cuenta concreta
    de Django, para UPN que no derivan de ningun email de negocio.

    La cuenta administrativa del tenant debe entrar como el superusuario que ya
    existe en la plataforma, no como una cuenta nueva llamada "admin". Una
    cuenta enlazada asi conserva su email y su nombre: el alias significa "esta
    identidad ES esta cuenta", no "copiale los datos del token".

    Formato del mapa: upn_completo => username_de_django
  EOT
  type        = map(string)
  default = {
    "admin@inetumoffshore.onmicrosoft.com" = "inetum_admin"
  }
}

variable "abreviatura_region" {
  description = <<-EOT
    Abreviatura de la region para los nombres de recurso, siguiendo la
    convencion ya usada en la suscripcion (rg-sdlcagents-dev-eus2-001).
    eastus2 -> eus2.
  EOT
  type        = string
  default     = "eus2"
}

variable "instancia" {
  description = "Numero de instancia dentro del entorno, parte de la convencion de nombres (001)."
  type        = string
  default     = "001"

  validation {
    condition     = can(regex("^[0-9]{3}$", var.instancia))
    error_message = "Tres digitos, por ejemplo 001."
  }
}

variable "presupuesto_mensual_usd" {
  description = <<-EOT
    Techo mensual esperado, en USD, para el grupo de recursos. No corta nada:
    Azure no apaga recursos por superarlo, solo avisa. Sirve para enterarse de
    una desviacion en cuanto empieza, no al recibir la factura.

    Estimacion real: ~21 USD fijos (PostgreSQL + ACR) + 4-8 de uso. El valor por
    defecto deja margen para no disparar avisos por ruido.
  EOT
  type        = number
  default     = 40
}

variable "emails_alertas_costo" {
  description = <<-EOT
    Direcciones que reciben los avisos de presupuesto.

    Por defecto la cuenta administrativa del tenant. Si ese buzon no recibe
    correo de verdad (los tenants .onmicrosoft.com sin licencia de Exchange no
    lo hacen), poner aqui una direccion real o los avisos se pierden.
  EOT
  type        = list(string)
  default     = ["admin@inetumoffshore.onmicrosoft.com"]
}

variable "presupuesto_inicio" {
  description = "Inicio del periodo del presupuesto. Debe ser el dia 1 de un mes, y no puede estar en el pasado al crearlo."
  type        = string
  default     = "2026-08-01T00:00:00Z"
}

variable "meses_vigencia_secreto" {
  description = <<-EOT
    Vigencia del secreto de cliente del SSO, en meses. Azure admite hasta 24.

    24 y no 12 por una razon concreta: cuando caduque, es probable que quien
    monto esto ya no este. Azure NO avisa de la caducidad — ni correo, ni
    alerta, ni nada: el boton de Microsoft simplemente deja de funcionar. El
    aviso lo da la propia aplicacion (ver OIDC_SECRETO_CADUCA), y el login
    local sigue siendo la via de entrada mientras se rota.

    Rotarlo a proposito:
      terraform apply -replace=azuread_application_password.sso
  EOT
  type        = number
  default     = 24

  validation {
    condition     = var.meses_vigencia_secreto >= 1 && var.meses_vigencia_secreto <= 24
    error_message = "Azure admite entre 1 y 24 meses."
  }
}

variable "dias_aviso_caducidad_secreto" {
  description = "Con cuantos dias de antelacion la aplicacion empieza a avisar a los Admin de que el secreto caduca."
  type        = number
  default     = 60
}

variable "github_environment" {
  description = <<-EOT
    Entorno de GitHub que declara el job de despliegue.

    Importa porque cambia el subject del token OIDC: un job con `environment:`
    presenta `repo:owner/repo:environment:<nombre>` en vez de la rama. Si este
    valor no coincide con el del workflow, el despliegue falla con AADSTS700213
    antes de tocar nada.
  EOT
  type        = string
  default     = "produccion"
}
