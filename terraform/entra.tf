# ---------------------------------------------------------------------------
# Microsoft Entra ID - SSO
# ---------------------------------------------------------------------------
# Coste: 0 USD. El SSO de una aplicacion propia registrada en el tenant esta
# incluido en el tier Free de Entra ID. Solo se paga (P1, ~6 USD/usuario/mes) si
# se quieren politicas de Acceso Condicional o MFA forzada sobre la app, que
# aqui NO se activan por decision explicita: el objetivo es no memorizar varias
# contrasenas, no anadir un segundo factor.
#
# El login local de Django sigue funcionando en paralelo (ver sso_habilitado).

resource "random_uuid" "rol_admin" {}
resource "random_uuid" "rol_pm" {}
resource "random_uuid" "rol_ingeniero" {}

resource "azuread_application" "sso" {
  display_name = "Plataforma Gestion de Recursos (${var.entorno})"

  # Solo cuentas de este tenant. Sin invitados externos ni cuentas personales.
  sign_in_audience = "AzureADMyOrg"

  owners = [data.azuread_client_config.actual.object_id]

  web {
    redirect_uris = [
      "${local.url}/oidc/callback/",
    ]

    # Flujo de codigo de autorizacion puro. Sin tokens implicitos: estan
    # deprecados y exponen el token en la URL.
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = false
    }
  }

  # Los tres roles del proyecto, emitidos en el claim `roles` del id_token.
  # Django los mapea a sus grupos homonimos en cada login
  # (ver apps/accounts/oidc.py). Los nombres DEBEN coincidir con
  # apps/accounts/roles.py: Admin / PM / Ingeniero.
  app_role {
    id                   = random_uuid.rol_admin.result
    value                = "Admin"
    display_name         = "Admin"
    description          = "Administracion completa, incluidos costos y tarifas."
    allowed_member_types = ["User"]
    enabled              = true
  }

  app_role {
    id                   = random_uuid.rol_pm.result
    value                = "PM"
    display_name         = "PM"
    description          = "Gestion de proyectos y asignaciones. Ve costos y tarifas."
    allowed_member_types = ["User"]
    enabled              = true
  }

  app_role {
    id                   = random_uuid.rol_ingeniero.result
    value                = "Ingeniero"
    display_name         = "Ingeniero"
    description          = "Consulta de sus propias asignaciones. NUNCA ve costos."
    allowed_member_types = ["User"]
    enabled              = true
  }

  # Permisos delegados minimos: los tres scopes de OpenID Connect y nada mas.
  #
  # No se pide User.Read: la aplicacion nunca llama a Microsoft Graph. La
  # identidad y los roles se leen del propio id_token, ya firmado y verificado
  # contra el JWKS del tenant (ver apps/accounts/oidc.py > get_userinfo). Pedir
  # acceso al directorio seria pedir permiso para algo que no se hace.
  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "37f7f235-527c-4136-accd-4a02d197296e" # openid
      type = "Scope"
    }
    resource_access {
      id   = "14dad69e-099b-42c9-810b-d002981feec1" # profile
      type = "Scope"
    }
    resource_access {
      id   = "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0" # email
      type = "Scope"
    }
  }

  optional_claims {
    id_token {
      name      = "email"
      essential = false
    }
    id_token {
      name      = "upn"
      essential = false
    }
  }
}

resource "azuread_service_principal" "sso" {
  client_id = azuread_application.sso.client_id
  owners    = [data.azuread_client_config.actual.object_id]

  # Solo entran usuarios con un rol asignado explicitamente. Sin esto, cualquier
  # cuenta del tenant podria iniciar sesion. La asignacion de roles se vuelve el
  # unico punto de control de acceso, y se hace desde Entra ID > Aplicaciones
  # empresariales > esta app > Usuarios y grupos.
  app_role_assignment_required = true

  feature_tags {
    enterprise = true
  }
}

# Evita el bloqueo total: quien despliega queda asignado como Admin. Sin esto,
# con app_role_assignment_required = true nadie podria entrar por SSO hasta
# asignar roles a mano, incluido el propio administrador.
resource "azuread_app_role_assignment" "admin_inicial" {
  app_role_id         = random_uuid.rol_admin.result
  principal_object_id = data.azuread_client_config.actual.object_id
  resource_object_id  = azuread_service_principal.sso.object_id
}

# Secreto de cliente. Caduca en 1 ano: anotar la fecha, porque el dia que expire
# el SSO deja de funcionar sin previo aviso (el login local seguira sirviendo
# como via de entrada mientras se rota).
resource "azuread_application_password" "sso" {
  application_id = azuread_application.sso.id
  display_name   = "terraform-${var.entorno}"
  end_date       = timeadd(timestamp(), "8760h") # 1 ano

  lifecycle {
    # timestamp() cambia en cada plan. Sin esto, Terraform querria recrear el
    # secreto en cada apply y el SSO se caeria hasta el siguiente despliegue.
    # Para rotarlo a proposito: terraform apply -replace=azuread_application_password.sso
    ignore_changes = [end_date]
  }
}

# ---------------------------------------------------------------------------
# Asignacion de roles a las personas del tenant
# ---------------------------------------------------------------------------
# Con app_role_assignment_required = true esta es la puerta de entrada: quien no
# tenga una asignacion aqui no puede iniciar sesion por SSO.
#
# Se declara en Terraform en vez de hacerse a mano en el portal para que quede
# revisable en el repositorio y para que reproducir el entorno no dependa de que
# alguien recuerde 15 clics.

locals {
  # Las claves deben coincidir con los `value` de los app_role de arriba y con
  # los nombres de grupo de apps/accounts/roles.py.
  ids_roles = {
    "Admin"     = random_uuid.rol_admin.result
    "PM"        = random_uuid.rol_pm.result
    "Ingeniero" = random_uuid.rol_ingeniero.result
  }
}

# Falla el plan si alguien de la lista no existe en el tenant, en vez de
# descubrirlo cuando esa persona no pueda entrar.
data "azuread_user" "asignados" {
  for_each            = var.roles_entra
  user_principal_name = "${each.key}@${var.dominio_tenant}"
}

resource "azuread_app_role_assignment" "usuarios" {
  for_each = var.roles_entra

  app_role_id         = local.ids_roles[each.value]
  principal_object_id = data.azuread_user.asignados[each.key].object_id
  resource_object_id  = azuread_service_principal.sso.object_id
}

# ---------------------------------------------------------------------------
# Consentimiento de administrador
# ---------------------------------------------------------------------------
# Sin esto, cada persona ve al entrar la pantalla "Se necesita la aprobacion del
# administrador" y no puede continuar: este tenant no permite que los usuarios
# consientan aplicaciones por su cuenta.
#
# Se concede aqui, en Terraform, y no con un clic en el portal, para que
# reconstruir el entorno no deje a todo el mundo bloqueado en esa pantalla.
#
# El alcance es el minimo posible: los tres scopes de OpenID Connect, que solo
# sirven para saber quien es la persona. Ningun permiso de lectura del
# directorio.

data "azuread_service_principal" "msgraph" {
  client_id = "00000003-0000-0000-c000-000000000000"
}

resource "azuread_service_principal_delegated_permission_grant" "consentimiento" {
  service_principal_object_id          = azuread_service_principal.sso.object_id
  resource_service_principal_object_id = data.azuread_service_principal.msgraph.object_id
  claim_values                         = ["openid", "profile", "email"]
}
