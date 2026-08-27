# QA — Plataforma de Gestión de Recursos (Inetum)

**Para:** Erika Castiblanco-Monroy · **Fecha:** 2026-08-26

Dos partes: primero el **manual** (cómo funciona el sistema y dónde ya han
aparecido errores) y después el **plan** con 156 casos. Todo lo que necesitas
está aquí; no hace falta acceso al repositorio ni instalar nada.

---

# Parte 1 · Manual

Esta primera parte no lista casos de prueba: explica **cómo está pensado el sistema** para que puedas diseñar los tuyos. Los casos vienen después.

Lo escribe quien construyó la aplicación, así que incluye lo que normalmente no se cuenta: dónde ya han aparecido errores y qué partes son frágiles.

---

## 1. Qué es esto

Una herramienta interna para **~30 ingenieros** que sustituye una planilla de Excel. Hace dos cosas que se miran entre sí:

| | Quién | Qué responde |
|---|---|---|
| **Planificación** (asignaciones) | El PM | *"Qué vamos a hacer"* — comprometo a Ana con el proyecto X por 40 h |
| **Legalización** (horas) | La propia persona | *"Qué hicimos"* — Ana declara en qué se le fue cada día |

**Que las dos no coincidan no es un fallo: es el producto.** Si a alguien se le asignaron 40 h y cargó 63, eso es una desviación que antes nadie veía hasta abrir la planilla.

Esa distinción explica muchas decisiones. Por ejemplo, las horas se registran contra el **proyecto** y no contra la asignación: alguien puede acabar trabajando en algo que nadie le asignó, y el sistema tiene que poder recogerlo en vez de impedirlo.

---

## 2. Los tres roles

| Rol | En una frase | Puntos sensibles |
|---|---|---|
| **Ingeniero** | Registra sus horas y sus vacaciones | **Nunca** debe ver costos ni tarifas. Solo ve **sus** datos |
| **PM** | Pide recursos para sus proyectos y aprueba las horas que se les imputan | Ve costos. **No** puede aprobar asignaciones |
| **Admin** | Aprueba asignaciones y administra los catálogos | Puede todo. Es el respaldo cuando un PM no está |

El rol **no vive en la aplicación**: viene de Microsoft Entra ID y se sincroniza en cada inicio de sesión. Si a alguien le cambian el rol en Entra, cambia en la aplicación la próxima vez que entre.

---

## 3. Las reglas que nunca deben romperse

Si encuentras una forma de saltarte alguna de estas, es un fallo grave aunque la pantalla no dé error.

1. **El Ingeniero nunca ve costos ni tarifas.** En ninguna pantalla, ninguna API, ningún informe. Un usuario sin rol asignado tampoco.
2. **Nada se borra de verdad.** Todo es *soft-delete*: la fila se queda en la base marcada como eliminada. Vale tanto para el botón de la ficha como para el borrado masivo de la lista.
3. **El registro de auditoría no se edita ni se borra.** Solo se añade.
4. **Nadie supera su jornada.** Ni al asignar (8,5 h/día máximo entre todos los proyectos) ni al legalizar.
5. **Solo lo aprobado cuenta.** Una vacación pendiente no bloquea capacidad. Una liberación solicitada no libera cupo. Una asignación solicitada no ocupa a nadie.
6. **Un día de horas aceptado es inmutable.** Solo quien aprueba puede devolverlo, y con motivo.
7. **El día tiene que cuadrar exacto.** Ni más ni menos que la jornada.
8. **El SSO no crea cuentas duplicadas.** Quien ya existe entra en su cuenta, con su historial.

---

## 4. Los estados

Cuatro máquinas de estado. La mayoría de los errores interesantes están en las transiciones.

**Asignación** — la pide un PM, la aprueba un Admin
```
SOLICITADA ──► APROBADA ──► REVOCADA
     └───────► RECHAZADA
```
Solo `APROBADA` consume capacidad.

**Liberación** — congela temporalmente una asignación
```
SOLICITADA ──► APROBADA ──► ANULADA
     └───────► RECHAZADA
```
Al aprobar hay que elegir política: **RECOMPUTAR** (empuja la fecha de fin, conserva horas) o **REDUCIR** (mantiene la fecha, baja las horas).

**Novedad** (vacaciones o permiso) — la pide la persona, la aprueba un Admin
```
PENDIENTE ──► APROBADA
    ├───────► RECHAZADA
    └───────► (cancelada por quien la pidió, solo mientras esté pendiente)
```
Solo `APROBADA` hace que esos días dejen de ser hábiles.

**Día de horas** — lo registra la persona, lo aprueba el PM
```
ABIERTO ──► REGISTRADO ──► APROBADO
   ▲              │
   └── devolver ──┘   (con motivo obligatorio)
```

---

## 5. Vocabulario del dominio

**Jornada.** Lunes a jueves **8,5 h**, viernes **8 h** — 42 h semanales. Es la jornada legal colombiana desde el 15 de julio de 2026. Solo se gestionan recursos de Colombia.

**Día hábil.** No lo es un fin de semana, un festivo de Colombia, un día no laborable de empresa, ni un día con **ausencia aprobada** de esa persona. En un día no hábil no hay horas que registrar.

**Facturable vs interno.** Un proyecto interno (`INT-DEPART`, `INT-MGMT`) es un proyecto normal marcado como no facturable. Las horas de estudio y entrenamiento tampoco son facturables. Esta separación es la que alimenta el informe de horas cobrables.

**Bench.** Alguien sin carga asignada ese día.

**Cesión de horas.** Pasar horas de una asignación a otra. Mientras el destino no se apruebe, las horas quedan **reservadas**: la carga no baja para terceros, para que nadie ocupe ese cupo.

**Liberación.** Congelar una ventana de una asignación para que el recurso quede libre en esos días.

**Capacidad cruzada.** Nadie puede tener más de 8,5 h/día sumando **todos** sus proyectos. Se valida al aprobar.

---

## 6. Los dos alias de identidad

Esto es peculiar de este despliegue y conviene entenderlo, porque si se rompe **no da ningún error visible**.

El tenant de Microsoft no tiene verificado el dominio corporativo. Las cuentas son `nombre@inetumoffshore.onmicrosoft.com`, pero en la plataforma las personas son `nombre@inetum.com`.

- **Alias de dominio.** Al entrar, el sistema traduce el dominio. Tu cuenta `erika.castiblanco-monroy@inetumoffshore.onmicrosoft.com` entra en la cuenta existente `erika.castiblanco-monroy@inetum.com`.
- **Alias de usuario.** Para cuentas cuyo nombre no deriva de un email de negocio: `admin@inetumoffshore…` entra como el superusuario `inetum_admin`.

**Cómo se rompería sin avisar:** en vez de entrar en tu cuenta, crearía una nueva vacía. La pantalla se vería perfecta; simplemente habría dos "Erika" y el historial se quedaría en la otra. **Por eso hay un caso que cuenta los usuarios antes y después de entrar** (SSO-05).

---

## 7. Dónde ya han aparecido errores

No es una lista teórica: todos estos se encontraron durante el desarrollo. Son las zonas donde merece la pena insistir.

**Decimales con coma.** El sistema está en español, así que Django escribe `8,5`. Cuando ese número llega a JavaScript, `parseFloat("8,5")` devuelve **8** — corta en la coma. Provocó que la jornada valiera 8 en el navegador y el día no cuadrara nunca. *Prueba cualquier cosa con medias horas y mira si los totales cuadran.*

**Borrado individual vs masivo.** Borrar desde la ficha y borrar seleccionando en la lista son **dos caminos distintos** en Django. El masivo se saltaba el soft-delete y borraba de verdad. *Cuando pruebes borrados, prueba los dos.*

**Comentarios que se imprimen.** Los comentarios de plantilla de una sola línea, partidos en dos, se renderizan como texto visible. Pasó tres veces. *Si ves texto raro entre llaves, es esto.*

**Orden de operaciones.** Al aprobar una vacación que cruza asignaciones, los ajustes tienen que hacerse **antes** de marcar la ausencia como aprobada; al revés, los días ya no son hábiles y el cálculo da cero. *Prueba aprobar ausencias sobre días ocupados.*

**Mensajes que mienten.** Aprobar dos veces daba *"no eres PM de este proyecto"* en lugar de *"ya está aprobado"*. El permiso y el estado son preguntas distintas. *Cuando un mensaje de error te desoriente, repórtalo: probablemente esté mezclando dos cosas.*

**Concurrencia.** Varias pantallas releen el dato bajo bloqueo antes de actuar, porque con dos pestañas abiertas se podía cerrar dos veces el mismo día o aprobar dos veces. *Prueba con dos pestañas.*

---

## 8. Cosas que no son fallos

1. **La primera carga tarda 10-30 s** tras un rato sin uso. La aplicación se apaga sola cuando nadie la usa, para no cobrar por estar encendida.
2. **Una sola réplica**, sin autoescalado. Decisión consciente hasta ver rendimiento real.
3. **No hay horas extra.** Un día no puede pasar de la jornada.
4. **No se calcula dinero por hora.** Se registran horas; la nómina vive en otro sistema.
5. **Un solo PM basta.** Si un día mezcla proyectos de varios PM, cualquiera de ellos lo aprueba entero.
6. **No existe pedir reapertura** de un día ya aprobado. Solo quien aprueba puede devolver uno *registrado*.

---

## 9. Cómo reportar

Para cada fallo:

- **ID del caso** si venía del plan, o una descripción corta si lo encontraste explorando.
- **Con qué cuenta.** Imprescindible: la misma pantalla enseña cosas distintas según el rol, y sin ese dato el fallo no se puede reproducir. El nombre y el rol salen arriba a la derecha.
- **Qué esperabas y qué pasó.**
- **Captura**, y la fecha si es algo del calendario (los festivos y los fines de semana cambian el comportamiento).

Si algo te parece raro pero no sabes si es un fallo, repórtalo igual. Varias de las decisiones de esta lista se tomaron a ojo y pueden estar mal: que no dé error no significa que sea correcto.


---

# Parte 2 · Plan de pruebas

**Versión:** 2.0 · **Fecha:** 2026-08-26
**Alcance:** aplicación completa desplegada en Azure — autenticación local y SSO, RBAC, catálogos, calendario, asignaciones, cesiones, liberaciones, novedades, legalización de horas y su aprobación.

Cada caso indica pasos y resultado esperado. QA registra **PASS / FAIL / BLOQUEADO** y evidencia (captura o respuesta de API) en el anexo final.

> **La v1.0 cubría solo la Fase 1.** Esta versión añade SSO con Entra ID, novedades, legalización de horas, aprobación de horas y el despliegue en Azure. Los casos de la v1.0 siguen vigentes con su misma numeración salvo donde se indica lo contrario.

---

## 1. Entorno y cuentas

### 1.1 Dónde se prueba

**Todo se prueba aquí**, desde el navegador. No hace falta instalar nada.

> ### https://ca-platgestion-prod-eus2-001.redocean-b9f4e1e1.eastus2.azurecontainerapps.io

**La primera carga tarda 10-30 segundos** si nadie ha usado la aplicación en un rato: se apaga sola cuando está ociosa para no gastar. A partir de ahí va rápida. **No es un fallo** — es lo primero con lo que te vas a topar.

Tres casos del bloque INF y la limpieza final los ejecuta el equipo de desarrollo, no tú; están marcados como tales.

### 1.2 Tus cuentas

El plan recorre los tres roles, así que hacen falta tres accesos. Todas entran por **"Iniciar sesión con Microsoft"**.

| Cuenta (UPN de Entra) | Rol | Cuándo usarla |
|---|---|---|
| `erika.castiblanco-monroy@inetumoffshore.onmicrosoft.com` | Ingeniero | **Tu cuenta.** Bloques AUT, SSO, RBAC, NOV y HOR |
| `qa.pm@inetumoffshore.onmicrosoft.com` | PM | Bloques SOL, CES, LIB y la parte de PM en HAP |
| `qa.admin@inetumoffshore.onmicrosoft.com` | Admin | Bloques MAE, APR, la aprobación de novedades y la parte de Admin en HAP |

> **`qa.pm` y `qa.admin` son cuentas de prueba creadas para esta ronda.** Se usan para no tener que cambiarle el rol a nadie de verdad. **`qa.admin` puede aprobar y revocar asignaciones reales**, así que hay que desactivarlas en Entra cuando termine la ronda.

Cuentas de apoyo, para casos concretos:

| Cuenta | Rol | Para qué |
|---|---|---|
| `test.ingeniero@inetumoffshore.onmicrosoft.com` | Ingeniero | **Sin asignaciones**: solo para HOR-10 |
| `carmen.leon@inetumoffshore.onmicrosoft.com` | PM | **PM ajeno** a `QA-001`: solo para HAP-02 |

### 1.3 Datos preparados para ti

Tres proyectos de prueba, cada uno con un papel distinto:

| Proyecto | PM | Estado | Para qué |
|---|---|---|---|
| `QA-001` Portal de Clientes QA | `qa.pm` | Activo | **Estás asignada.** Aparece en `/horas/` dentro del rango |
| `QA-002` Migración SAP QA | `carmen.leon` | Activo | PM distinto: sirve para comprobar que la cola de aprobación filtra |
| `QA-003` Mantenimiento Legacy QA | `qa.pm` | **Cerrado** | Comprobar que un proyecto cerrado **no** aparece donde no debe |

- **Tu asignación aprobada a `QA-001`** va del **2026-08-06 a hoy**. Ese rango es lo que hace verificable el filtro: dentro, `QA-001` aparece en el desplegable de `/horas/`; fuera, desaparece (HOR-11 y HOR-12).
- `qa.pm` también tiene recurso y asignación, para poder probar que un PM legaliza sus propias horas (HOR-27).
- Proyectos internos: `INT-DEPART`, `INT-MGMT` (no facturables). Salen **siempre**, con o sin asignación.
- Actividades sin proyecto: **Entrenamiento** y **Estudio**. `Formación` está retirada y **no debe aparecer**.
- Jornada: **lunes a jueves 8,5 h · viernes 8 h** (42 h semanales desde 2026-07-15).

> **Al terminar la ronda**, avisa al equipo: retiran los proyectos de prueba, tus asignaciones a ellos y las cuentas `qa.*` con un solo comando. No tienes que deshacer nada tú.

### 1.4 Orden sugerido

El plan tiene dependencias: no se puede aprobar lo que no se ha registrado.

1. **AUT y SSO** con tu cuenta — confirma que entras y con qué rol.
2. **RBAC** alternando las tres cuentas.
3. **MAE y CAL** con `qa.admin` — deja los datos listos.
4. **SOL, CES, LIB** con `qa.pm`; sus aprobaciones con `qa.admin`.
5. **NOV** con tu cuenta; las aprobaciones con `qa.admin`.
6. **HOR** con tu cuenta — es el bloque más largo.
7. **HAP** con `qa.pm` y `qa.admin`, sobre los días que registraste en el paso 6.
8. **DASH, AUD, INF** al final.

---

## 2. AUT — Autenticación

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| AUT-01 | Login local correcto | `/login/` con usuario y contraseña válidos | Entra al dashboard |
| AUT-02 | Login incorrecto | Contraseña errónea | Vuelve al formulario con error; **no revela** si el usuario existe |
| AUT-03 | Bloqueo tras 5 intentos | Fallar 5 veces; intentar una 6ª **con la contraseña correcta** | 403 "Demasiados intentos fallidos. Espere 15 minutos…" |
| AUT-04 | El bloqueo no es global | Con AUT-03 activo, loguear otra cuenta en otro navegador | Entra sin problema |
| AUT-05 | Rutas protegidas sin sesión | Abrir `/dashboard/`, `/horas/`, `/novedades/` sin sesión | Redirige a `/login/?next=…` y vuelve a la página pedida tras entrar |
| AUT-06 | API sin sesión | GET `/api/asignaciones/` sin sesión | 403, sin exponer datos |
| AUT-07 | Logout | `/logout/` | Cierra sesión y vuelve a `/login/` |
| AUT-08 | El login del admin usa la página propia | Abrir `/admin/` sin sesión | Redirige a `/login/?next=/admin/`, no al login de Django |
| AUT-09 | **Quién está dentro** | Logueado con cualquier cuenta, mirar la esquina superior derecha | Muestra nombre y **rol** (Admin / PM / Ingeniero) y el botón de salir |
| AUT-10 | El rol mostrado coincide | Repetir AUT-09 con las tres cuentas de distinto rol | La etiqueta cambia correctamente en cada una |

---

## 3. SSO — Entra ID

Solo en Azure. **El login local debe seguir funcionando en todos estos casos**: es la vía de contingencia.

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| SSO-01 | El botón aparece | Abrir `/login/` en Azure | Se ve "Iniciar sesión con Microsoft" **y** el formulario de usuario/contraseña |
| SSO-02 | Redirección a Microsoft | Pulsar el botón | Lleva a `login.microsoftonline.com`, **sin** pantalla de "se necesita aprobación del administrador" |
| SSO-03 | Primer inicio pide cambiar contraseña | Entrar con una cuenta nueva | Microsoft obliga a cambiarla antes de continuar |
| SSO-04 | **Alias de dominio** | Entrar con tu cuenta `erika.castiblanco-monroy@inetumoffshore.onmicrosoft.com` | Entras en tu cuenta **existente** `erika.castiblanco-monroy@inetum.com`, con tu historial. **No** se crea un usuario nuevo |
| SSO-05 | El total de usuarios no crece | Contar usuarios en `/admin/auth/user/` antes y después de SSO-04 | El número **no cambia** |
| SSO-06 | **Alias de usuario** | Entrar como `admin@inetumoffshore.onmicrosoft.com` | Entra como `inetum_admin`; su email sigue siendo `jose.barrera-cocunubo@inetum.com`, **no** `admin@inetum.com` |
| SSO-07 | Rol sincronizado desde Entra | Entrar como `qa.pm@…` | Queda en el grupo **PM**; ve Solicitudes, Cesiones, Liberaciones y Aprobar horas |
| SSO-08 | Sin rol en Entra no se entra | Quitar el rol a `test.ingeniero` en Entra e intentar entrar | Microsoft impide el acceso a la aplicación |
| SSO-09 | Cambio de rol en Entra manda | Cambiar en Entra el rol de una cuenta de Ingeniero a PM y volver a entrar | En Django cambia de grupo; el menú se ajusta |
| SSO-10 | El login local sobrevive | Con SSO activo, entrar con usuario y contraseña locales | Funciona igual |
| SSO-11 | El usuario SSO no queda atrapado | Entrar por SSO con una cuenta que tuviera cambio de contraseña pendiente | Entra normalmente; **no** lo manda al formulario de cambio |
| SSO-12 | Aviso de caducidad | Como Admin, mirar la cabecera | Solo aparece la franja si faltan menos de 60 días para que caduque el secreto. Hoy **no** debe verse (caduca 2028-08-25) |

---

## 4. RBAC — Control de acceso

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| RBAC-01 | **El Ingeniero nunca ve costos** | Como Ingeniero, recorrer dashboard, detalle de recurso, `/horas/` y las APIs accesibles | En **ninguna** pantalla aparece tarifa ni costo |
| RBAC-02 | El Ingeniero no ve emails ajenos | Como Ingeniero, GET `/api/dashboard/ocupacion/` | La respuesta no trae `email` |
| RBAC-03 | Menú por rol | Comparar el menú superior como Ingeniero, PM y Admin | El Ingeniero **no** ve Solicitudes, Cesiones, Liberaciones ni Aprobar horas. Nadie ve enlaces que devuelvan 403 |
| RBAC-04 | Vistas de PM cerradas al Ingeniero | Como Ingeniero, abrir `/solicitud/`, `/cesion/`, `/liberacion/`, `/horas/aprobar/` | 403 en las cuatro |
| RBAC-05 | Revisar novedades es solo de Admin | Abrir `/novedades/revisar/` como PM | 403 |
| RBAC-06 | **El Ingeniero solo se ve a sí mismo** | Como Ingeniero, abrir `/dashboard/` | Aparece **una sola fila**: la suya. No ve la ocupación ni el bench del equipo |
| RBAC-07 | El PM ve a todo el equipo | Mismo dashboard como PM | Aparecen todos los recursos asignables |
| RBAC-08 | El PM no aprueba asignaciones | Como PM, intentar aprobar una asignación desde `/admin/` | Error "Se requiere rol Admin"; el estado no cambia |
| RBAC-09 | Solo Admin escribe catálogos | Como PM, POST `/api/recursos/` | 403. Como Admin → 201 |

---

## 5. MAE — Datos maestros

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| MAE-01 | Crear recurso | `/admin/core/recurso/add/` con nombre, email y banda | Se crea y aparece en el dashboard |
| MAE-02 | Email único | Crear otro recurso con un email ya usado | Error de validación |
| MAE-03 | **Crear proyecto de cliente** | `/admin/core/proyecto/add/`, código con formato SAP, `facturable` marcado | Se crea; aparece en el buscador de solicitudes |
| MAE-04 | **Crear proyecto interno** | Igual pero **desmarcando `facturable`** | Se crea; aparece en `/horas/` para **todos**, incluso sin asignación |
| MAE-05 | Código SAP no válido solo avisa | Crear con código `PRUEBA-1` | Se guarda con aviso (`SAP_VALIDACION_ESTRICTA=False`) |
| MAE-06 | Editar estado y facturable desde la lista | En `/admin/core/proyecto/`, cambiar `estado` y `facturable` sin entrar al detalle | Se guardan los cambios |
| MAE-07 | Tarifa con vigencia | Añadir una `TarifaVigente` a un recurso con `fecha_desde` futura | Se guarda; el costo de asignaciones activas se recomputa (aviso en pantalla) |
| MAE-08 | La tarifa es append-only | Intentar **editar** una tarifa ya registrada | No se puede modificar, solo añadir otra |
| MAE-09 | **Soft-delete individual** | Borrar un recurso desde su ficha | Desaparece de las listas pero la fila sigue en la base (`deleted_at` con fecha) |
| MAE-10 | **Soft-delete masivo** | Anota el código de un proyecto de prueba. Selecciónalo en la lista y usa "Eliminar seleccionados". Después intenta **crear uno nuevo con ese mismo código** | Desaparece de la lista, pero el código **sigue ocupado**: al crearlo da error de duplicado. Eso demuestra que la fila no se borró de verdad |
| MAE-11 | Un recurso que sale de la empresa | Marcar `activo = False` en su ficha | Deja de aparecer como asignable; su historial se conserva |
| MAE-12 | Catálogo de actividades | `/admin/legalizacion/tipoactividad/` | Se ven Proyecto, Entrenamiento y Estudio activos, y Formación **inactiva** |

---

## 6. CAL — Calendario y jornada

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| CAL-01 | Feriados de Colombia | GET `/api/calendario/feriados/?year=2026` | Incluye `2026-07-20` y `2026-08-07` |
| CAL-02 | Año inválido | `?year=abc` y `?year=1800` | 400 con mensaje, no error 500 |
| CAL-03 | Fecha fin salta fin de semana | Solicitud por horas: inicio lunes, 40 h, intensidad 8 | Termina el viernes de esa semana |
| CAL-04 | Fecha fin salta feriado | Inicio jueves 2026-07-16, 24 h, intensidad 8 | Termina el 21; **salta el lunes 20** |
| CAL-05 | Día no laborable global | Crear uno y repetir un cálculo que lo cruce | Ese día no cuenta |
| CAL-06 | **Jornada lunes a jueves** | En `/horas/`, abrir un miércoles | La jornada exigida es **8,5 h** |
| CAL-07 | **Jornada del viernes** | En `/horas/`, abrir un viernes | La jornada exigida es **8 h** |
| CAL-08 | La semana suma 42 h | Sumar las jornadas de lunes a viernes | 42 h exactas |

---

## 7. SOL — Solicitud de asignaciones (PM)

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| SOL-01 | Buscar disponibilidad | `/solicitud/` con un rango de fechas | Lista de recursos con días y horas disponibles |
| SOL-02 | Crear por horas | Modo "por horas": 40 h, intensidad 8 | Calcula fecha fin sobre días hábiles; queda **SOLICITADA** |
| SOL-03 | Crear por días | Modo "por días hábiles" | Igual, calculando desde los días |
| SOL-04 | Crear por rango | Modo "por rango de fechas" | Usa las fechas dadas |
| SOL-05 | Jornada completa | Marcar "jornada completa" | Consume el tope de cada día (8,5 / 8), no una intensidad fija |
| SOL-06 | **Solicitud recurrente** | `/solicitud/recurrente/`, varias semanas | Crea una **serie** de asignaciones de un día; todas comparten identificador de serie |
| SOL-07 | Tope de semanas | Pedir más semanas de las permitidas | Error de validación |
| SOL-08 | **Capacidad cruzada 8 h/día** | Asignar a alguien que ya tiene 8,5 h aprobadas ese día | Rechaza indicando el día y las horas en conflicto |
| SOL-09 | Costo estimado | Como PM, ver el resumen antes de crear | Muestra costo estimado con la tarifa vigente de cada día |
| SOL-10 | Solicitar no consume capacidad | Crear una solicitud y mirar el dashboard | La ocupación **no cambia** hasta aprobarla |

---

## 8. APR — Aprobación de asignaciones (Admin)

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| APR-01 | Aprobar | Como Admin, aprobar una solicitud | Pasa a **APROBADA**; la ocupación aparece en el dashboard |
| APR-02 | Snapshot de tarifa | Tras APR-01, mirar la asignación | `tarifa_aplicada` y `costo_estimado` quedan rellenos |
| APR-03 | **Rechazar** | Rechazar una solicitud | Pasa a RECHAZADA; no consume capacidad |
| APR-04 | **Revocar (cancelar)** | Revocar una asignación ya aprobada | Pasa a REVOCADA; libera la capacidad; queda en auditoría |
| APR-05 | **Primero en aprobar gana** | Con dos solicitudes que se solapan para el mismo recurso, aprobar ambas | La primera pasa; la segunda es rechazada por capacidad |
| APR-06 | Recomputo por cambio de tarifa | Registrar una tarifa nueva para un recurso con asignaciones activas | Se recalcula el costo; queda `RECOMPUTO_TARIFA` en auditoría con actor vacío |
| APR-07 | Reajustar la fecha fin | Provocar un recomputo (p. ej. aprobando una ausencia con política RECOMPUTAR) | La fecha fin se empuja; las horas se conservan |

---

## 9. CES — Cesión de horas

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| CES-01 | Ceder horas | `/cesion/`, ceder horas de una asignación aprobada a otra | Crea la cesión y una asignación destino SOLICITADA |
| CES-02 | Las horas quedan reservadas | Mirar el dashboard antes de aprobar el destino | La carga bruta **no baja** para terceros: nadie puede ocupar ese cupo |
| CES-03 | Aprobar el destino | Aprobar la asignación destino | Ahora sí se descuentan de la original |
| CES-04 | **Anular la cesión** | Rechazar o revocar el destino | Las horas vuelven a la asignación original |
| CES-05 | No se cede más de lo que hay | Ceder más horas de las disponibles ese día | Error de validación |

---

## 10. LIB — Liberación de recursos

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| LIB-01 | Solicitar liberación | `/liberacion/`, elegir asignación y ventana | Queda **SOLICITADA**; no libera cupo todavía |
| LIB-02 | Una solicitud no libera nada | Mirar el dashboard | La ocupación no cambia |
| LIB-03 | **Aprobar con RECOMPUTAR** | Como Admin, aprobar con esa política | La ventana se congela; la **fecha fin se empuja**; las horas se conservan |
| LIB-04 | **Aprobar con REDUCIR** | Igual con la otra política | La ventana se congela; **bajan las horas**; la fecha fin no cambia |
| LIB-05 | Rechazar | Rechazar una solicitud | No surte ningún efecto |
| LIB-06 | **Anular una aprobada** | Anular una liberación ya aprobada | Revierte los efectos; si otro proyecto ocupó la ventana, **falla** con mensaje claro |
| LIB-07 | Solo Admin aprueba | Como PM, intentar aprobar | No puede |

---

## 11. NOV — Novedades (vacaciones y permisos)

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| NOV-01 | Registrar vacaciones | Como Ingeniero, `/novedades/`, tipo Vacaciones | Queda **pendiente de aprobación** |
| NOV-02 | El motivo solo en permisos | Cambiar el tipo entre Vacaciones y Permiso | El campo "Motivo" solo aparece con **Permiso** |
| NOV-03 | **Pendiente no bloquea capacidad** | Con la novedad pendiente, mirar el dashboard | Los días siguen apareciendo disponibles |
| NOV-04 | Cada quien ve solo las suyas | Como otro Ingeniero, abrir `/novedades/` | No ve las de nadie más |
| NOV-05 | Cancelar una pendiente | Cancelar la propia novedad | Desaparece del listado; la fila se conserva en base |
| NOV-06 | No se cancela una aprobada | Intentar cancelar tras aprobarla | Lo impide con mensaje claro |
| NOV-07 | Sin solape | Pedir dos novedades que se crucen | Rechaza la segunda |
| NOV-08 | Retroactividad limitada | Pedir una novedad de hace más de 60 días | La rechaza |
| NOV-09 | **Aprobar (Admin)** | `/novedades/revisar/`, aprobar una que no cruce asignaciones | Pasa a Aprobada; los días dejan de ser hábiles |
| NOV-10 | Rechazar con motivo | Rechazar indicando motivo | El solicitante ve el motivo en su historial |
| NOV-11 | **Ausencia al 100 % en el dashboard** | Tras NOV-09, mirar esos días en el dashboard | Salen con trama morada y **"100 %"**, no como día libre |
| NOV-12 | El fin de semana no es ausencia | Mirar un sábado | Sigue gris y al **0 %** |
| NOV-13 | **Cruce con asignación: hay que elegir** | Pedir una novedad sobre días con asignación aprobada e intentar aprobarla | **No** aparece el botón "Aprobar" suelto: obliga a elegir RECOMPUTAR o REDUCIR por cada asignación |
| NOV-14 | Aprobar y ajustar | Elegir RECOMPUTAR y confirmar | La novedad queda aprobada y la fecha fin de la asignación se empuja |
| NOV-15 | Rastro en auditoría | Tras NOV-14, revisar el log de la asignación | Aparecen `SOLICITAR_LIBERACION` y `LIBERAR` |
| NOV-16 | El PM no aprueba novedades | Como PM, abrir `/novedades/revisar/` | 403 |

---

## 12. HOR — Legalización de horas (Ingeniero)

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| HOR-01 | Abrir la pantalla | Como Ingeniero, `/horas/` | Muestra el día de hoy, el selector con flechas y la lista de días sin registrar |
| HOR-02 | Navegar entre días | Usar las flechas ← → | Cambia de día; la flecha "siguiente" está inactiva en el día de hoy |
| HOR-03 | No se legaliza el futuro | Elegir una fecha futura | No lo permite |
| HOR-04 | **Fin de semana** | Abrir un sábado | Mensaje "no hay horas que registrar"; **sin** formulario |
| HOR-05 | **Festivo** | Abrir el 2026-08-07 | Igual, indicando que es festivo |
| HOR-06 | **Día con vacaciones aprobadas** | Abrir un día con una novedad aprobada | Mensaje de vacaciones/permiso; sin formulario. **No hay que teclear nada** |
| HOR-07 | Ayuda de cada actividad | Cambiar el desplegable de Actividad | Bajo él aparece una frase distinta explicando cuándo usar cada una |
| HOR-08 | Formación no aparece | Revisar el desplegable | Solo Proyecto, Entrenamiento y Estudio |
| HOR-09 | El proyecto solo cuando aplica | Elegir Estudio y luego Proyecto | El campo Proyecto **se oculta** con Estudio y aparece con Proyecto |
| HOR-10 | **Proyectos filtrados (sin asignación)** | Como `test.ingeniero`, abrir el desplegable | Solo `INT-DEPART` e `INT-MGMT`. **Ningún proyecto de cliente** |
| HOR-11 | **Proyectos filtrados (con asignación)** | Con tu cuenta, abrir un día **del 06 de agosto en adelante** | Además de los internos, aparece **`QA-001`** |
| HOR-12b | Un proyecto cerrado no aparece | Buscar `QA-003` en el desplegable | **No** aparece: está cerrado |
| HOR-12 | El filtro depende del día | Con tu cuenta, abrir un día **anterior al 06 de agosto** | `QA-001` **desaparece**; quedan solo los internos |
| HOR-13 | **Nada se guarda al añadir** | Añadir dos actividades a la lista y **recargar la página sin guardar** | La lista se pierde: no se guardó nada |
| HOR-14 | El contador avanza | Ir añadiendo actividades | El marcador sube (`x / 8,5 h`) y la barra se llena |
| HOR-15 | **Decimales correctos** | Con 4 h puestas, añadir 4,5 h en un día de 8,5 | Lo acepta y el día cuadra. **No** debe decir "quedan 4 h" |
| HOR-16 | No se pasa de la jornada | Intentar añadir más horas de las que caben | Avisa cuántas quedan por asignar |
| HOR-17 | Media hora | Intentar añadir 1,3 h | Lo rechaza: solo bloques de media hora |
| HOR-18 | El detalle es obligatorio | Añadir sin escribir qué se hizo | Lo rechaza |
| HOR-19 | Guardar el día | Con la lista completa, pulsar "Guardar día" | Guarda y **muestra el resumen**; el día sigue **abierto** |
| HOR-20 | Corregir antes de aceptar | En el resumen, pulsar "Corregir" | Vuelve al formulario |
| HOR-21 | Guardar reemplaza | Corregir, cambiar las actividades y guardar otra vez | Quedan **solo** las nuevas, no se suman a las anteriores |
| HOR-22 | **No se acepta si no cuadra** | Guardar solo 6 h de una jornada de 8,5 y mirar el resumen | Dice cuántas faltan y **no ofrece** "Aceptar día" |
| HOR-23 | **Aceptar el día** | Con el día cuadrado, pulsar "Aceptar día" y confirmar | Queda **Registrado**, pendiente de aprobación |
| HOR-24 | **Inmutable tras aceptar** | Volver a ese día | Se ve en solo lectura, sin formulario ni botón de corregir |
| HOR-25 | Facturable vs interno | En el resumen, mirar el desglose | Separa horas facturables de no facturables; las internas y las de estudio **no** son facturables |
| HOR-26 | Días pendientes | Mirar la lista inferior | Solo días **hábiles** sin registrar; los que ya se aceptaron desaparecen |
| HOR-27 | Un PM también legaliza | Como PM, abrir `/horas/` | Puede registrar sus propias horas |

---

## 13. HAP — Aprobación de horas (PM y Admin)

> **Quién aprueba qué.** El PM aprueba los días que tocan **sus** proyectos. El Admin ve **todos** — es la válvula de escape para cuando un PM está de vacaciones, se va o simplemente tarda, y la **única** vía para un día sin ningún proyecto, que no tiene PM que lo reclame. Este bloque necesita `qa.pm`, `qa.admin` y `carmen.leon` (como PM ajeno).


| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| HAP-01 | El PM ve lo suyo | Como `qa.pm`, abrir `/horas/aprobar/` tras registrar días con horas de `QA-001` | Ve esos días: es el PM del proyecto |
| HAP-02 | Un PM ajeno no lo ve | Como `carmen.leon`, que es PM de `QA-002` pero **no** de `QA-001` | La cola **no** incluye esos días |
| HAP-03 | **El Admin lo ve todo** | Como Admin, abrir la misma pantalla | Ve todos los días pendientes, también los de proyectos ajenos |
| HAP-04 | Al Admin se le avisa | Como Admin, mirar arriba de la cola | Franja explicando que ve todo y que sirve de respaldo |
| HAP-05 | Al PM no se le muestra esa nota | Como PM | No aparece esa franja |
| HAP-06 | **Día sin proyecto: solo Admin** | Registrar un día entero de Estudio y mirar la cola | El PM **no** lo ve; el Admin **sí**. Es la única vía para aprobarlo |
| HAP-07 | Desglose visible | Mirar una tarjeta de la cola | Muestra cada actividad, su detalle y sus horas — no solo el total |
| HAP-08 | Marca de proyecto propio | Como PM, mirar las líneas | Sus proyectos llevan la etiqueta "Tu proyecto" |
| HAP-09 | **Aprobar** | Pulsar Aprobar y confirmar | Pasa a Aprobado; el ingeniero lo ve con la firma de quien aprobó |
| HAP-10 | **El Admin aprueba en lugar del PM** | Como Admin, aprobar un día de un proyecto ajeno | Lo aprueba; queda su nombre como aprobador |
| HAP-11 | No se aprueba dos veces | Intentar aprobar un día ya aprobado | Error diciendo **"ya está aprobado"** (no un mensaje de permisos) |
| HAP-12 | El Ingeniero no aprueba | Como Ingeniero, abrir `/horas/aprobar/` | 403 |
| HAP-13 | **Devolver exige motivo** | Pulsar "Devolver" y enviar sin texto | No lo permite |
| HAP-14 | **Devolver reabre el día** | Devolver con motivo | El día vuelve a estar abierto para su autor |
| HAP-15 | **El motivo se ve** | Como el ingeniero, volver a ese día | Franja naranja "Te devolvieron este día para corregir" con el motivo |
| HAP-16 | El aviso también en el resumen | Corregir, guardar y mirar el resumen | La franja **sigue visible**: no puede re-aceptarse a ciegas |
| HAP-17 | Aprobar limpia el motivo | Corregir, aceptar y aprobar | El día queda aprobado **sin** el reproche pegado |

---

## 14. DASH — Dashboard

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| DASH-01 | Heatmap | Como PM, cargar un mes | Matriz recurso × día con porcentajes de ocupación |
| DASH-02 | Bench | Mirar las tarjetas superiores | Totales de recursos, ocupados hoy y en bench |
| DASH-03 | Rango máximo | Pedir más de 90 días | Error controlado |
| DASH-04 | PM y Admin fuera del heatmap | Revisar la lista de recursos | Los recursos cuyo usuario es PM o Admin no aparecen |
| DASH-05 | Jornada completa al 100 % | Mirar una asignación de jornada completa | Sale al 100 % todos los días, no al 94 % |

---

## 15. AUD — Auditoría y trazabilidad

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| AUD-01 | Se registran los cambios de estado | Aprobar, rechazar y revocar asignaciones | Cada acción deja su entrada con actor y fecha |
| AUD-02 | **Append-only** | Intentar editar o borrar una entrada del log | No es posible |
| AUD-03 | Actor del sistema | Revisar un `RECOMPUTO_TARIFA` | El actor está vacío (acción automática) |
| AUD-04 | Nada se borra de verdad | Tras MAE-09 y MAE-10, consultar la base | Las filas siguen ahí con `deleted_at` |

---

## 16. INF — Infraestructura y despliegue

| ID | Título | Pasos | Resultado esperado |
|---|---|---|---|
| INF-01 | Salud | GET `/healthz/` | 200 con `{"estado":"ok","base_datos":"ok"}` |
| INF-02 | Redirección a HTTPS | GET por `http://` | 301 a `https://` |
| INF-03 | Estáticos | Cargar cualquier página | CSS y JS cargan; los estáticos llevan hash en el nombre |
| INF-04 | **Arranque en frío** | Dejar la app sin usar 10 min y volver a entrar | Tarda 10-30 s la primera vez. **Es lo esperado**, no un bug |
| INF-05 | Guard de suscripción · **lo ejecuta el equipo** | Pídeselo a quien mantiene el despliegue: lanza el plan de Terraform apuntando a otra suscripción | Falla con "Suscripción no permitida". Anota el resultado que te reporten |
| INF-06 | Migraciones idempotentes · **lo ejecuta el equipo** | Pide que lancen el job de migraciones dos veces seguidas | Ambas terminan correctamente y no se duplican datos |

---

## 17. Matriz rol × acción

| Acción | Ingeniero | PM | Admin |
|---|:---:|:---:|:---:|
| Ver **su** ocupación | ✔ | ✔ | ✔ |
| Ver la ocupación **del equipo** | ✘ | ✔ | ✔ |
| Ver tarifas y costos | ✘ | ✔ | ✔ |
| Ver emails de recursos | ✘ | ✔ | ✔ |
| Crear solicitudes de asignación | ✘ | ✔ | ✔ |
| Aprobar / rechazar / revocar asignaciones | ✘ | ✘ | ✔ |
| Ceder horas y solicitar liberaciones | ✘ | ✔ | ✔ |
| Aprobar liberaciones | ✘ | ✘ | ✔ |
| Registrar **sus** novedades | ✔ | ✔ | ✔ |
| Aprobar novedades | ✘ | ✘ | ✔ |
| Legalizar **sus** horas | ✔ | ✔ | ✔ |
| **Aprobar horas** | ✘ | ✔ (sus proyectos) | ✔ (todas) |
| CRUD de catálogos | ✘ | ✘ | ✔ |
| Editar o borrar auditoría | ✘ | ✘ | ✘ |

---

## 18. Comportamientos conocidos (no reportar como bug)

1. **Arranque en frío de 10-30 s** tras inactividad. Es el precio de `min-replicas 0`, decidido a propósito.
2. **Una sola réplica.** Sin autoescalado, por decisión: se espera a ver rendimiento real.
3. **Sin horas extra.** Un día no puede pasar de la jornada. El módulo de extras está fuera de alcance por ahora.
4. **Sin nómina ni recargos.** Se registran horas; no se calcula lo que valen.
5. **Reapertura de un día aprobado.** Todavía no existe circuito para que el ingeniero *pida* reabrir un día ya aprobado; solo quien aprueba puede devolver uno **registrado**.
6. **Un solo PM basta.** Si un día mezcla proyectos de varios PM, cualquiera de ellos puede aprobarlo entero.
7. **Solo Colombia.** No se gestionan recursos de España ni su jornada.

---

## Anexo — Registro de ejecución

| Caso | Resultado | Evidencia | Observaciones | Probador | Fecha |
|---|---|---|---|---|---|
| AUT-01 | | | | | |
| … | | | | | |

Reportar cada FAIL con: ID del caso, pasos reales, resultado obtenido frente al esperado, captura y **cuenta utilizada** (el rol cambia lo que se ve, así que sin ese dato el fallo no se puede reproducir).

