terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.20"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Estado local por defecto (terraform.tfstate está en .gitignore).
  # El estado contiene la contraseña de PostgreSQL y el client secret de Entra
  # EN CLARO: nunca subirlo a git. Para trabajo en equipo, descomentar el
  # backend remoto y ejecutar antes `bootstrap_backend.sh`.
  #
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate-plataforma"
  #   storage_account_name = "sttfstateplatgestion"
  #   container_name       = "tfstate"
  #   key                  = "prod.terraform.tfstate"
  #   use_azuread_auth     = true
  # }
}
