# Infraestructura Azure

Guía completa: [`docs/DESPLIEGUE_AZURE.md`](../docs/DESPLIEGUE_AZURE.md).

## Arranque rápido

```bash
az login --tenant fdb323c6-1c3c-47a4-9144-2cabbc82699c
az account set --subscription b383e51f-9354-4d6a-8d3b-cc9abb1b9743

cp terraform.tfvars.example terraform.tfvars
echo "ip_desarrollador = \"$(curl -s https://api.ipify.org)\"" >> terraform.tfvars

terraform init
terraform plan
terraform apply
```

## Archivos

| Archivo | Contenido |
|---|---|
| `guard.tf` | Verifica que se despliega en la suscripción correcta. **Leer antes de tocar nada.** |
| `variables.tf` | Todas las perillas, con el porqué de cada valor por defecto. |
| `main.tf` | Grupo de recursos, nombres, contraseñas generadas. |
| `database.tf` | PostgreSQL B1ms, firewall, parámetros del servidor. |
| `containerapp.tf` | Entorno, aplicación y job de migraciones. |
| `registry.tf` | ACR Basic e identidad de la aplicación. |
| `entra.tf` | App Registration del SSO y los roles Admin/PM/Ingeniero. |
| `cicd.tf` | Identidad federada de GitHub Actions (OIDC, sin secretos). |
| `observability.tf` | Log Analytics con tope de ingesta. |

## Avisos

- **El estado tiene secretos en claro** (contraseña de PostgreSQL, `SECRET_KEY`
  de Django, client secret de Entra). `terraform.tfstate` está en `.gitignore`.
  Para trabajo en equipo, descomentar el backend remoto de `versions.tf`.
- **La imagen la gestiona el CI/CD.** Terraform ignora los cambios en el campo
  `image`; si no lo hiciera, cada `apply` revertiría el último despliegue.
- **La base de datos tiene `prevent_destroy`.** Un `terraform destroy` falla a
  propósito hasta que se quite a mano.
