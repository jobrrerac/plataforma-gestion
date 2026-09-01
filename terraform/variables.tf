# Variables de la infraestructura.
#
# El porque de cada valor esta en docs/DECISIONES_INFRA.md. Aqui solo va que es
# cada cosa, para poder encontrar una variable de un vistazo.

# ===========================================================================
# Identidad de la suscripcion
# ===========================================================================
# Fijadas a proposito. La cuenta ve cuatro suscripciones en tres tenants y
# desplegar en la equivocada no falla: crea recursos que alguien paga.
# → docs/DECISIONES_INFRA.md#suscripcion-y-tenant-fijados  ·  ver guard.tf

variable "subscription_id" {
  description = "Suscripcion destino. Solo 'Azure subscription 1' del tenant inetumoffshore."
  type        = string
  default     = "b383e51f-9354-4d6a-8d3b-cc9abb1b9743"

  validation {
    condition     = var.subscription_id == "b383e51f-9354-4d6a-8d3b-cc9abb1b9743"
    error_message = <<-EOT
      Suscripcion no permitida.

      Este proyecto SOLO puede desplegarse en 'Azure subscription 1'
      (b383e51f-9354-4d6a-8d3b-cc9abb1b9743), tenant inetumoffshore.onmicrosoft.com.

      Hay otras suscripciones visibles en la cuenta que viven en tenants
      distintos; desplegar ahi generaria costos en la cuenta equivocada.
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

# ===========================================================================
# Nombres y ubicacion
# ===========================================================================
# Convencion de la suscripcion: <tipo>-<proyecto>-<entorno>-<region>-<instancia>

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
  # La mas barata con Container Apps + Postgres Burstable desde Colombia.
  # → docs/DECISIONES_INFRA.md#region-eastus2
  description = "Region de Azure."
  type        = string
  default     = "eastus2"
}

variable "abreviatura_region" {
  description = "Abreviatura de la region en los nombres de recurso (eastus2 -> eus2)."
  type        = string
  default     = "eus2"
}

variable "instancia" {
  description = "Numero de instancia dentro del entorno (001)."
  type        = string
  default     = "001"

  validation {
    condition     = can(regex("^[0-9]{3}$", var.instancia))
    error_message = "Tres digitos, por ejemplo 001."
  }
}

variable "tags" {
  description = "Tags aplicados a todos los recursos."
  type        = map(string)
  default = {
    proyecto = "plataforma-gestion"
    gestion  = "terraform"
  }
}

# ===========================================================================
# Base de datos
# ===========================================================================
# El escalon mas pequeno que vende Azure; los minimos son minimos de verdad.
# → docs/DECISIONES_INFRA.md#postgres-b1ms

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
  # Azure prohibe: azure_superuser, azure_pg_admin, admin, administrator, root,
  # guest, public.
  description = "Usuario admin del servidor."
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
  # Vacia = no se abre ninguna regla. Obtenerla: curl -s https://api.ipify.org
  # → docs/DECISIONES_INFRA.md#ip-desarrollador
  description = "IP publica desde la que permitir psql/pg_dump. Vacia para no abrir nada."
  type        = string
  default     = ""
}

# ===========================================================================
# Container App
# ===========================================================================

variable "imagen_contenedor" {
  # Solo para el primer apply, con el ACR aun vacio. Despues la gestiona CI/CD y
  # Terraform ignora este campo (ver containerapp.tf).
  # → docs/DECISIONES_INFRA.md#imagen-de-arranque
  description = "Imagen de arranque inicial. La real la publica el pipeline."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "min_replicas" {
  description = "0 = scale-to-zero. Ahorra ~15 USD/mes a cambio de arranque en frio de 10-30s."
  type        = number
  default     = 0
}

variable "max_replicas" {
  # Con min=0 y max=1 NO hay autoescalado: capacidad fija y factura predecible.
  # Subirlo a 2 lo reintroduce.
  # → docs/DECISIONES_INFRA.md#sin-autoescalado
  description = "Techo de replicas. En 1 a proposito, sin autoescalado."
  type        = number
  default     = 1
}

variable "cpu" {
  description = "vCPU por replica. Validas: 0.25/0.5Gi, 0.5/1Gi, 0.75/1.5Gi, 1/2Gi."
  type        = number
  default     = 0.5
}

variable "memoria" {
  description = "Memoria por replica. Debe ser el doble de cpu, en GiB."
  type        = string
  default     = "1Gi"
}

variable "gunicorn_workers" {
  # → docs/DECISIONES_INFRA.md#gunicorn-workers
  description = "Peticiones concurrentes por replica. 3 compensa la replica que no hay."
  type        = number
  default     = 3
}

# ===========================================================================
# Logs
# ===========================================================================

variable "log_retention_days" {
  description = "Retencion en Log Analytics. 30 dias es el minimo facturable."
  type        = number
  default     = 30
}

variable "log_cuota_diaria_gb" {
  # Tope duro: por debajo del grant gratuito, asi que los logs no pueden costar
  # dinero ni con la app en bucle de errores.
  # → docs/DECISIONES_INFRA.md#cuota-de-logs
  description = "Tope duro de ingesta diaria en GB."
  type        = number
  default     = 0.1
}

# ===========================================================================
# Coste
# ===========================================================================

variable "presupuesto_mensual_usd" {
  # No corta nada: Azure solo avisa. Real: ~21 USD fijos + 4-8 de uso.
  # → docs/DECISIONES_INFRA.md#presupuesto
  description = "Techo mensual esperado en USD. Solo dispara avisos, no apaga nada."
  type        = number
  default     = 40
}

variable "emails_alertas_costo" {
  # Ojo: un tenant .onmicrosoft.com sin Exchange NO recibe correo.
  # → docs/DECISIONES_INFRA.md#emails-de-alerta
  description = "Direcciones que reciben los avisos de presupuesto."
  type        = list(string)
  default     = ["admin@inetumoffshore.onmicrosoft.com"]
}

variable "presupuesto_inicio" {
  description = "Inicio del periodo. Dia 1 de un mes, y no puede estar en el pasado al crearlo."
  type        = string
  default     = "2026-08-01T00:00:00Z"
}

# ===========================================================================
# Aplicacion
# ===========================================================================

variable "django_allowed_hosts_extra" {
  description = "Hosts adicionales ademas del FQDN de la Container App."
  type        = list(string)
  default     = []
}

variable "sap_validacion_estricta" {
  description = "Si true, un formato SAP invalido se rechaza al guardar en vez de solo avisar."
  type        = bool
  default     = false
}

# ===========================================================================
# SSO con Entra ID
# ===========================================================================
# El login local y el SSO conviven siempre: el local es el plan de contingencia
# cuando el secreto de Entra caduca.

variable "sso_habilitado" {
  description = "Activa el boton 'Iniciar sesion con Microsoft'."
  type        = bool
  default     = true
}

variable "sso_crear_usuarios" {
  # false = solo entran cuentas que ya existan en Django.
  description = "Si true, a quien entre por SSO se le crea la cuenta al vuelo."
  type        = bool
  default     = true
}

variable "dominio_tenant" {
  description = "Dominio verificado del tenant. Es el que llevan los UPN de Entra."
  type        = string
  default     = "inetumoffshore.onmicrosoft.com"
}

variable "dominio_corporativo" {
  # El tenant no lo tiene verificado, asi que la app traduce entre ambos al
  # entrar. Sin esa traduccion el SSO duplicaria cada cuenta, sin dar error.
  # → docs/DECISIONES_INFRA.md#dos-dominios
  description = "Dominio con el que la plataforma conoce a las personas."
  type        = string
  default     = "inetum.com"
}

variable "usuario_alias" {
  # Para UPN que no derivan de ningun email de negocio. Formato:
  # upn_completo => username_de_django
  # → docs/DECISIONES_INFRA.md#usuario-alias
  description = "Identidades de Entra que SON una cuenta concreta de Django."
  type        = map(string)
  default = {
    "admin@inetumoffshore.onmicrosoft.com" = "inetum_admin"
  }
}

variable "meses_vigencia_secreto" {
  # 24 y no 12 porque cuando caduque probablemente ya no este quien lo monto, y
  # Azure no avisa. Rotar: terraform apply -replace=azuread_application_password.sso
  # → docs/DECISIONES_INFRA.md#vigencia-del-secreto
  description = "Vigencia del secreto de cliente del SSO, en meses. Azure admite hasta 24."
  type        = number
  default     = 24

  validation {
    condition     = var.meses_vigencia_secreto >= 1 && var.meses_vigencia_secreto <= 24
    error_message = "Azure admite entre 1 y 24 meses."
  }
}

variable "dias_aviso_caducidad_secreto" {
  description = "Antelacion con la que la aplicacion avisa a los Admin de la caducidad."
  type        = number
  default     = 60
}

# ===========================================================================
# Acceso: quien entra y con que rol
# ===========================================================================
# Con app_role_assignment_required = true, quien no este aqui NO entra por SSO.
# Es el control de acceso, no una lista de permisos.
# → docs/DECISIONES_INFRA.md#roles-entra

variable "roles_entra" {
  description = "Rol de cada persona, indexado por la parte local del UPN."
  type        = map(string)
  default = {
    # Superusuario de la plataforma (entra como `inetum_admin`, ver usuario_alias).
    "jose.barrera-cocunubo" = "Admin"

    "carmen.leon" = "PM"

    # Cuenta de pruebas del flujo de novedades. Su Recurso esta inactivo para no
    # aparecer en la planificacion.
    "test.ingeniero" = "Ingeniero"

    # Cuentas para el plan de pruebas (docs/QA_PLAN_PRUEBAS.md).
    # DESACTIVAR EN ENTRA AL TERMINAR LA RONDA: qa.admin aprueba y revoca de verdad.
    "qa.pm"    = "PM"
    "qa.admin" = "Admin"

    "daniel.guzman"             = "Ingeniero"
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

    "andres.moreno-suarez"        = "Ingeniero"
    "andres.vasquez-acosta"       = "Ingeniero"
    "daniel.florez-miranda"       = "Ingeniero"
    "daniel.martinez-forero"      = "Ingeniero"
    "jhonathan.gutierrez-meneses" = "Ingeniero"
    "jose-joaquin.guevara"        = "Ingeniero"
    "laura.paiba-celeita"         = "Ingeniero"
    "martin.medina-novoa"         = "Ingeniero"
    "santiago.martinez-herrera"   = "Ingeniero"
    "william.franco"              = "Ingeniero"

    # diego.sautter y santiago.hernaiz siguen sin cuenta en la plataforma.
    # Darles rol es una decision, no un efecto secundario del despliegue.
  }

  validation {
    condition     = alltrue([for r in values(var.roles_entra) : contains(["Admin", "PM", "Ingeniero"], r)])
    error_message = "Los roles validos son exactamente: Admin, PM, Ingeniero (deben coincidir con apps/accounts/roles.py)."
  }
}

variable "invitados_b2b" {
  # Un invitado tiene DOS identidades y el rol vive en cada una por separado.
  # Sin esta lista, cambiar su rol en roles_entra no le afecta y Terraform dice
  # que todo esta al dia.
  # → docs/DECISIONES_INFRA.md#invitados-b2b
  description = "Quien entra por B2B con su cuenta corporativa. El rol sale de roles_entra."
  type        = set(string)
  default = [
    # Las 28 del alta masiva, invitadas el 31/08/2026 para que entren con su
    # cuenta corporativa y sin una segunda contrasena. El rol de cada una sale
    # de `roles_entra`, no de aqui.
    "andres.moreno-suarez",
    "andres.vasquez-acosta",
    "carmen.leon",
    "daniel.florez-miranda",
    "daniel.guzman",
    "daniel.martinez-forero",
    "erika.castiblanco-monroy",
    "gustavo.villazon-gonzalez",
    "ingrid.cespedes-diaz",
    "ivan.rodriguez-parra",
    "jhonathan.gutierrez-meneses",
    "jose-joaquin.guevara",
    "juan.murcia-sanchez",
    "juan.pena-ayala",
    "julian.vera-soto",
    "laura.paiba-celeita",
    "luisa.acosta-pelaez",
    "luisa.hernandez-serrato",
    "martin.medina-novoa",
    "michael-steven.pinzon",
    "nicolas.paez-torres",
    "sandra.chavarria-romero",
    "santiago.ariza-chinchilla",
    "santiago.martinez-herrera",
    "victor.ortega-soto",
    "william.franco",
    "wilson.alarcon-sierra",
    "yilma.espitia-sanabria",
  ]

  validation {
    condition     = length(var.invitados_b2b) == length(setintersection(var.invitados_b2b, keys(var.roles_entra)))
    error_message = "Todo invitado B2B tiene que tener rol en roles_entra."
  }
}

# ===========================================================================
# CI/CD
# ===========================================================================

variable "github_repo" {
  description = "Repositorio 'owner/nombre' autorizado a desplegar via OIDC. Vacio = sin identidad federada."
  type        = string
  default     = "jobrrerac/plataforma-gestion"
}

variable "github_branch" {
  description = "Rama desde la que se permite desplegar."
  type        = string
  default     = "main"
}

variable "github_environment" {
  # Cambia el subject del token OIDC. Si no coincide con el workflow, el
  # despliegue falla con AADSTS700213 antes de tocar nada.
  # → docs/DECISIONES_INFRA.md#github-environment
  description = "Entorno de GitHub que declara el job de despliegue."
  type        = string
  default     = "produccion"
}
