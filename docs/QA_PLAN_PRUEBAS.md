# Plan de Pruebas QA — Plataforma de Gestión de Asignación de Recursos

**Versión:** 1.0 · **Fecha:** 2026-07-07 · **Alcance:** Fase 1 (calendario, solicitudes, aprobación, dashboard, RBAC, auditoría)

Cada caso indica: precondiciones, pasos y resultado esperado. QA registra **PASS / FAIL / BLOQUEADO** y evidencia (captura o respuesta de API). Los casos de API pueden ejecutarse desde el DRF browsable API (navegando a la URL logueado) o con Postman/curl usando sesión.

---

## 1. Preparación del entorno y datos de prueba

Ejecutar una sola vez antes de la ronda de pruebas (o tras un reseteo de BD):

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py setup_grupos
docker compose exec web python manage.py createsuperuser   # si no existe
```

### 1.1 Usuarios de prueba

Crear en `/admin/auth/user/` (logueado como superusuario):

| Usuario     | Grupo     | Es staff | Uso |
|-------------|-----------|----------|-----|
| `qa_admin`  | Admin     | ✔ Sí     | Aprueba/rechaza/revoca, administra catálogos |
| `qa_pm`     | PM        | ✔ Sí     | Crea solicitudes, gestiona indisponibilidades |
| `qa_ing`    | Ingeniero | ✔ Sí     | Solo consulta; **nunca debe ver costos ni emails** |

> "Es staff" es necesario para que puedan entrar a `/admin/`. El grupo controla qué pueden hacer una vez dentro.

### 1.2 Datos maestros

Crear como `qa_admin` en `/admin/`:

| Entidad | Datos |
|---|---|
| Recurso **R1** | Nombre: `QA Recurso Uno`, email: `r1@qa.test`, banda: Senior, activo |
| Recurso **R2** | Nombre: `QA Recurso Dos`, email: `r2@qa.test`, banda: Junior, activo |
| Proyecto **P1** | Código: `QA-001`, estado: ACTIVO, PM: `qa_pm`, fecha inicio: `2026-07-01` |
| Proyecto **P2** | Código: `QA-002`, estado: CERRADO (para probar que no aparece en formularios) |
| Tarifa R1 | 50,00 €/h vigente desde `2026-01-01` |
| Día no laborable global | `2026-07-24` (viernes), descripción: "Día QA global" |
| Indisponibilidad R2 | Del `2026-07-13` al `2026-07-17` (vacaciones) |

### 1.3 Fechas de referencia (julio–agosto 2026)

- `2026-07-20` (lunes) — **feriado**: Día de la Independencia.
- `2026-08-07` (viernes) — **feriado**: Batalla de Boyacá.
- `2026-07-24` (viernes) — día no laborable global creado en 1.2.
- `2026-07-13` a `2026-07-17` — semana de indisponibilidad de R2.
- Jornada máxima: **lunes a jueves 8,5 h · viernes 8,0 h** (42 h semanales).

> La lista oficial de feriados puede consultarse en `/api/calendario/feriados/?year=2026`.

---

## 2. AUT — Autenticación y bloqueo de fuerza bruta

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| AUT-01 | Login correcto | Ir a `/login/`, ingresar `qa_pm` con contraseña válida | Redirige al dashboard (`/`); se ve el heatmap de ocupación |
| AUT-02 | Login incorrecto | Ingresar `qa_pm` con contraseña errónea | Vuelve al formulario con mensaje de error; NO revela si el usuario existe |
| AUT-03 | Bloqueo tras 5 intentos fallidos | Fallar el login 5 veces seguidas con `qa_ing`; intentar una 6ª vez (incluso con la contraseña correcta) | Página 403: "Demasiados intentos fallidos. Espere 15 minutos…" |
| AUT-04 | El bloqueo es por usuario/IP, no global | Con AUT-03 activo, en otro navegador (u otra sesión) loguear `qa_admin` correctamente | `qa_admin` entra sin problema |
| AUT-05 | Desbloqueo tras 15 min | Esperar 15 min tras AUT-03 y loguear `qa_ing` con contraseña correcta | Login exitoso |
| AUT-06 | Rutas protegidas sin sesión | Sin loguearse, abrir `/dashboard/`, `/solicitud/`, `/recurso/1/` | Redirige a `/login/?next=…`; tras loguearse vuelve a la página pedida |
| AUT-07 | API sin sesión | Sin sesión, GET `/api/asignaciones/` | HTTP 403 (no expone datos) |
| AUT-08 | Logout | Logueado, ir a `/logout/` | Sesión cerrada, redirige a `/login/`; el "atrás" del navegador no muestra datos nuevos |
| AUT-09 | Login del admin redirige al login propio | Sin sesión, abrir `/admin/` | Redirige a `/login/?next=/admin/`; tras login como `qa_admin` entra al sitio administrativo |

---

## 3. RBAC — Control de acceso por rol

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| RBAC-01 | Ingeniero no accede al flujo de solicitud | Como `qa_ing`, abrir `/solicitud/` y `/solicitud/crear/` | HTTP 403 en ambas |
| RBAC-02 | PM sí accede al flujo de solicitud | Como `qa_pm`, abrir `/solicitud/` | Carga el buscador de disponibilidad |
| RBAC-03 | **Ingeniero nunca ve costos** | Como `qa_pm`, buscar disponibilidad en `/solicitud/` y anotar que aparecen columnas de tarifa/costo estimado. Repetir el intento como `qa_ing` | Para `qa_ing` la página devuelve 403 (no llega a ver costos). En ninguna pantalla accesible a `qa_ing` (dashboard, detalle de recurso, API) aparece tarifa ni costo |
| RBAC-04 | Ingeniero no ve emails en el dashboard | Como `qa_ing`, GET `/api/dashboard/ocupacion/` | La respuesta NO incluye el campo `email` de los recursos |
| RBAC-05 | Admin/PM sí ven emails | Como `qa_pm`, GET `/api/dashboard/ocupacion/` | Cada recurso incluye su `email` |
| RBAC-06 | API recursos oculta email a Ingeniero | Como `qa_ing`, GET `/api/recursos/` | Los objetos NO traen campo `email`; como `qa_pm` sí lo traen |
| RBAC-07 | Solo Admin escribe catálogos por API | Como `qa_pm`, POST `/api/recursos/` con un recurso nuevo | HTTP 403 "Se requiere rol Admin…". Como `qa_admin` → 201 creado |
| RBAC-08 | PM crea asignaciones por API, Ingeniero no | POST `/api/asignaciones/` (recurso R1, proyecto P1, 8 h, intensidad 8, inicio `2026-08-10`) como `qa_ing` y luego como `qa_pm` | `qa_ing` → 403; `qa_pm` → 201 con estado SOLICITADA |
| RBAC-09 | PM no puede aprobar | Como `qa_pm`, POST `/api/asignaciones/{id}/aprobar/` sobre la solicitud de RBAC-08 | HTTP 403 |
| RBAC-10 | PM no puede editar ni borrar asignaciones | Como `qa_pm`, PATCH y DELETE sobre `/api/asignaciones/{id}/` | HTTP 403 en ambos |
| RBAC-11 | Indisponibilidades: PM y Admin | Como `qa_ing`, GET `/api/calendario/indisponibilidades/` → 403. Como `qa_pm` → 200 y puede crear una | Según lo descrito |
| RBAC-12 | Días no laborables globales: solo Admin escribe | Como `qa_pm`, POST `/api/calendario/dias-no-laborables/` → 403. GET → 200. Como `qa_admin` POST → 201 | Según lo descrito |
| RBAC-13 | Admin del sitio: PM no aprueba desde /admin/ | Como `qa_pm`, entrar a `/admin/assignments/asignacion/` e intentar el botón "✓ Aprobar" de una solicitud (o la URL `/admin/assignments/asignacion/aprobar/{id}/`) | Mensaje de error "Se requiere rol Admin para aprobar asignaciones"; el estado NO cambia |

---

## 4. CAL — Motor de calendario

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| CAL-01 | Feriados de Colombia por API | GET `/api/calendario/feriados/?year=2026` logueado | Lista con feriados; incluye `2026-07-20` (Independencia) y `2026-08-07` (Boyacá) |
| CAL-02 | Año inválido | GET `/api/calendario/feriados/?year=abc` y `?year=1800` | HTTP 400 con mensaje de error en ambos (no error 500) |
| CAL-03 | Fecha fin salta fin de semana | Como `qa_pm`, en `/solicitud/` modo "por horas": inicio `2026-07-06` (lunes), 40 h, intensidad 8 | Fecha fin calculada `2026-07-10` (viernes): 5 días hábiles corridos |
| CAL-04 | Fecha fin salta feriado | Igual, inicio `2026-07-16` (jueves), 24 h, intensidad 8 | Fecha fin `2026-07-21`: cuenta 16, 17 y 21; **salta el lunes 20 (feriado)** |
| CAL-05 | Fecha fin salta día no laborable global | Igual, inicio `2026-07-22` (miércoles), 24 h, intensidad 8 | Fecha fin `2026-07-27`: cuenta 22, 23 y 27; **salta el viernes 24** (día no laborable de 1.2) |
| CAL-06 | Indisponibilidad del recurso | Como `qa_pm`, buscar disponibilidad del `2026-07-13` al `2026-07-17` | R2 aparece con 0 días hábiles / 0 h de capacidad (semana de vacaciones); R1 con 4 días (el 13–16; nota: 17 es hábil, verificar 5 días si aplica) — R1 muestra la semana completa como disponible |
| CAL-07 | Día no hábil en el dashboard | Como `qa_pm`, en `/dashboard/` navegar a julio 2026 | Las columnas de sábados, domingos, el 20 y el 24 de julio se ven marcadas como no hábiles (celdas grises) para todos los recursos; la indisponibilidad de R2 (13–17 jul) solo en la fila de R2 |

> Nota CAL-06: la fecha `2026-07-17` es viernes y forma parte tanto de la semana laboral como de la indisponibilidad de R2 — R2 debe quedar con 0 días en todo el rango.

---

## 5. SOL — Flujo de solicitud de recursos (PM)

Todos como `qa_pm`, en `/solicitud/`.

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| SOL-01 | Búsqueda por rango de fechas | Buscar del `2026-08-03` al `2026-08-14` sin skills | Lista de recursos activos ordenada de más a menos disponible, con % libre, horas de capacidad y días hábiles (9 días: salta el feriado 07/08) |
| SOL-02 | Búsqueda por horas totales | Modo "por horas": inicio `2026-08-03`, 40 h, intensidad 8 | Calcula la fecha fin automáticamente y muestra disponibilidad para ese rango |
| SOL-03 | Filtro por skills | Asignar un skill a R1 en `/admin/` y buscar filtrando por ese skill | Solo aparece R1 |
| SOL-04 | Validación: fechas invertidas | Buscar con fecha fin anterior a fecha inicio | Mensaje "La fecha fin debe ser posterior a la fecha de inicio"; no rompe la página |
| SOL-05 | Validación: rango mayor a 180 días | Buscar del `2026-07-01` al `2027-02-01` | Mensaje "El rango máximo de búsqueda es 180 días" |
| SOL-06 | Validación: intensidad fuera de rango | Modo por horas con intensidad `9` | Mensaje "Intensidad máxima: 8.5 h/día" |
| SOL-07 | Crear solicitud por rango | Desde los resultados de SOL-01, elegir R1 → formulario de creación → proyecto P1, intensidad 4, enviar | Se crea la asignación en estado **SOLICITADA**; pantalla de confirmación con fechas y horas (36 h = 9 días × 4 h) |
| SOL-08 | Proyecto cerrado no seleccionable | En el formulario de creación, revisar el desplegable de proyectos | Aparece `QA-001` (ACTIVO); NO aparece `QA-002` (CERRADO) |
| SOL-09 | Jornada completa | Crear solicitud para R1, `2026-08-10` al `2026-08-14`, marcando "jornada completa" | Horas calculadas: 42 (4×8,5 + 8 del viernes); intensidad guardada 8.0 |
| SOL-10 | Crear por horas respetando ocupación | Con una asignación APROBADA de 8 h/día para R1 (ver APR-01) en `2026-08-03`–`07`, crear solicitud por horas: R1, inicio `2026-08-03`, 16 h, intensidad 8 | La solicitud salta los días ya llenos: muestra días bloqueados y la fecha fin cae después del `2026-08-07` |
| SOL-11 | Solicitud con parámetros manipulados | Abrir `/solicitud/crear/?recurso=999&fecha_inicio=2026-08-03&fecha_fin=2026-08-07` (recurso inexistente) | Página de error controlada ("parámetros inválidos"); no error 500 |

---

## 6. APR — Aprobación, rechazo y revocación (Admin)

Todos como `qa_admin` en `/admin/assignments/asignacion/`, salvo indicación.

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| APR-01 | Aprobar solicitud sin conflicto | Crear (como `qa_pm`) una solicitud para R1, `2026-08-03`–`07`, intensidad 8, P1. Como `qa_admin` pulsar "✓ Aprobar" | Estado pasa a **APROBADA** (badge verde); mensaje de éxito |
| APR-02 | Log de auditoría del ciclo | Abrir la asignación de APR-01 y ver el inline "Logs de auditoría" | Existen entradas CREAR (actor `qa_pm`) y APROBAR (actor `qa_admin`) con timestamp |
| APR-03 | Sobreasignación bloqueada | Con APR-01 aprobada (8 h/día), crear otra solicitud para R1 mismo rango, intensidad 4, y aprobarla | NO se aprueba directo: aparece la pantalla de **recomputo** proponiendo nueva fecha fin (salta los días llenos) |
| APR-04 | Aprobar recomputando | En la pantalla de APR-03, confirmar | Estado APROBADA con fecha fin corrida; log con `recomputo: true` |
| APR-05 | Cancelar recomputo | Repetir APR-03 con otra solicitud pero pulsar "volver"/cancelar en la pantalla de confirmación | La solicitud queda en SOLICITADA, sin cambios |
| APR-06 | Borde viernes: 8,5 h no caben | Aprobar para R2 una asignación de intensidad 4,5 (`2026-08-24`–`28`, lun–vie). Luego solicitar y aprobar otra de intensidad 4,0 mismo rango | Lun–jue suman 8,5 (cabe justo); el **viernes 28** suma 8,5 > 8,0 → pantalla de recomputo mostrando solo el viernes como conflictivo |
| APR-07 | Rechazar solicitud | Crear una solicitud y pulsar "✗ Rechazar" | Estado **RECHAZADA**; log RECHAZAR registrado |
| APR-08 | Revocar aprobada | Sobre una APROBADA, pulsar "↩ Revocar" | Estado **REVOCADA**; sus horas dejan de contar: una nueva solicitud sobre esas fechas ya no genera conflicto |
| APR-09 | No se aprueba dos veces | Por API: POST `/api/asignaciones/{id}/aprobar/` sobre una ya APROBADA | HTTP 400 "No se puede aprobar una asignación en estado 'APROBADA'" |
| APR-10 | No se revoca una SOLICITADA | POST `/api/asignaciones/{id}/revocar/` sobre una SOLICITADA | HTTP 400 "Solo se pueden revocar asignaciones aprobadas" |
| APR-11 | Carrera: primero en aprobar gana (opcional/avanzado) | Dos solicitudes que llenan la misma capacidad de R1. Dos sesiones de admin en ventanas distintas; aprobar casi simultáneamente una en cada una | Solo una queda APROBADA; la otra recibe error de sobreasignación o pantalla de recomputo. Nunca quedan ambas aprobadas superando el tope diario |
| APR-12 | API de aprobación con conflicto | POST `/api/asignaciones/{id}/aprobar/` sobre una solicitud que excede capacidad | HTTP 409 con mensaje "Sobreasignación: …" |

---

## 7. DASH — Dashboard de ocupación

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| DASH-01 | Vista general | Como cualquier usuario logueado, abrir `/dashboard/` | Heatmap con recursos activos en filas y días en columnas; hoy resaltado |
| DASH-02 | Estados BENCH / OCUPADO | Con R1 con asignación aprobada hoy y R2 sin nada | R1 figura OCUPADO con su % del día; R2 figura BENCH (0 h) |
| DASH-03 | Porcentajes por día | En un día donde R1 tiene 4 h aprobadas (lun–jue) | Celda ~47 % (4/8,5); si fuera viernes, 50 % (4/8) |
| DASH-04 | Rango máximo API | GET `/api/dashboard/ocupacion/?fecha_inicio=2026-01-01&fecha_fin=2026-06-30` | HTTP 400 "El rango máximo es 90 días" |
| DASH-05 | Fechas mal formadas | GET `/api/dashboard/ocupacion/?fecha_inicio=31-12-2026` | HTTP 400 "Formato inválido. Use YYYY-MM-DD." |
| DASH-06 | Detalle de recurso | Click en un recurso del dashboard (o `/recurso/{id}/`) | Asignaciones en curso y próximas del recurso, con proyecto y fechas; sin datos de tarifa para `qa_ing` |
| DASH-07 | Recurso inactivo no aparece | Desactivar R2 en `/admin/core/recurso/` (activo=No) y recargar dashboard | R2 desaparece del heatmap; reactivarlo al terminar |

---

## 8. AUD — Auditoría y soft-delete

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| AUD-01 | Log append-only en admin | Como `qa_admin`, abrir `/admin/assignments/logauditoria/` y entrar a un registro | Solo lectura: sin botón "Guardar", sin "Añadir log", sin "Eliminar" |
| AUD-02 | Toda transición queda registrada | Revisar los logs tras la ronda APR | Cada CREAR/APROBAR/RECHAZAR/REVOCAR tiene entrada con actor, timestamp y detalle JSON |
| AUD-03 | Soft-delete de recurso | Como `qa_admin`, DELETE `/api/recursos/{id de R2}/` (o eliminar desde admin) | HTTP 204; R2 desaparece de `GET /api/recursos/` y del dashboard, **pero** sus asignaciones históricas siguen existiendo y el registro conserva sus datos (verificable en BD: `deleted_at` con fecha) |
| AUD-04 | Borrado físico bloqueado por integridad | Intentar borrar (desde admin) un Proyecto con asignaciones | El proyecto se marca eliminado (soft-delete), las asignaciones históricas no se pierden |

> Para restaurar un registro soft-deleted durante pruebas: limpiar `deleted_at` en BD o recrear el dato.

---

## 9. API — Robustez general

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| API-01 | Paginación | GET `/api/asignaciones/` con más de 100 registros | Respuesta paginada (`count`, `next`, `previous`, `results` de máx. 100) |
| API-02 | Filtros de asignaciones | GET `/api/asignaciones/?recurso={R1}&estado=APROBADA` | Solo asignaciones de R1 aprobadas |
| API-03 | Throttling autenticado | Lanzar >200 requests en un minuto al mismo endpoint (script) | A partir del límite responde HTTP 429 |
| API-04 | Validaciones de creación | POST `/api/asignaciones/` con `horas_totales: -5` o `intensidad_diaria: 0` | HTTP 400 con el detalle del campo |
| API-05 | Datos ajenos al esquema | POST `/api/asignaciones/` intentando fijar `estado: "APROBADA"` o `fecha_fin` manual | Se ignoran: la asignación se crea SOLICITADA y con fecha fin calculada por el motor |

---

## 10. Matriz de trazabilidad rol × acción

| Acción | Ingeniero | PM | Admin |
|---|:---:|:---:|:---:|
| Ver dashboard / detalle de recurso | ✔ | ✔ | ✔ |
| Ver emails de recursos | ✘ | ✔ | ✔ |
| Ver tarifas y costos | ✘ | ✔ | ✔ |
| Buscar disponibilidad y crear solicitud | ✘ | ✔ | ✔ |
| Aprobar / rechazar / revocar | ✘ | ✘ | ✔ |
| Editar / eliminar asignaciones | ✘ | ✘ | ✔ |
| CRUD recursos, proyectos, tarifas, días no laborables | ✘ | ✘ | ✔ |
| Gestionar indisponibilidades | ✘ | ✔ | ✔ |
| Ver log de auditoría | ✔ (API) | ✔ | ✔ |
| Editar/borrar log de auditoría | ✘ | ✘ | ✘ (append-only) |

---

## 11. Gaps conocidos (no reportar como bug)

1. **Snapshot de tarifa al aprobar**: los campos `tarifa_aplicada` y `costo_estimado` de la asignación existen pero aún no se llenan al aprobar (pendiente de implementación). En el admin se verán vacíos.
2. **Bloqueo de login con múltiples instancias**: el contador de intentos fallidos es por proceso (cache local). En despliegue con varias instancias el límite efectivo puede ser mayor a 5 hasta que se configure un cache compartido (Redis/BD).
3. Integraciones Skills/SAP: fuera del alcance de Fase 1 (solo existen los campos `nro_persona_sap` y clusters).

---

## Anexo — Registro de ejecución

| Caso | Resultado (PASS/FAIL/BLOQ) | Evidencia | Observaciones | Probador | Fecha |
|---|---|---|---|---|---|
| AUT-01 | | | | | |
| … | | | | | |

Reportar los FAIL como issues en GitHub indicando: ID del caso, pasos reales, resultado obtenido vs. esperado, capturas y usuario utilizado.
