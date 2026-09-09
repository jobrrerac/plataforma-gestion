# Seguimiento de la entrega

Marco de gestión y hoja de ruta del módulo `seguimiento`. Cubre las **fases 1 y
2** de las cinco que tiene el plan.

---

## El problema

Un recurso rindió por debajo de lo esperado durante un mes. Ni él ni el jefe de
proyecto levantaron la mano, y la señal llegó tarde para corregir.

La lectura fácil es que faltó seguimiento. La correcta es que **no había nada que
produjera una señal**: las tareas se asignaban de palabra y día a día, sin
alcance escrito ni criterio de aceptación; las dudas se resolvían según la
disponibilidad de quien las resolviera; y no quedaba ningún registro que
envejeciera y molestara cuando algo llevaba días parado.

En esas condiciones, un perfil junior con poca autogestión rinde mal de forma
previsible. Eso no es información sobre la persona: es información sobre el
entorno. Este módulo existe para poder separar las dos cosas antes de juzgar
ninguna.

---

## Principios

**Medir el entorno antes que a la persona.** Mientras la entrada sea inestable,
cualquier nota sobre un recurso mide el entorno y lo llama desempeño.

**Simetría o nada.** Todo lo que se registra sobre el recurso tiene su
equivalente sobre el proyecto. Un instrumento que solo mira hacia un lado
fabrica evidencia contra el lado que no tiene voz.

**Observación antes que nota.** Se registra qué pasó, cuándo y con qué
consecuencia. La calificación se deriva de eso y es opcional. Una observación
fechada se puede contrastar y rebatir; un «3 sobre 5» no se puede ni defender ni
discutir.

**El dato del proyecto sale del recurso.** Ningún indicador del lado cliente
depende de que el jefe de proyecto llene nada. Todos se derivan de lo que
registra el recurso más lo que la aplicación ya sabe. Es lo que permite arrancar
sin negociar nada con nadie.

**Levantar la mano se premia.** Un bloqueante registrado es una conducta
correcta, no una queja. En el momento en que reportar un bloqueo se vea mal,
nadie reporta ninguno y volvemos al punto de partida, que era el silencio.

**Sin sistemas paralelos.** Todo cuelga de lo que ya existe —asignaciones,
legalización, triaje—. Una herramienta aparte que haya que llenar dos veces se
abandona en tres semanas.

---

## Bloqueantes

Un objeto con dueño y con edad. Se pide poco a propósito: qué necesitas y quién
lo resuelve. Un formulario largo para reportar un bloqueo consigue que nadie
reporte bloqueos.

**Quién puede resolverlo son dos campos y no uno.** La mitad de las veces es un
jefe de proyecto que sí tiene cuenta —`Proyecto.pm` es una clave foránea a
`User`— y la otra mitad es alguien del equipo cliente que no la tiene. Se guarda
la FK cuando existe porque es lo único que permite agregar el tiempo de
desbloqueo por persona sin que «Álvaro», «alvaro» y «Alvaro O.» cuenten como
tres.

**`resuelto_en` es lo que hace todo el trabajo.** Sin esa fecha hay una lista de
quejas; con ella hay una métrica —la diferencia entre las dos fechas es el
tiempo de respuesta de quien tenía que desbloquear— y no hizo falta pedirle a
nadie del lado cliente que registrara nada.

**Cerrarlo no lo puede hacer cualquiera**: quien lo reportó, quien figura como
responsable, o un Admin. Si lo pudiera cerrar quien pasaba por ahí, el tiempo
entre las dos fechas dejaría de medir algo.

El plazo son **48 horas** de reloj, no dos días hábiles: un bloqueo que nace un
jueves por la tarde y sigue el lunes ya costó cuatro días de calendario.

---

## Feedback

Un solo modelo para las dos direcciones, para poder leerlas **enfrentadas** en la
misma línea de tiempo. Con dos modelos, cada pantalla que las muestre junta
tendría que mezclarlas a mano y tarde o temprano una se quedaría fuera.

| Dirección | Qué recoge |
|---|---|
| **Proyecto → persona** | situación, conducta observada, impacto, tipo, dimensión opcional |
| **Persona → proyecto** | claridad del objetivo (1-5), qué tuvo que intuir, horas hasta respuesta, cambios de alcance, qué habría ahorrado tiempo |

La segunda es la que hoy no existe en ningún sitio y la que más información nueva
aporta: es la única que puede decir que el objetivo llegó a medias o que nadie
contestó en tres días.

### La asimetría de visibilidad, que es deliberada

Lo que el proyecto escribe **sobre** una persona, esa persona lo ve. No hay
expediente secreto: un registro que se usa para evaluar a alguien y que esa
persona no puede leer no se puede rebatir, y por tanto no se puede considerar
justo.

Lo que una persona escribe **sobre** el proyecto, en cambio, **no lo ve el jefe
de proyecto individualmente**: lo ven ella y el Admin. La razón es práctica, no
política. Quien depende de otro para que le asignen tareas y le resuelvan dudas
no va a escribir «llevo tres días sin respuesta» si sabe que esa misma persona lo
va a leer con su nombre encima. Lo que sí llega al proyecto es el patrón
agregado, no la entrada suelta: se protege la observación individual y se publica
la tendencia.

### Editar

No es append-only, a diferencia de `LogAuditoria`: una errata en una observación
tiene que poder corregirse. Lo que sí queda es la huella —`created_at` y
`updated_at` dejan de coincidir— y la pantalla lo dice con una etiqueta. Que se
note es lo que evita reescribir una observación en silencio después de que
alguien la haya leído.

---

## Alertas

El seguimiento de 27 personas no es una ronda de 27 conversaciones —eso no escala
y ya se demostró que no escala—: es atender lo que el sistema levanta.

| Alerta | Umbral | Acción esperada |
|---|---|---|
| Bloqueante abierto | 48 h | Escalar por escrito, citando la fecha de alta |
| Recurso sin imputar a su proyecto | 2 días en los últimos 7 | Confirmar si tiene tarea; si no, reclamarla |
| Nadie ha observado a alguien asignado | 14 días | Pedir la observación al jefe de proyecto |

**Solo las ve quien puede actuar.** Enseñarle a un ingeniero que tres compañeros
están sin tarea no le sirve de nada y le cuenta cosas que no le corresponden.

La alerta de «sin tarea» no necesita ningún dato nuevo: sale de cruzar el plan
(`Asignacion`) con lo declarado (`RegistroHoras`), que es exactamente para lo que
se separaron los dos módulos. Cuenta los días en los que la persona **sí registró
horas** pero ninguna fue a su proyecto; un día sin registrar es otro problema y
ya lo cubre la lista de días pendientes.

**Esto es también la defensa del líder de equipo.** La pregunta «¿por qué no
hiciste seguimiento uno a uno con los 27?» tiene una mala respuesta, que es
intentarlo, y una buena: *el sistema levantó estas alertas, actué sobre todas, y
aquí están las fechas*. Un registro de excepciones atendidas es más defendible
que una agenda llena.

---

## Período de calibración

Los indicadores del recurso se capturan desde el primer día pero **no cuentan
para evaluar** hasta pasadas las primeras dos asignaciones de cada persona y las
primeras ocho semanas del marco. Esa regla se publica antes de empezar, no
después de ver los resultados: es lo que la hace defendible y lo que hace que la
gente sea honesta mientras tanto.

Sin ella, los primeros meses de ruido del entorno quedan escritos en el
expediente de gente que apenas empieza.

---

## Lo que falta

| Fase | Qué añade | Depende de |
|---|---|---|
| ~~1~~ | ~~Bloqueantes y alertas~~ | — |
| ~~2~~ | ~~Feedback en dos direcciones~~ | — |
| 3 | Tareas con estimación propia, avance y cambios de alcance | Fase 1 |
| 4 | Cierre de asignación con acta obligatoria de ambos lados | Fases 2 y 3 |
| 5 | Tablero por proyecto y por jefe de proyecto | Fases 1 a 4 |

**Sobre la estimación (fase 3):** estima el recurso, el jefe de proyecto confirma
o corrige. Así no hace falta enseñarle a nadie a planificar para empezar a medir,
y produce dos señales de una sola pregunta: si el proyecto nunca corrige, es un
dato sobre el proyecto; si la estimación del recurso mejora con los meses, es un
dato sobre el recurso.

**Sobre el cierre (fase 4):** una asignación no se cierra sin el feedback final de
ambos lados. Es la palanca — cerrar tiene valor administrativo para el proyecto,
así que es el único momento del ciclo en el que pedirle algo tiene una
contrapartida clara para él.

La fase 5 va al final a propósito: antes de tener tres meses de datos, un tablero
solo sirve para sacar conclusiones apresuradas con muestras de dos.

---

## Qué NO hace este módulo

Decirlo por adelantado es lo que evita que se le pidan cosas que no puede dar, y
que se le retire la confianza cuando no las dé.

- **No sustituye la conversación.** Reduce cuántas hacen falta y dice cuáles son
  urgentes. Las que quedan siguen siendo conversaciones.
- **No mide calidad técnica del entregable.** Eso se ve en revisión de código y
  en la validación con el cliente, no en un formulario.
- **No arregla que un proyecto no planifique.** Lo hace visible y fechado, que es
  otra cosa — y es todo lo que una herramienta puede hacer ahí.
- **No sirve para decidir sobre una persona con los datos de los primeros
  meses.** Para eso está el período de calibración, y saltárselo invalida el
  instrumento entero.
- **No es control horario.** Si se usa para eso una sola vez, deja de recibir
  información honesta y se convierte en un teatro.
