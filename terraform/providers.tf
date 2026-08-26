# La suscripción se fija AQUÍ, no se hereda de `az account set`.
# Si tu CLI está apuntando a otra suscripción, Terraform igual trabaja contra
# la correcta (y guard.tf verifica que las credenciales tengan acceso a ella).
provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  features {
    resource_group {
      # No borrar un RG si quedaron recursos dentro: obliga a revisar qué falta.
      prevent_deletion_if_contains_resources = true
    }
  }
}

provider "azuread" {
  tenant_id = var.tenant_id
}

provider "random" {}
