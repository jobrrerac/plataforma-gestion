# ---------------------------------------------------------------------------
# Log Analytics
# ---------------------------------------------------------------------------
# El entorno de Container Apps necesita un destino de logs. La opcion "ninguno"
# existe y es gratis, pero deja la app sin forma de diagnosticar por que fallo
# un arranque, que es exactamente cuando hacen falta.
#
# Coste real esperado: 0 USD. Los primeros 5 GB/mes de ingesta son gratuitos y
# esta app, con ~50 usuarios, no se acerca. La cuota diaria es un tope duro por
# si algun dia entra en bucle de logs.

resource "azurerm_log_analytics_workspace" "principal" {
  name                = local.nombres.log_analytics
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.log_cuota_diaria_gb

  tags = local.tags
}
