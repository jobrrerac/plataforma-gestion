# Sistema de Gestión de Asignación de Recursos — Especificación

> Síntesis de la conversación de diseño. Pensado para usarse como contexto en **Claude Code**.
> Guárdalo como `docs/SPEC.md` en el repo y crea un `CLAUDE.md` corto que lo referencie (plantilla al final).
> Documentos complementarios: `Plan_Proyecto_Gestion_Asignacion_Recursos.xlsx` (cronograma y estimación) y `RFP_Detallado_Por_Actividad.docx` (fichas A0–A11).

---

## 1. Contexto y objetivo

El área es un **centro de costo** que administra recursos que paga la *business line*. Hoy la gestión se hace en planillas Excel. El objetivo es una **aplicación web interna** que permita:

- Asignar recursos (personas) a proyectos según los requerimientos de los PM.
- Estimar **costo** y **fecha fin** de cada asignación a partir de horas e intensidad diaria.
- Controlar la **capacidad** de cada persona (evitar sobreasignación).
- Mantener un **flujo de aprobación** con traza de auditoría.

Escala: ~27 recursos. Despliegue: Azure / intranet, bajo costo, capaz de correr local.

**El margen NO se modela aquí**: el área solo toma la tarifa plana de costo del recurso. El margen lo estima el PM por fuera del sistema.

---

## 2. Arquitectura

- **Backend:** Django + Django REST Framework. Se elige Django porque su auth, permisos por grupos, ORM, migraciones y admin cubren de entrada el RBAC y los CRUD de catálogos.
- **Base de datos:** PostgreSQL (mejor manejo de fechas y *window functions* para capacidad y reportes).
- **Frontend:** React donde haya interacción rica (el simulador de asignación); admin de Django / HTMX para el resto.
- **Identidad:** SSO con **Microsoft Entra ID** (OIDC/MSAL); los grupos de Entra ID alimentan el RBAC.
- **Empaquetado:** Docker Compose. **Despliegue:** Azure App Service o VM B-series en intranet.
- **Integraciones (Skills, SAP) tras adaptadores read-only**, para que el origen sea intercambiable.

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

> El rol **Ingeniero nunca recibe costos**, ni en la API ni en la UI. Probar explícitamente.
> El **PM ve tarifa exacta** (decisión tomada; no hay enmascaramiento por banda en el cálculo final).

---

## 4. Modelo de dominio (entidades núcleo)

Campos clave (no exhaustivo). **Soft-delete en todas** (no borrado físico, por auditoría).

- **Recurso**: `nombre`, `email`, `banda/seniority`, `activo`, relación a skills.
- **Skill**: `nombre`, `categoria`, `external_id` (id en la fuente de origen).
- **RecursoSkill**: `recurso`, `skill`, `nivel?`.
- **Tarifa**: `recurso`, `costo_hora`, `moneda` (COP/USD), `vigente_desde`, `vigente_hasta` (null = vigente). **Effective dating obligatorio.**
- **Proyecto**: `codigo`, `nombre`, `cliente`, `fecha_inicio`, `fecha_fin`, `estado`, `pm`.
- **Asignacion**: `recurso`, `proyecto`, `horas_totales`, `intensidad_diaria`, `fecha_inicio`, `fecha_fin` (calculada), `politica_ausencia` (`RECOMPUTAR`/`REDUCIR`), `tarifa_aplicada` (snapshot), `costo_estimado` (snapshot), `estado`, `solicitada_por`, `creada_en`.
- **DiaNoLaborable** (global): `fecha`, `descripcion`. Registrado por el Ingeniero.
- **Indisponibilidad** (por recurso): `recurso`, `fecha_inicio`, `fecha_fin`, `tipo` (`VACACION`/`PERMISO`), `origen` (`MANUAL`/`SAP`), `external_id?`.
- **Feriado**: calcular con la librería `holidays` (Colombia); cachear si se requiere.
- **LogAuditoria** (append-only, inmutable): `asignacion`, `accion` (`APROBAR`/`RECHAZAR`/`REVOCAR`/`INVALIDAR`), `actor`, `timestamp`, `detalle/snapshot`.

---

## 5. Reglas de negocio (críticas)

### 5.1 Tarifa con vigencia
La asignación guarda la tarifa vigente **al momento de crearse** (snapshot). Cambiar una tarifa NO altera asignaciones históricas. Resolver siempre por fecha:
```
tarifa_vigente(recurso, fecha) -> Tarifa con vigente_desde <= fecha < vigente_hasta (o null)
```

### 5.2 Cálculo de fecha fin y costo
```
dias_necesarios = ceil(horas_totales / intensidad_diaria)
fecha = fecha_inicio; habiles = 0
while habiles < dias_necesarios:
    if es_habil(fecha, recurso):   # ver 5.5
        habiles += 1
        if habiles == dias_necesarios: break
    fecha += 1 dia
fecha_fin = fecha
costo_estimado = horas_totales * tarifa_vigente(recurso, fecha_inicio).costo_hora
alerta_intensidad = intensidad_diaria > 8     # alerta, no bloqueo
```

### 5.3 Capacidad cruzada (anti-sobreasignación)
La regla de 8h **no es por asignación, es por día y por persona**:
```
para cada dia habil en [fecha_inicio .. fecha_fin]:
    carga = suma(intensidad_diaria de asignaciones APROBADAS del recurso que cubren ese dia)
    if carga + intensidad_diaria > 8:
        BLOQUEAR  # sobreasignación
```
Consulta **todas** las asignaciones aprobadas del recurso, no solo la actual.

### 5.4 Política de ausencia (campo `politica_ausencia`)
Cuando una indisponibilidad cae dentro del rango de una asignación:
- **RECOMPUTAR**: preserva `horas_totales`, empuja `fecha_fin` (recorre más días hábiles). Costo constante.
- **REDUCIR**: preserva la ventana, `horas_totales -= dias_perdidos * intensidad_diaria`, recalcula costo.

Se elige al crear la asignación o al resolver el conflicto.

### 5.5 Días hábiles
`es_habil(fecha, recurso)` es verdadero si la fecha **no** es:
- fin de semana,
- feriado de Colombia (librería `holidays`, respeta Ley Emiliani — **no hardcodear**),
- `DiaNoLaborable` global,
- `Indisponibilidad` del recurso.

### 5.6 Flujo de aprobación — "primero en aprobar gana"
La capacidad se reserva **en la aprobación, no en la solicitud**. Las solicitudes pueden solaparse libremente. Máquina de estados:

| Desde | Evento | Hacia | Efecto |
|---|---|---|---|
| Solicitada | Aprobar (✓ capacidad, tx+lock) | Aprobada | Reserva capacidad |
| Solicitada | Rechazar | Rechazada | — |
| Solicitada | Pierde la carrera de capacidad | Invalidada | — |
| Aprobada | Revocar | Revocada | Libera capacidad |
| Aprobada | Ausencia SAP retroactiva | (sin cambio) | Levanta alerta de conflicto |

Aprobación transaccional (evita carreras entre dos admins):
```python
with transaction.atomic():
    recurso = Recurso.objects.select_for_update().get(pk=asignacion.recurso_id)  # serializa por recurso
    if cabe_en_capacidad(asignacion):   # re-valida contra APROBADAS, ver 5.3
        asignacion.estado = "APROBADA"
        LogAuditoria.objects.create(asignacion=asignacion, accion="APROBAR", actor=request.user, ...)
    else:
        # rechazar con mensaje claro o marcar INVALIDADA
        ...
```
El `LogAuditoria` es **append-only**: no se edita ni se borra.

---

## 6. Integraciones (read-only, tras adaptador)

- **Skills**: interfaz `SkillProvider` con ≥1 implementación (API / vista de solo lectura / export). Job de sync **idempotente**. Nunca escribir en el origen. Orden de preferencia de fuente: API → réplica/vista de solo lectura → ETL programado.
- **SAP (ausencias)**: en versiones iniciales, **ETL** que carga `Indisponibilidad`. Idempotente y read-only. **Reconciliación**: si una ausencia importada pisa una asignación ya aprobada, **levantar alerta para el admin**, nunca romper en silencio.

---

## 7. Paquetes de trabajo (A0–A11)

Esfuerzo en **días-junior** (ejecutan los ingenieros junior; senior lidera/parea). Detalle completo en el `.docx`.

| ID | Actividad | Tecnologías | Días-jr | Líder | Riesgo | Depende de | Sprint |
|---|---|---|---:|---|---|---|:---:|
| A0 | Setup e infraestructura | Docker, PostgreSQL, Django, DRF, CI | 5.6 | Senior | Medio | — | 1 |
| A1 | Modelo de datos + RBAC | Django ORM, auth/groups | 6.0 | Senior | Medio | A0 | 1 |
| A2 | SSO (Entra ID) | OIDC/MSAL, Entra ID | 4.0 | Senior | Alto | A0 | 1 |
| A3 | Proyectos (CRUD) | DRF, React/HTMX | 3.5 | Junior | Bajo | A1 | 2 |
| A4 | Recursos + Skills | DRF, adapter, ETL | 9.0 | Senior | Alto | A1 | 2 |
| A5 | Tarifas + visibilidad por rol | Django, serializers | 4.25 | Junior | Medio | A4 | 2 |
| A6 | Motor de calendario | `holidays`, ETL SAP | 12.0 | Senior | Alto | A1 | 3 |
| A7 | Asignación: cálculo, costo, capacidad | Django, React | 15.4 | Senior | Muy alto | A5, A6 | 4 |
| A8 | Aprobación + concurrencia + auditoría | `select_for_update`, state machine | 11.25 | Senior | Muy alto | A7 | 5 |
| A9 | Notificaciones | signals, email | 4.25 | Junior | Medio | A8 | 5 |
| A10 | Dashboard y reportes | DRF, agregaciones, charts | 6.4 | Junior | Medio-bajo | A7, A8 | 6 |
| A11 | Despliegue + hardening + UAT | Azure, Docker, nginx, backups | 10.0 | Senior | Alto | Todas | 6 |

**Totales:** completo ~92 días-jr base (~108 con buffer 18%); MVP (A0–A8) ~71 base (~84 con buffer).

---

## 8. Cronograma, camino crítico y paralelización

**6 sprints de 2 semanas = 12 semanas.** MVP funcional end-to-end ~semana 10.

Hitos visibles:
- **Sem 2**: login SSO + admin (cargar datos).
- **Sem 3-4**: primeros CRUD de negocio (proyectos, recursos+skills, tarifas).
- **Sem 6**: motor de calendario.
- **Sem 8**: simulador de asignación (función estrella).
- **Sem 10**: flujo de aprobación → MVP.

**Camino crítico (secuencial, no paralelizable):**
```
A0 → A1 → A6 → A7 → A8
```
~50 días-jr en serie. **Piso de ~10 semanas** aunque se sume headcount.

**Paralelización (forma de reloj de arena):**
- **Ancho (sprints 1-2):** catálogos A2/A3/A4/A5 casi independientes → los 4 juniors en paralelo.
- **Angosto (sprints 3-5):** la espina A6→A7→A8 no se parte; aquí 1 senior + 1-2 juniors pareando (sumar más juniors estorba).
- **Ancho otra vez (sprint 6):** A9/A10/A11 se reparten.
- **Cuello de botella real:** la disponibilidad del senior (~30%) en sprints 3-5. Subirla a ~50-60% en ese tramo recorta 1-2 semanas; más juniors no.

---

## 9. Riesgos y bloqueantes a resolver ANTES del Sprint 1

1. **Acceso a la fuente de Skills** (API / réplica / export). Bloquea A4. → Confirmar con el proveedor del sistema.
2. **Alta de la app en Entra ID** (requiere admin de Azure / TI). Bloquea A2. → Solicitar a TI ya.
3. Concurrencia (A8) y calendario (A6/A7) son de alto riesgo junior → senior lidera; reservar buffer.
4. Ausencias SAP retroactivas → reconciliación con alerta, no romper en silencio.

---

## 10. Cómo empezar con Claude Code

1. Crea el repo, ábrelo en VSCode con Claude Code, y coloca este archivo en `docs/SPEC.md`.
2. Crea un `CLAUDE.md` en la raíz (plantilla abajo) que apunte al spec y fije las reglas no negociables.
3. Arranca por el **Sprint 1** en orden: **A0 → A1 → A2**.
4. Primer prompt sugerido para Claude Code:
   > "Lee `docs/SPEC.md` y `CLAUDE.md`. Implementa A0: scaffold de un proyecto Django + DRF con PostgreSQL, `docker-compose.yml` (web, db), configuración por variables de entorno, `.env.example`, linter y un workflow de CI que corra los tests. No incluyas credenciales en el repo."

### Estructura de repo sugerida
```
.
├── CLAUDE.md
├── docs/SPEC.md
├── docker-compose.yml
├── .env.example
├── backend/            # Django + DRF
│   ├── config/         # settings, urls
│   └── apps/
│       ├── core/       # Recurso, Skill, Tarifa, Proyecto
│       ├── calendar/   # días hábiles, feriados, indisponibilidad
│       ├── assignments/# Asignacion, cálculo, capacidad, estados, auditoría
│       └── accounts/   # RBAC, SSO Entra ID
├── frontend/           # React (simulador de asignación)
└── integrations/       # SkillProvider, ETL SAP
```

### Plantilla de `CLAUDE.md`
```markdown
# Proyecto: Gestión de Asignación de Recursos

La especificación completa está en `docs/SPEC.md`. Léela antes de implementar.

## Stack
Django + DRF, PostgreSQL, React (solo simulador), Docker Compose, SSO Entra ID.
Monolito modular. Despliegue en Azure/intranet.

## Reglas no negociables
- Soft-delete en todas las entidades (nunca borrado físico).
- RBAC con grupos de Django (PM / Admin / Ingeniero). El rol Ingeniero NUNCA ve costos.
- Tarifa con vigencia (effective dating); la asignación guarda snapshot de tarifa y costo.
- Feriados de Colombia con la librería `holidays` (no hardcodear; respeta Ley Emiliani).
- Capacidad cruzada: la regla de 8h es por día y por persona, sobre asignaciones APROBADAS.
- Aprobación = "primero en aprobar gana": reservar capacidad en la aprobación, dentro de
  una transacción con `select_for_update` por recurso.
- LogAuditoria es append-only (no editar ni borrar).
- Integraciones (Skills, SAP) read-only, tras adaptador. Jobs idempotentes.

## Convenciones
- Tests con cada feature; cubrir fechas borde del calendario y la carrera de aprobación.
- Sin credenciales en el repo; todo por variables de entorno.
```
