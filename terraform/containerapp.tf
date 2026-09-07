# ---------------------------------------------------------------------------
# Container Apps - entorno, aplicacion y job de migraciones
# ---------------------------------------------------------------------------
# El entorno en plan Consumption no tiene coste fijo: se paga solo por
# vCPU-segundo y GiB-segundo consumidos, y los primeros 180.000 vCPU-s /
# 360.000 GiB-s / 2M peticiones al mes son gratuitos por suscripcion.
# Con min_replicas = 0 la app se apaga sola cuando nadie la usa.

resource "azurerm_container_app_environment" "principal" {
  name                       = local.nombres.entorno_apps
  resource_group_name        = azurerm_resource_group.principal.name
  location                   = azurerm_resource_group.principal.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.principal.id

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Configuracion compartida entre la app y el job de migraciones
# ---------------------------------------------------------------------------

locals {
  secretos = [
    {
      name  = "django-secret-key"
      value = random_password.django_secret_key.result
    },
    {
      name  = "postgres-password"
      value = random_password.postgres.result
    },
    {
      name  = "oidc-client-secret"
      value = azuread_application_password.sso.value
    },
  ]

  # Variables de entorno que consumen ambos contenedores. Las que llevan
  # `secret_name` se resuelven desde los secretos de la Container App y nunca
  # aparecen en la definicion del recurso ni en los logs.
  env_comunes = [
    { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.production" },
    { name = "DJANGO_DEBUG", value = "False" },
    { name = "DJANGO_SECRET_KEY", secret_name = "django-secret-key" },
    { name = "DJANGO_ALLOWED_HOSTS", value = local.allowed_hosts },
    { name = "CSRF_TRUSTED_ORIGINS", value = local.origins },
    { name = "CORS_ALLOWED_ORIGINS", value = local.origins },

    { name = "POSTGRES_DB", value = var.postgres_db_name },
    { name = "POSTGRES_USER", value = var.postgres_admin_user },
    { name = "POSTGRES_PASSWORD", secret_name = "postgres-password" },
    { name = "POSTGRES_HOST", value = azurerm_postgresql_flexible_server.principal.fqdn },
    { name = "POSTGRES_PORT", value = "5432" },
    # Azure exige TLS. Sin esto psycopg no negocia SSL y la conexion se rechaza.
    { name = "POSTGRES_SSLMODE", value = "require" },

    { name = "SAP_VALIDACION_ESTRICTA", value = var.sap_validacion_estricta ? "True" : "False" },

    # SSO. Con OIDC_HABILITADO=False la app funciona solo con login local.
    { name = "OIDC_HABILITADO", value = var.sso_habilitado ? "True" : "False" },
    { name = "OIDC_TENANT_ID", value = var.tenant_id },
    { name = "OIDC_RP_CLIENT_ID", value = azuread_application.sso.client_id },
    { name = "OIDC_RP_CLIENT_SECRET", secret_name = "oidc-client-secret" },
    { name = "OIDC_CREAR_USUARIOS", value = var.sso_crear_usuarios ? "True" : "False" },
    # Traduce el UPN de Entra al email corporativo con el que la plataforma
    # conoce a cada persona. Sin esto el SSO no reconoceria ninguna de las
    # cuentas existentes y crearia duplicados.
    { name = "OIDC_DOMINIO_ALIAS", value = "${var.dominio_tenant}=${var.dominio_corporativo}" },
    # Identidades de Entra que apuntan a una cuenta concreta de Django.
    { name = "OIDC_USUARIO_ALIAS", value = join(",", [for upn, u in var.usuario_alias : "${upn}=${u}"]) },

    # Cuando caduca el secreto de cliente. Azure NO avisa de esto: el dia que
    # ocurra, el boton de Microsoft deja de funcionar sin ningun mensaje. La
    # aplicacion lo avisa a los Admin con antelacion para que no se descubra
    # por sorpresa, probablemente ya sin quien monto esto.
    { name = "OIDC_SECRETO_CADUCA", value = azuread_application_password.sso.end_date },
    { name = "OIDC_DIAS_AVISO_CADUCIDAD", value = tostring(var.dias_aviso_caducidad_secreto) },

    # Con esto la aplicacion se conecta con un token de Entra en lugar de con
    # POSTGRES_PASSWORD. La contrasena sigue arriba a proposito: es la red de
    # seguridad mientras el camino nuevo no lleve tiempo funcionando.
    #
    # El usuario es distinto de POSTGRES_USER porque el servidor no autentica a
    # `pgadmin` con un token, sino al rol que lleva la etiqueta de seguridad de
    # esta identidad. Ese rol NO lo crea Terraform —es SQL dentro de la base, no
    # un recurso de Azure— y se da de alta con:
    #
    #   CREATE ROLE "id-platgestion-prod-eus2-001" WITH LOGIN;
    #   SECURITY LABEL FOR "pgaadauth" ON ROLE "id-platgestion-prod-eus2-001"
    #     IS 'aadauth,oid=<principal_id de la identidad>,type=service';
    #   GRANT pgadmin TO "id-platgestion-prod-eus2-001";
    #
    # El orden no importa: si el rol todavia no existe, el servidor rechaza el
    # token y la aplicacion entra con la contrasena (config/db/entra/base.py).
    #
    # Van al final de la lista a proposito: Terraform compara estas listas por
    # posicion, asi que insertarlas en medio reescribe el diff de todas las de
    # abajo y vuelve ilegible un cambio de dos lineas.
    { name = "POSTGRES_ENTRA_USER", value = azurerm_user_assigned_identity.app.name },
    { name = "POSTGRES_ENTRA_CLIENT_ID", value = azurerm_user_assigned_identity.app.client_id },
  ]
}

# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------

resource "azurerm_container_app" "principal" {
  name                         = local.nombres.app
  resource_group_name          = azurerm_resource_group.principal.name
  container_app_environment_id = azurerm_container_app_environment.principal.id
  revision_mode                = "Single"

  tags = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.principal.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.secretos
    content {
      name  = secret.value.name
      value = secret.value.value
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    # El ingress termina TLS y reenvia HTTP al contenedor. Django lo detecta por
    # X-Forwarded-Proto (SECURE_PROXY_SSL_HEADER en settings/production.py).
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "web"
      image  = var.imagen_contenedor
      cpu    = var.cpu
      memory = var.memoria

      dynamic "env" {
        for_each = concat(local.env_comunes, [
          { name = "GUNICORN_WORKERS", value = tostring(var.gunicorn_workers) },
        ])
        content {
          name        = env.value.name
          value       = lookup(env.value, "value", null)
          secret_name = lookup(env.value, "secret_name", null)
        }
      }
    }
  }

  lifecycle {
    # La imagen la actualiza el pipeline de CI/CD con `az containerapp update`.
    # Sin esto, cada `terraform apply` revertiria el despliegue a la imagen que
    # figure en la variable (por defecto, la de arranque).
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  depends_on = [
    azurerm_role_assignment.app_acr_pull,
  ]
}

# ---------------------------------------------------------------------------
# Job de migraciones
# ---------------------------------------------------------------------------
# Las migraciones NO van en el arranque del contenedor web: con varias replicas
# arrancando a la vez, varias ejecutarian `migrate` en paralelo sobre la misma
# base. Este job de disparo manual las ejecuta una sola vez, y el pipeline lo
# invoca antes de publicar la nueva revision.
#
# Tambien crea la tabla `django_cache`, que production.py necesita para el
# DatabaseCache donde se cuentan los intentos fallidos de login.
#
# Coste: se factura por segundo de ejecucion. Unos segundos por despliegue.

resource "azurerm_container_app_job" "migraciones" {
  name                         = local.nombres.job_migraciones
  resource_group_name          = azurerm_resource_group.principal.name
  location                     = azurerm_resource_group.principal.location
  container_app_environment_id = azurerm_container_app_environment.principal.id

  replica_timeout_in_seconds = 600
  replica_retry_limit        = 1

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  tags = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.principal.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.secretos
    content {
      name  = secret.value.name
      value = secret.value.value
    }
  }

  template {
    container {
      name   = "migrate"
      image  = var.imagen_contenedor
      cpu    = var.cpu
      memory = var.memoria

      command = ["/bin/sh", "-c"]
      # Todos los comandos son idempotentes, por eso pueden correr en cada
      # despliegue: migrate no reaplica lo aplicado, createcachetable no
      # recrea la tabla, y los dos setup_ usan get_or_create / update_or_create.
      args = [
        join(" && ", [
          "python manage.py migrate --noinput",
          "python manage.py createcachetable",
          "python manage.py setup_grupos",
          "python manage.py setup_actividades",
        ])
      ]

      dynamic "env" {
        for_each = local.env_comunes
        content {
          name        = env.value.name
          value       = lookup(env.value, "value", null)
          secret_name = lookup(env.value, "secret_name", null)
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  depends_on = [
    azurerm_role_assignment.app_acr_pull,
  ]
}
