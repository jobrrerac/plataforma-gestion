# Carga masiva de datos (recursos y proyectos)

Dos comandos de gestión hacen **upsert** desde un CSV: si el registro ya existe se
**actualiza**, si no se **crea**. Reejecutar es seguro (idempotente).

> Los archivos se editan cómodamente en Excel. Al guardar, elegir **CSV UTF-8**.
> El separador (`,` `;` o tabulador) se detecta solo; forzarlo con `--delimiter ";"`.

---

## 1. Usuarios / Recursos — `cargar_recursos`

```bash
# Requisito: los grupos deben existir
docker compose exec web python manage.py setup_grupos

# Simular sin escribir (recomendado la primera vez)
docker compose exec web python manage.py cargar_recursos /app/../docs/plantillas/recursos.csv --dry-run

# Cargar de verdad (copie su CSV a una ruta accesible por el contenedor)
docker compose exec web python manage.py cargar_recursos recursos.csv
```

### Columnas

| Columna | Oblig. | Descripción |
|---|---|---|
| `rol` | Sí | `Admin`, `PM` o `Ingeniero` |
| `username` | Sí | Usuario de login (clave de upsert del User) |
| `nombre` | Sí | Nombre completo (se parte en nombre/apellido para el login) |
| `email` | Sí | Único |
| `activo` | No | `Si`/`No` (default `Si`) |
| `es_staff` | No | Acceso a `/admin/`. Default: Admin/PM = Sí, Ingeniero = No |
| `banda` | Ingeniero | `JR`, `SSR`, `SR`, `LEAD` |
| `nro_persona_sap` | No | N.º de persona en SAP (único). Clave preferida para casar el Recurso |
| `clusters` | No | Códigos separados por `;` (se crean si no existen). Ej: `CL-DATA;CL-BE` |
| `skills` | No | `skill:nivel` separados por `;`, nivel 1–5. Ej: `Python:5;SQL:3`. Sin nivel = 3 |
| `tarifa_valor_hora` | Ingeniero* | €/h (admite coma o punto) |
| `tarifa_fecha_desde` | Ingeniero* | Vigencia de la tarifa, `YYYY-MM-DD` |

\* La tarifa es opcional, pero si se informa una columna debe informarse la otra.

### Reglas de negocio aplicadas
- **Recurso**: se crea para Ingenieros (o cualquier fila con `banda`). Admin/PM sin
  `banda` solo generan el usuario de login. Se casa por `nro_persona_sap` y, en su
  defecto, por `email`.
- **Rol**: se fija el grupo indicado y se quitan los otros dos roles (una persona = un rol).
- **Tarifa (append-only)**: solo se **crea** una nueva vigencia. Si ya existe una tarifa
  con esa `fecha_desde`, no se modifica (regla no negociable). Para cambiar una tarifa,
  cargue una fila con una `fecha_desde` posterior.
- **Skills/clusters**: se agregan/actualizan los listados. Con `--reemplazar-skills` se
  dejan en el recurso **exactamente** los del CSV (borra los no listados).

### Contraseñas
Los usuarios **nuevos** reciben una contraseña aleatoria y se genera
`credenciales_generadas_<fecha>.csv`. **Contiene secretos**: entréguelas por un canal
seguro, exija cambio en el primer ingreso y borre el archivo. Alternativas:
`--password "Clave.2026"` (misma clave para todos, sin reporte) o cargar y luego resetear.

### Orden del nombre
La columna `nombre` se parte en nombre/apellidos para el usuario de login. Los
export de SAP vienen como **"Apellidos Nombres"** (ej: `Leon-Rangel Carmen`); en
ese caso usar `--orden-nombre apellido-nombre`. Por defecto asume "Nombre Apellido".

```bash
docker compose exec web python manage.py cargar_recursos /tmp/recursos.csv --orden-nombre apellido-nombre
```

### Opciones
`--dry-run` · `--reemplazar-skills` · `--orden-nombre {nombre-apellido,apellido-nombre}` ·
`--password` · `--reporte RUTA` · `--delimiter` · `--encoding`

---

## 2. Proyectos — `cargar_proyectos`

```bash
docker compose exec web python manage.py cargar_proyectos proyectos.csv --dry-run
docker compose exec web python manage.py cargar_proyectos proyectos.csv
```

### Columnas

| Columna | Oblig. | Descripción |
|---|---|---|
| `codigo` | Sí | Código interno del proyecto (clave de upsert, único) |
| `codigo_pep` | No | Elemento PEP en SAP (único cuando se informa) |
| `nombre` | Sí | Nombre del proyecto |
| `cliente` | Sí | Cliente |
| `fecha_inicio` | Sí | `YYYY-MM-DD` |
| `fecha_fin` | No | `YYYY-MM-DD` (vacío = sin fecha fin) |
| `estado` | No | `ACTIVO`, `EN_PAUSA`, `CERRADO` (default `ACTIVO`) |
| `pm_username` | Sí | Usuario del PM (debe existir; **cargue usuarios primero**) |

> Los **skills** son atributo del recurso, no del proyecto: no se cargan aquí.

---

## Orden recomendado
1. `setup_grupos`
2. `cargar_recursos recursos.csv` (crea PMs/usuarios)
3. `cargar_proyectos proyectos.csv` (referencia a los PM ya creados)
