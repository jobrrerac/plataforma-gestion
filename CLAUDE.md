# Proyecto: Gestión de Asignación de Recursos

Especificación completa en `docs/SPEC.md`. Leer antes de implementar.

## Stack
Django 5.2 + DRF, PostgreSQL 16, Docker Compose.
Monolito modular. Despliegue: Azure App Service / VM / intranet.

## Fase 1 — Implementada (A0 + A1 + A6 + A7 + A10 parcial)
- Scaffold Docker + Django + PostgreSQL + CI GitHub Actions
- Modelos: Recurso, Proyecto, Asignacion, LogAuditoria, DiaNoLaborable, Indisponibilidad
- Motor de calendario: feriados Colombia (`holidays`), días no laborables globales, indisponibilidades por recurso
- Cálculo de `fecha_fin` a partir de horas/intensidad sobre días hábiles
- Validación de capacidad cruzada (8 h/día por persona sobre asignaciones APROBADAS)
- Dashboard visual: heatmap de ocupación / bench por día

## Despliegue en Azure
IaC en `terraform/`. Guía: `docs/DESPLIEGUE_AZURE.md`.
- Container Apps (Consumption, min-replicas 0) + PostgreSQL Flexible B1ms + ACR Basic.
- Solo se puede desplegar en la suscripción `b383e51f-...` del tenant `inetumoffshore.onmicrosoft.com`
  (guard de 3 capas en `terraform/guard.tf`). No sortearlo.
- Criterio de coste: empezar por el escalón más barato y escalar solo cuando haga falta.
  Si se descarta una opción por precio, dejar el motivo escrito en el `.tf`.
- La imagen del contenedor la gestiona CI/CD; Terraform ignora el campo `image`.
- Las migraciones van en el Container Apps Job, nunca en el arranque del contenedor web.

## Autenticación
Login local y SSO de Entra ID **conviven siempre**. El login local es el plan de
contingencia (el secreto de Entra caduca al año): no eliminarlo.
- Los app roles de Entra (`Admin`/`PM`/`Ingeniero`) se sincronizan a grupos de Django
  en cada login (`apps/accounts/oidc.py`). Ese mapeo es lo que sostiene el RBAC.
- `is_staff` se concede por rol; `is_superuser` NUNCA desde un token.
- WhiteNoise y el storage con manifiesto van solo en `settings/production.py`:
  en `base.py` rompen los tests y el desarrollo local.

## Reglas no negociables
- Soft-delete en todas las entidades (nunca borrado físico).
- RBAC con grupos de Django: `Admin`, `PM`, `Ingeniero`. El rol `Ingeniero` NUNCA ve costos.
- Tarifa con vigencia (effective dating); la asignación guarda snapshot de tarifa y costo.
- Feriados de Colombia con `holidays` (no hardcodear; respeta Ley Emiliani).
- Capacidad cruzada: regla 8 h/día es por persona, sobre asignaciones APROBADAS.
- Aprobación = "primero en aprobar gana": `select_for_update` por recurso dentro de `transaction.atomic`.
- `LogAuditoria` es append-only (no editar ni borrar).
- Integraciones (Skills, SAP): read-only, tras adaptador. Jobs idempotentes.

## Convenciones
- Tests con cada feature; cubrir fechas borde del calendario y la carrera de aprobación.
- Sin credenciales en el repo; todo por variables de entorno.
- Migraciones: `docker compose exec web python manage.py makemigrations` → revisar → `migrate`.
