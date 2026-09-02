# Módulos, capas y qué hay que reprobar cuando cambia algo

El plan de QA tiene **171 casos**. Repasarlos entero cada vez que se toca una
pantalla no es sostenible: es la diferencia entre poder desplegar un martes y no
poder.

Este documento dice qué bloques de QA hay que reprobar según lo que se toque. El
mapa solo vale si las dependencias entre módulos son las que aquí se afirman,
así que no se afirman: **se comprueban en un test**
(`apps/core/tests_arquitectura.py`). Si alguien introduce un acoplamiento que
rompa el mapa, falla el CI antes de que Erika se entere probando.

---

## Las capas

Cada app solo puede depender de las de arriba. Es lo que hace que un cambio en
`legalizacion` no pueda romper `assignments`.

| # | App | De qué responde |
|---|---|---|
| 1 | `accounts` | Quién es cada quien y qué puede hacer. Login local, SSO, roles (Admin / PM / Ingeniero / Visor). |
| 2 | `core` | El maestro: recursos, proyectos, tarifas, clusters, skills. |
| 3 | `calendar_engine` | Qué días son hábiles para cada persona. Feriados, novedades. |
| 4 | `assignments` | Quién está asignado a qué y cuándo. **El plan.** |
| 5 | `legalizacion` | Qué hizo cada quien con su jornada. **Lo declarado.** |
| 6 | `revision` | Triaje de la cola de aprobación. Se puede quitar de `INSTALLED_APPS`. |
| 7 | `dashboard` | Pantallas que componen todo lo anterior. |

`legalizacion` puede mirar el plan de `assignments` —para enseñar la tarea
planificada al lado de lo declarado— pero `assignments` **no** puede depender de
lo que la gente declaró después. Esa flecha va en un solo sentido y el test la
sostiene.

### Tres formas de depender, que no acoplan igual

- **Estructural** — `from apps.x import y` en el cuerpo del módulo. Se ejecuta al
  cargar la app y es lo que crea ciclos reales. Es lo que se vigila.
- **Diferido** — el mismo import dentro de una función. Acopla en ejecución pero
  no al cargar; es la salida legítima para una dependencia puntual hacia arriba.
  `calendar_engine` la usa así para solicitar una liberación.
- **De comando** — dentro de `management/commands/`. `limpiar_operacion` toca
  todas las tablas por definición; contarlo como acoplamiento del módulo daría un
  grafo falso.

Hoy **no hay ningún ciclo estructural**. Lo que parecía uno eran imports
diferidos y ese comando de mantenimiento.

---

## Qué reprobar según lo que se toque

Los bloques **propios** son los que ejercitan directamente ese módulo. Los
**arrastrados** son los de las capas que dependen de él.

| Si tocas… | Bloques propios | Arrastra | Casos |
|---|---|---|---|
| `accounts` | AUT, SSO, RBAC | **todo** — es la base de quién puede qué | 171 |
| `core` | MAE, RBAC | SOL, APR, CES, LIB, HOR, HAP, APD, DASH | ~120 |
| `calendar_engine` | CAL, NOV | SOL, APR, LIB, HOR | ~75 |
| `assignments` | SOL, APR, CES, LIB, AUD | HAP, APD, DASH (leen el plan) | ~70 |
| `legalizacion` | HOR, HAP, APD | DASH | ~64 |
| `revision` | HAP | — | 21 |
| `dashboard` | DASH | — | 5 |
| `templates/base.html` | AUT-09, AUT-10, RBAC-03 | — | 3 |
| `config/settings/` | INF, AUT | — | 17 |
| `terraform/` | INF | — | 6 |

Tocar la capa 1 obliga a probarlo todo. Es el precio de estar abajo, y la razón
para tocarla poco.

### Humo: siempre, cueste lo que cueste el cambio

Ocho casos que cubren lo que no puede romperse nunca. Si alguno falla, da igual
lo que dijera el mapa:

| Caso | Qué protege |
|---|---|
| AUT-01 | Se puede entrar |
| AUT-05 | Las rutas siguen protegidas sin sesión |
| RBAC-01 | **El Ingeniero no ve costos** — regla no negociable del proyecto |
| RBAC-06 | El Ingeniero solo se ve a sí mismo |
| HOR-01 | Se pueden registrar horas |
| HAP-01 | Se pueden aprobar |
| DASH-01 | El dashboard carga |
| INF-01 | `/healthz/` responde |

---

## Cómo se usa

Cada PR dice en su descripción qué bloques hay que reprobar. Sale de mirar qué
apps toca el diff y buscarlas en la tabla de arriba.

Para ver el grafo de dependencias tal como está hoy:

```bash
docker compose exec web python manage.py test apps.core.tests_arquitectura -v 2
```

Si añades una app nueva, el test falla hasta que la sitúes en `CAPAS` y la
añadas a las dos tablas de este documento. Es a propósito: una app sin capa deja
el mapa incompleto, y un mapa incompleto es peor que no tenerlo, porque se
confía en él igual.
