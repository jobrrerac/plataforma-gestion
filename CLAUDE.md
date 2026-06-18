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
