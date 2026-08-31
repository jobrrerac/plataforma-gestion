# ---------------------------------------------------------------------------
# Identidad de despliegue para GitHub Actions (OIDC, sin secretos)
# ---------------------------------------------------------------------------
# GitHub intercambia su propio token OIDC por un token de Azure. No hay ningun
# secreto de larga vida guardado en el repositorio: nada que rotar y nada que
# filtrar. Solo la rama indicada puede desplegar.
#
# Coste: 0 USD. Las managed identities son gratuitas.

locals {
  cicd_activo = var.github_repo != ""
}

resource "azurerm_user_assigned_identity" "cicd" {
  count               = local.cicd_activo ? 1 : 0
  name                = local.nombres.identidad_cicd
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "github" {
  count                     = local.cicd_activo ? 1 : 0
  name                      = "github-${var.github_branch}"
  user_assigned_identity_id = azurerm_user_assigned_identity.cicd[0].id

  audience = ["api://AzureADTokenExchange"]
  issuer   = "https://token.actions.githubusercontent.com"

  # Ata la credencial a un repositorio y una rama concretos. Un fork o una rama
  # distinta no obtienen token.
  subject = "repo:${var.github_repo}:ref:refs/heads/${var.github_branch}"
}

# Segunda credencial para el mismo repositorio, con otro subject.
#
# No es redundante: cuando un job declara `environment:`, GitHub CAMBIA el
# subject del token OIDC. Deja de presentar la rama y presenta el entorno:
#
#   sin environment:  repo:owner/repo:ref:refs/heads/main
#   con environment:  repo:owner/repo:environment:produccion
#
# El workflow declara `environment: produccion` para poder exigir aprobacion
# manual antes de desplegar, asi que es esta la que se usa en la practica. La
# de la rama se conserva para `workflow_dispatch` y por si algun dia se quita
# el entorno. Faltando esta, el despliegue falla con AADSTS700213 en el primer
# paso, antes de tocar nada.
resource "azurerm_federated_identity_credential" "github_entorno" {
  count                     = local.cicd_activo ? 1 : 0
  name                      = "github-entorno-${var.github_environment}"
  user_assigned_identity_id = azurerm_user_assigned_identity.cicd[0].id

  audience = ["api://AzureADTokenExchange"]
  issuer   = "https://token.actions.githubusercontent.com"
  subject  = "repo:${var.github_repo}:environment:${var.github_environment}"
}

# Subir imagenes al registro. AcrPush no incluye borrar ni administrar el ACR.
resource "azurerm_role_assignment" "cicd_acr_push" {
  count                = local.cicd_activo ? 1 : 0
  scope                = azurerm_container_registry.principal.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.cicd[0].principal_id
}

# Actualizar la revision de la app. Contributor acotado al recurso concreto: no
# existe un rol integrado mas fino para Container Apps, pero el alcance impide
# tocar la base de datos, el registro o cualquier otra cosa del grupo.
resource "azurerm_role_assignment" "cicd_app" {
  count                = local.cicd_activo ? 1 : 0
  scope                = azurerm_container_app.principal.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.cicd[0].principal_id
}

# Lanzar el job de migraciones antes de publicar la revision.
resource "azurerm_role_assignment" "cicd_job" {
  count                = local.cicd_activo ? 1 : 0
  scope                = azurerm_container_app_job.migraciones.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.cicd[0].principal_id
}
