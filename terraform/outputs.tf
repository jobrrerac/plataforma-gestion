output "url_aplicacion" {
  description = "URL publica de la aplicacion."
  value       = local.url
}

output "resource_group" {
  description = "Grupo de recursos creado."
  value       = azurerm_resource_group.principal.name
}

# ---------------------------------------------------------------------------
# Registro y despliegue
# ---------------------------------------------------------------------------

output "acr_login_server" {
  description = "Servidor del registro (destino de docker push)."
  value       = azurerm_container_registry.principal.login_server
}

output "acr_nombre" {
  description = "Nombre del ACR, para `az acr login --name`."
  value       = azurerm_container_registry.principal.name
}

output "container_app_nombre" {
  description = "Nombre de la Container App, para `az containerapp update`."
  value       = azurerm_container_app.principal.name
}

output "job_migraciones_nombre" {
  description = "Job que ejecuta migrate + createcachetable + setup_grupos."
  value       = azurerm_container_app_job.migraciones.name
}

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

output "postgres_fqdn" {
  description = "Host del servidor PostgreSQL."
  value       = azurerm_postgresql_flexible_server.principal.fqdn
}

output "postgres_usuario" {
  description = "Usuario administrador de PostgreSQL."
  value       = azurerm_postgresql_flexible_server.principal.administrator_login
}

# La contrasena existe pero el servidor YA NO LA ACEPTA: `password_auth_enabled`
# esta en false. Se conserva —no se borra el `random_password`— porque volver a
# abrir la puerta es poner ese flag en true y aplicar, sin generar credenciales
# nuevas ni tocar la base. Mientras tanto es una cadena inerte.
output "postgres_password" {
  description = "Contrasena de PostgreSQL. INERTE: el servidor solo acepta Entra."
  value       = random_password.postgres.result
  sensitive   = true
}

# Con la contrasena apagada, la unica forma de entrar es con un token, y el
# token va en el sitio de la contrasena. Dura entre 5 y 60 minutos: se pide
# justo antes de conectar y no se guarda en ningun script.
output "postgres_cadena_psql" {
  description = "Como conectarse con psql usando Entra (requiere ip_desarrollador en el firewall)."
  value       = <<-EOT
    export PGPASSWORD=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)
    psql "host=${azurerm_postgresql_flexible_server.principal.fqdn} port=5432 dbname=${var.postgres_db_name} user=$(az ad signed-in-user show --query userPrincipalName -o tsv) sslmode=require"
  EOT
}

# ---------------------------------------------------------------------------
# Entra ID / SSO
# ---------------------------------------------------------------------------

output "entra_client_id" {
  description = "Client ID (application ID) del App Registration del SSO."
  value       = azuread_application.sso.client_id
}

output "entra_client_secret" {
  description = "Secreto de cliente del SSO. Ver con: terraform output -raw entra_client_secret"
  value       = azuread_application_password.sso.value
  sensitive   = true
}

output "entra_redirect_uri" {
  description = "Redirect URI registrado. Debe coincidir con el callback de la app."
  value       = "${local.url}/oidc/callback/"
}

output "entra_caducidad_secreto" {
  description = "Fecha en la que caduca el secreto del SSO. Rotarlo antes o el login con Microsoft dejara de funcionar."
  value       = azuread_application_password.sso.end_date
}

output "entra_url_asignar_roles" {
  description = "Pantalla de Entra para asignar los roles Admin / PM / Ingeniero a los usuarios."
  value       = "https://portal.azure.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Users/objectId/${azuread_service_principal.sso.object_id}/appId/${azuread_application.sso.client_id}"
}

# ---------------------------------------------------------------------------
# CI/CD - valores para los secrets del repositorio de GitHub
# ---------------------------------------------------------------------------

output "cicd_client_id" {
  description = "AZURE_CLIENT_ID para GitHub Actions."
  value       = local.cicd_activo ? azurerm_user_assigned_identity.cicd[0].client_id : null
}

output "cicd_tenant_id" {
  description = "AZURE_TENANT_ID para GitHub Actions."
  value       = var.tenant_id
}

output "cicd_subscription_id" {
  description = "AZURE_SUBSCRIPTION_ID para GitHub Actions."
  value       = var.subscription_id
}

output "cicd_comandos_gh" {
  description = "Comandos para cargar los secrets en GitHub de una vez."
  value = local.cicd_activo ? join("\n", [
    "gh secret set AZURE_CLIENT_ID --repo ${var.github_repo} --body ${azurerm_user_assigned_identity.cicd[0].client_id}",
    "gh secret set AZURE_TENANT_ID --repo ${var.github_repo} --body ${var.tenant_id}",
    "gh secret set AZURE_SUBSCRIPTION_ID --repo ${var.github_repo} --body ${var.subscription_id}",
    "gh variable set AZURE_RESOURCE_GROUP --repo ${var.github_repo} --body ${azurerm_resource_group.principal.name}",
    "gh variable set ACR_NAME --repo ${var.github_repo} --body ${azurerm_container_registry.principal.name}",
    "gh variable set CONTAINER_APP_NAME --repo ${var.github_repo} --body ${azurerm_container_app.principal.name}",
    "gh variable set MIGRATIONS_JOB_NAME --repo ${var.github_repo} --body ${azurerm_container_app_job.migraciones.name}",
  ]) : null
}

# ---------------------------------------------------------------------------
# Costos
# ---------------------------------------------------------------------------

output "costo_url_analisis" {
  description = "Cost Analysis del portal, ya filtrado a este grupo de recursos."
  value       = "https://portal.azure.com/#@${var.tenant_id}/resource${azurerm_resource_group.principal.id}/costanalysis"
}

output "costo_comando_mes_actual" {
  description = "Gasto del mes en curso, desglosado por recurso, desde la terminal."
  value       = "az consumption usage list --start-date $(date +%Y-%m-01) --end-date $(date +%Y-%m-%d) --query \"[?contains(instanceName,'${var.proyecto}')].{recurso:instanceName,costo:pretaxCost}\" -o table"
}
