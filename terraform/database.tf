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
# Firewall
# ---------------------------------------------------------------------------

# El rango 0.0.0.0-0.0.0.0 es el valor magico de Azure para "permitir servicios
# de Azure". Hace falta porque la IP de salida de un entorno de Container Apps
# en plan Consumption NO es estatica: Microsoft documenta que puede cambiar, asi
# que no se puede fijar una regla por IP. Las alternativas con IP fija (NAT
# Gateway sobre workload profiles, plan Dedicated) cuestan mas de 30 USD/mes,
# mas que todo el resto de la infraestructura junta.
#
# Lo que protege la base mientras tanto:
#   - require_secure_transport = ON (TLS obligatorio, abajo)
#   - contrasena aleatoria de 32 caracteres, nunca escrita a mano
#   - la base no expone datos sin autenticar
#
# Endurecimiento real cuando el presupuesto lo permita: inyeccion en VNet.
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

# Zona horaria del servidor alineada con Django (America/Bogota).
resource "azurerm_postgresql_flexible_server_configuration" "timezone" {
  name      = "timezone"
  server_id = azurerm_postgresql_flexible_server.principal.id
  value     = "America/Bogota"
}
