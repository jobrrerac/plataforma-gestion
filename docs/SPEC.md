# Sistema de Gestión de Asignación de Recursos — Especificación

> Síntesis de la conversación de diseño. Pensado para usarse como contexto en **Claude Code**.
> Documentos complementarios: `Plan_Proyecto_Gestion_Asignacion_Recursos.xlsx` (cronograma y estimación) y `RFP_Detallado_Por_Actividad.docx` (fichas A0–A11).

---

## 1. Contexto y objetivo

El área es un **centro de costo** que administra recursos que paga la *business line*. Hoy la gestión se hace en planillas Excel. El objetivo es una **aplicación web interna** que permita:

- Asignar recursos (personas) a proyectos según los requerimientos de los PM.
- Estimar **costo** y **fecha fin** de cada asignación a partir de horas e intensidad diaria.
- Controlar la **capacidad** de cada persona (evitar sobreasignación).
- Mantener un **flujo de aprobación** con traza de auditoría.

Escala: ~27 recursos. Despliegue: Azure / intranet, bajo costo, capaz de correr local.

**El margen NO se modela aquí**: el área solo toma la tarifa plana de costo del recurso.

---

## 2. Arquitectura

- **Backend:** Django + Django REST Framework.
- **Base de datos:** PostgreSQL.
- **Frontend:** React donde haya interacción rica (el simulador de asignación); admin de Django / HTMX para el resto.
- **Identidad:** SSO con **Microsoft Entra ID** (OIDC/MSAL) — diferido a fase posterior.
- **Empaquetado:** Docker Compose. **Despliegue:** Azure App Service o VM B-series en intranet.
- **Integraciones (Skills, SAP) tras adaptadores read-only**.

Estilo: **monolito modular**, no microservicios.

---

## 3. Roles y visibilidad

Tres roles (grupos de Django): **PM**, **Admin**, **Ingeniero**.

| Capacidad | Ingeniero | PM | Admin |
|---|:---:|:---:|:---:|
| Banda / rango de tarifa | ✗ | ✓ | ✓ |
| Tarifa exacta | ✗ | ✓ | ✓ |
| Costo estimado total | ✗ | ✓ | ✓ |
| Registrar días no laborables | ✓ | ✗ | ✓ |
| Crear solicitud de asignación | ✗ | ✓ | ✓ |
| Aprobar / revocar | ✗ | ✗ | ✓ |

> El rol **Ingeniero nunca recibe costos**, ni en la API ni en la UI.

---

## 4. Modelo de dominio (entidades núcleo)

Soft-delete en todas (no borrado físico, por auditoría).

- **Recurso**: `nombre`, `email`, `banda/seniority`, `activo`.
- **Proyecto**: `codigo`, `nombre`, `cliente`, `fecha_inicio`, `fecha_fin`, `estado`, `pm`.
- **Asignacion**: `recurso`, `proyecto`, `horas_totales`, `intensidad_diaria`, `fecha_inicio`, `fecha_fin` (calculada), `politica_ausencia` (`RECOMPUTAR`/`REDUCIR`), `tarifa_aplicada` (snapshot), `costo_estimado` (snapshot), `estado`, `solicitada_por`.
- **DiaNoLaborable** (global): `fecha`, `descripcion`.
- **Indisponibilidad** (por recurso): `recurso`, `fecha_inicio`, `fecha_fin`, `tipo`, `origen`, `external_id?`.
- **Feriado**: calcular con la librería `holidays` (Colombia); no hardcodear.
- **LogAuditoria** (append-only, inmutable): `asignacion`, `accion`, `actor`, `timestamp`, `detalle`.

---

## 5. Reglas de negocio (críticas)

### 5.1 Cálculo de fecha fin
```
dias_necesarios = ceil(horas_totales / intensidad_diaria)
fecha = fecha_inicio; habiles = 0
while habiles < dias_necesarios:
    if es_habil(fecha, recurso):
        habiles += 1
        if habiles == dias_necesarios: break
    fecha += 1 dia
fecha_fin = fecha
alerta_intensidad = intensidad_diaria > 8   # alerta, no bloqueo
```

### 5.2 Capacidad cruzada (anti-sobreasignación)
```
para cada dia habil en [fecha_inicio .. fecha_fin]:
    carga = suma(intensidad_diaria de asignaciones APROBADAS del recurso que cubren ese dia)
    if carga + intensidad_diaria > 8:
        BLOQUEAR
```

### 5.3 Días hábiles
`es_habil(fecha, recurso)` es verdadero si la fecha no es:
- fin de semana,
- feriado de Colombia (librería `holidays`, respeta Ley Emiliani),
- `DiaNoLaborable` global,
- `Indisponibilidad` del recurso.

### 5.4 Flujo de aprobación — "primero en aprobar gana"
La capacidad se reserva **en la aprobación, no en la solicitud**.

```python
with transaction.atomic():
    recurso = Recurso.objects.select_for_update().get(pk=asignacion.recurso_id)
    if cabe_en_capacidad(asignacion):
        asignacion.estado = "APROBADA"
        LogAuditoria.objects.create(...)
    else:
        # marcar INVALIDADA o rechazar con mensaje claro
```

---

## 6. Paquetes de trabajo (A0–A11)

| ID | Actividad | Sprint |
|---|---|:---:|
| A0 | Setup e infraestructura | 1 |
| A1 | Modelo de datos + RBAC | 1 |
| A2 | SSO (Entra ID) | 1 |
| A3 | Proyectos (CRUD) | 2 |
| A4 | Recursos + Skills | 2 |
| A5 | Tarifas + visibilidad por rol | 2 |
| A6 | Motor de calendario | 3 |
| A7 | Asignación: cálculo, costo, capacidad | 4 |
| A8 | Aprobación + concurrencia + auditoría | 5 |
| A9 | Notificaciones | 5 |
| A10 | Dashboard y reportes | 6 |
| A11 | Despliegue + hardening + UAT | 6 |

**Camino crítico:** `A0 → A1 → A6 → A7 → A8`
