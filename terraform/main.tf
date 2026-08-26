# Sufijo estable para los nombres que deben ser unicos globalmente
# (ACR y el servidor de PostgreSQL comparten espacio de nombres con todo Azure).
# Se guarda en el estado, asi que no cambia entre applies.
resource "random_string" "sufijo" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  sufijo = random_string.sufijo.result

  # Convencion de nombres ya usada en la suscripcion:
  #   <tipo>-<workload>-<entorno>-<region>-<secuencia>
  # (ver rg-sdlcagents-dev-eus2-001, rg-prodbench-dev-eus2-001).
  # Mantenerla importa para el seguimiento de costos por grupo.
  base = "${var.proyecto}-${var.entorno}-${var.abreviatura_region}-${var.instancia}"

  nombres = {
    rg            = "rg-${local.base}"
    acr           = "acr${var.proyecto}${local.sufijo}" # sin guiones: ACR no los admite
    log_analytics = "log-${local.base}"
    entorno_apps  = "cae-${local.base}"
    app           = "ca-${local.base}"
    # Los nombres de Container App y de job estan limitados a 32 caracteres, por
    # eso el job no lleva sufijo descriptivo: el prefijo `caj` ya dice que es un
    # job, y es el unico que hay.
    job_migraciones = "caj-${local.base}"
    identidad       = "id-${local.base}"
    # ACR y PostgreSQL comparten espacio de nombres global: llevan el sufijo
    # aleatorio en vez del numero de instancia.
    postgres       = "psql-${var.proyecto}-${local.sufijo}"
    identidad_cicd = "id-${local.base}-cicd"
  }

  # El FQDN es predecible ANTES de crear la Container App:
  #   <nombre-app>.<default_domain-del-entorno>
  # Gracias a eso el App Registration de Entra puede crearse con el redirect URI
  # correcto sin dependencia circular app <-> registro.
  fqdn = "${local.nombres.app}.${azurerm_container_app_environment.principal.default_domain}"
  url  = "https://${local.fqdn}"

  allowed_hosts = join(",", concat([local.fqdn], var.django_allowed_hosts_extra))
  origins       = join(",", concat([local.url], [for h in var.django_allowed_hosts_extra : "https://${h}"]))

  tags = merge(var.tags, {
    entorno = var.entorno
  })
}

resource "azurerm_resource_group" "principal" {
  name     = local.nombres.rg
  location = var.location
  tags     = local.tags

  # Nada se crea si el guard no pasa.
  depends_on = [terraform_data.guard_suscripcion]
}

# ---------------------------------------------------------------------------
# Secretos generados
# ---------------------------------------------------------------------------
# Viven en el estado de Terraform EN CLARO. Por eso terraform.tfstate esta en
# .gitignore. Si el estado se mueve a un backend remoto, ese storage account
# debe tener acceso restringido.

resource "random_password" "postgres" {
  length  = 32
  special = true
  # Azure rechaza estos en la contrasena del admin de PostgreSQL.
  override_special = "!#%*()-_=+[]{}<>:?"
}

resource "random_password" "django_secret_key" {
  length  = 64
  special = true
  # Sin comillas ni barras: la clave viaja por variables de entorno.
  override_special = "!#%*()-_=+[]{}"
}
