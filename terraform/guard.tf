# ---------------------------------------------------------------------------
# Guard de suscripcion
# ---------------------------------------------------------------------------
# Tres capas, porque el modo mas facil de quemar dinero en Azure es aplicar
# Terraform contra la suscripcion equivocada sin darse cuenta:
#
#   1. providers.tf fija subscription_id/tenant_id explicitamente, en vez de
#      heredar lo que tenga `az account set`. Terraform apunta a la correcta
#      aunque el CLI este mirando otra.
#   2. variables.tf valida por comparacion literal que nadie sobreescriba esos
#      IDs desde un .tfvars o -var en linea de comandos.
#   3. Este archivo comprueba, en tiempo de plan, que las credenciales que
#      Terraform resolvio REALMENTE apuntan donde creemos. Es la unica capa que
#      detecta el caso "las variables dicen A pero el token vale para B".
#
# Las preconditions se evaluan en cada `plan` y `apply`, tambien cuando no hay
# cambios. Todo lo demas cuelga de azurerm_resource_group, que depende de este
# guard: si el guard falla, no se crea ni un recurso.
# ---------------------------------------------------------------------------

data "azurerm_client_config" "actual" {}

data "azuread_client_config" "actual" {}

data "azurerm_subscription" "actual" {
  subscription_id = var.subscription_id
}

resource "terraform_data" "guard_suscripcion" {
  # Fuerza reevaluacion si algo cambia bajo los pies.
  input = {
    subscription = data.azurerm_client_config.actual.subscription_id
    tenant       = data.azurerm_client_config.actual.tenant_id
  }

  lifecycle {
    precondition {
      condition     = data.azurerm_client_config.actual.subscription_id == var.subscription_id
      error_message = "Las credenciales resolvieron a la suscripcion ${data.azurerm_client_config.actual.subscription_id}, pero este proyecto solo despliega en ${var.subscription_id}. Ejecutar: az account set --subscription ${var.subscription_id}"
    }

    precondition {
      condition     = data.azurerm_client_config.actual.tenant_id == var.tenant_id
      error_message = "Las credenciales resolvieron al tenant ${data.azurerm_client_config.actual.tenant_id}, pero este proyecto solo despliega en ${var.tenant_id} (inetumoffshore.onmicrosoft.com). Ejecutar: az login --tenant ${var.tenant_id}"
    }

    precondition {
      condition     = data.azuread_client_config.actual.tenant_id == var.tenant_id
      error_message = "El proveedor azuread esta autenticado contra el tenant ${data.azuread_client_config.actual.tenant_id}. El App Registration del SSO debe crearse en ${var.tenant_id}."
    }

    precondition {
      condition     = data.azurerm_subscription.actual.state == "Enabled"
      error_message = "La suscripcion ${var.subscription_id} esta en estado '${data.azurerm_subscription.actual.state}', no 'Enabled'."
    }
  }
}
