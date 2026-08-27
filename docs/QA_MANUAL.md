# Manual de QA — cómo funciona la plataforma

Este documento no lista casos de prueba: explica **cómo está pensado el sistema** para que puedas diseñar los tuyos. Los casos están en [`QA_PLAN_PRUEBAS.md`](QA_PLAN_PRUEBAS.md).

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
