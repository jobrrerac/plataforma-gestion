# ---------------------------------------------------------------------------
# Azure Container Registry (Basic) + identidad de la aplicacion
# ---------------------------------------------------------------------------
# Basic: ~5 USD/mes, 10 GiB incluidos. Es el escalon mas barato de ACR.
#
# Alternativa gratuita: GitHub Container Registry (ghcr.io). Se descarto porque
# obliga a guardar un PAT de GitHub como secreto en la Container App; con ACR el
# pull se hace con managed identity y no hay ninguna credencial que rotar.
# Si 5 USD/mes llegan a importar, ver docs/DESPLIEGUE_AZURE.md > "Bajar a ghcr.io".

resource "azurerm_container_registry" "principal" {
  name                = local.nombres.acr
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location
  sku                 = "Basic"

  # Sin usuario admin: el acceso es solo por identidad/RBAC. Habilitarlo crearia
  # una credencial compartida de larga vida que no caduca ni se audita por usuario.
  admin_enabled = false

  tags = local.tags
}

# Identidad de la app. Se usa para hacer pull del ACR sin contrasenas.
# Se declara aparte de la Container App (en vez de usar una SystemAssigned) para
# poder asignarle el rol AcrPull ANTES de que la app exista: si la app arranca
# sin el rol, el primer pull falla y la revision queda en estado fallido.
resource "azurerm_user_assigned_identity" "app" {
  name                = local.nombres.identidad
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.principal.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
