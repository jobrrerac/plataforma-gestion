# ---------------------------------------------------------------------------
# PostgreSQL Flexible Server - Burstable B1ms (el escalon mas pequeno)
# ---------------------------------------------------------------------------
# 1 vCPU / 2 GiB / 32 GiB de disco. ~16 USD/mes, el grueso de la factura.
#
# Sin alta disponibilidad: el tier Burstable no la soporta. Habra ventanas de
# mantenimiento con reinicio. Para ~50 usuarios internos es aceptable; si deja
# de serlo, el salto es a General Purpose (D2ds_v5, ~4x el precio).

resource "azurerm_postgresql_flexible_server" "principal" {
  name                = local.nombres.postgres
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location

  version                = var.postgres_version
  sku_name               = var.postgres_sku
  storage_mb             = var.postgres_storage_mb
  administrator_login    = var.postgres_admin_user
  administrator_password = random_password.postgres.result

  backup_retention_days        = var.backup_retention_days
  geo_redundant_backup_enabled = false # duplicaria el coste del backup

  # Sin auto-grow: el disco solo crece hacia arriba y nunca baja de precio.
  # Con ~50 usuarios y datos de asignaciones, 32 GiB sobran de largo. Si se
  # llena, se sube a mano de forma consciente.
  auto_grow_enabled = false

  # Las dos autenticaciones a la vez, no una en lugar de la otra.
  #
  # Entra permite conectarse con un token en lugar de con la contrasena, que es
  # lo unico que hoy separa la base de cualquiera que llegue a ella. Pero
  # apagar la contrasena aqui dejaria la aplicacion sin poder entrar en el
  # mismo instante del apply: Django todavia se conecta con POSTGRES_PASSWORD.
  #
  # Asi que esto es el cimiento, no la mudanza. La contrasena se apaga el dia
  # que la aplicacion sepa pedir un token — y ese dia hay que apagarla, porque
  # mientras las dos esten abiertas la superficie es la de la mas debil.
  # → docs/DECISIONES_INFRA.md#autenticacion-de-la-base
  #
  # OJO: activar Entra habilita la extension PGAadAuth y **reinicia el
  # servidor**. Sin alta disponibilidad eso es un corte de un par de minutos.
  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
    tenant_id                     = data.azurerm_client_config.actual.tenant_id
  }

  # Acceso publico + firewall. La alternativa (inyeccion en VNet con acceso
  # privado) tambien es gratis y mas segura, pero deja la base inalcanzable
  # desde la maquina del desarrollador, y aqui hace falta restaurar el dump
  # inicial y poder inspeccionar datos. Ver docs/DESPLIEGUE_AZURE.md >
  # "Endurecer la red" para la migracion a VNet cuando compense.
  public_network_access_enabled = true

  tags = local.tags

  lifecycle {
    # Cambiar la zona obliga a recrear el servidor (y perder los datos).
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.postgres_db_name
  server_id = azurerm_postgresql_flexible_server.principal.id
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    # Evita que un `terraform destroy` accidental se lleve la base con datos.
    # Para un cambio que exija recrearla hay que ponerlo en false a proposito,
    # habiendo comprobado antes que existe un backup reciente.
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Administrador de Entra sobre la base
# ---------------------------------------------------------------------------
#
# Quien aplica este Terraform. No se fija a una persona por su object_id a
# mano: eso envejece en cuanto cambie quien administra, y deja un identificador
# de alguien real escrito en el repositorio.
#
# Para que una persona se conecte, el token va en el sitio de la contrasena:
#
#   export PGPASSWORD=$(az account get-access-token --resource-type oss-rdbms \
#                       --query accessToken -o tsv)
#   psql "host=... user=admin@inetumoffshore.onmicrosoft.com dbname=plataforma_gestion sslmode=require"
#
# El token dura entre 5 y 60 minutos: se pide justo antes de conectar y no se
# guarda en ningun script. Eso es precisamente lo que lo hace mejor que la
# contrasena compartida de `pgadmin`, que no caduca nunca.
#
# Si algun dia esto lo aplicara un service principal desde CI, la busqueda de
# `azuread_user` fallaria y habria que pasar a `azuread_service_principal`.
data "azuread_user" "admin_base" {
  object_id = data.azurerm_client_config.actual.object_id
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "principal" {
  server_name         = azurerm_postgresql_flexible_server.principal.name
  resource_group_name = azurerm_resource_group.principal.name
  tenant_id           = data.azurerm_client_config.actual.tenant_id
  object_id           = data.azuread_user.admin_base.object_id
  principal_name      = data.azuread_user.admin_base.user_principal_name
  principal_type      = "User"
}

# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

# 0.0.0.0-0.0.0.0 es el valor magico de Azure para "permitir servicios de Azure".
# La IP de salida de Container Apps en Consumption no es estatica, asi que no se
# puede fijar una regla por IP.
# → docs/DECISIONES_INFRA.md#firewall-de-la-base
resource "azurerm_postgresql_flexible_server_firewall_rule" "servicios_azure" {
  name             = "permitir-servicios-azure"
  server_id        = azurerm_postgresql_flexible_server.principal.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Acceso puntual desde la maquina del desarrollador (restaurar backup, psql).
resource "azurerm_postgresql_flexible_server_firewall_rule" "desarrollador" {
  count            = var.ip_desarrollador != "" ? 1 : 0
  name             = "desarrollador"
  server_id        = azurerm_postgresql_flexible_server.principal.id
  start_ip_address = var.ip_desarrollador
  end_ip_address   = var.ip_desarrollador
}

# ---------------------------------------------------------------------------
# Parametros del servidor
# ---------------------------------------------------------------------------

# TLS obligatorio. Viene activado por defecto, pero se declara para que quede
# explicito y para que un cambio manual en el portal se revierta en el siguiente
# apply. Django lo acompana con sslmode=require (ver settings/production.py).
resource "azurerm_postgresql_flexible_server_configuration" "tls_obligatorio" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "ON"
}

# Deja rastro de quien se conecta. Con la cuota de logs topada no supone coste.
resource "azurerm_postgresql_flexible_server_configuration" "log_conexiones" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "on"
}

# Frena los intentos repetidos de conexion fallidos desde la misma IP.
#
# Es la contrapartida directa de la regla 0.0.0.0 de aqui arriba: como esa
# regla deja que cualquier recurso de Azure —de cualquier suscripcion y de
# cualquier tenant— llegue al puerto 5432, lo unico que separa la base de un
# desconocido es la contrasena. Sin esto, probarla en bucle sale gratis.
#
# Viene apagado de fabrica. El parametro es dinamico, asi que activarlo no
# reinicia el servidor ni corta ninguna conexion viva.
resource "azurerm_postgresql_flexible_server_configuration" "freno_conexiones" {
  name      = "connection_throttle.enable"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "on"
}

# Extensiones permitidas. En Azure no basta con que la extension exista: hasta
# que no esta en esta lista, `CREATE EXTENSION` falla con "is not allow-listed
# for users in Azure Database for PostgreSQL" — y el parametro viene VACIO de
# fabrica, aunque `allowedValues` las liste todas. Mirar `allowedValues` y dar
# por hecho que estaba disponible fue lo que tumbo un despliegue.
#
# pg_trgm: busqueda por parecido de texto para los precedentes del triaje de
# horas (apps/revision/precedentes.py). El parametro es dinamico, asi que
# cambiarlo no reinicia el servidor.
resource "azurerm_postgresql_flexible_server_configuration" "extensiones" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "pg_trgm"
}

# Zona horaria del servidor alineada con Django (America/Bogota).
resource "azurerm_postgresql_flexible_server_configuration" "timezone" {
  name      = "timezone"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "America/Bogota"
}
