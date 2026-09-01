# Planes de trabajo cargados en bloque

Cronogramas que se dieron de alta con `manage.py cargar_asignaciones` en vez de
una fila cada vez desde la pantalla de solicitud.

Están versionados a propósito. La alternativa era pasarle el plan al comando por
la entrada estándar en el momento de ejecutarlo, y entonces lo que se cargó en
producción solo existiría en el historial de una terminal: no se podría revisar
antes, ni repetir después, ni comparar con lo que quedó en la base. Aquí el plan
se revisa en el PR como cualquier otro cambio, y volver a ejecutarlo es
inofensivo porque el comando es idempotente por (recurso, proyecto, rango).

## Dónde va el archivo

Aquí, en `backend/planes/`. **No** junto al comando, que vive en
`backend/apps/assignments/management/commands/`: el código va en `apps/`, los
datos en `planes/`.

Esta carpeta se ve como `planes/` desde dentro del contenedor, porque
`docker-compose.yml` monta `./backend:/app` y el `Dockerfile` copia `backend/`
entero a `/app`:

```
backend/planes/mi-plan.tsv      →      planes/mi-plan.tsv
   (en el repo)                        (lo que recibe el comando)
```

Por eso la misma ruta funciona en local y en producción sin cambiar nada, y por
eso el archivo tiene que estar commiteado antes de poder ejecutarlo en prod: si
no está en la imagen, el contenedor no lo ve.

La ruta es relativa a `/app`, que es donde corre `manage.py`. Una ruta absoluta
también vale, y con `-` lee de la entrada estándar.

## Formato

TSV sin cabecera, una fila por tarea, tal como se copia de un Excel:

```
recurso <TAB> actividad <TAB> fecha_inicio <TAB> fecha_fin <TAB> horas
```

El separador es un **tabulador**, no espacios. Pegado desde Excel sale bien
solo; escrito a mano en un editor que convierte tabuladores a espacios, no.
Para comprobarlo antes de ejecutar —tiene que imprimir un único `5`:

```bash
awk -F'\t' '{print NF}' backend/planes/mi-plan.tsv | sort -u
```

Si una línea no tiene 5 columnas, el comando dice cuál es y no escribe nada.

- **recurso**: el correo, o un nombre parcial que identifique a una sola
  persona. Si encaja con dos, el comando se detiene en vez de elegir.
- **fechas**: `dd/mm/aaaa`.
- **horas**: el **total** de la tarea en ese rango, no horas por día. El comando
  las reparte entre los días hábiles reales de esa persona.

## Cómo se ejecuta

En local:

```bash
docker compose exec web python manage.py cargar_asignaciones \
    planes/mi-plan.tsv --proyecto <CODIGO> --solicitante <usuario> --simular
```

En producción, a través del Container Apps Job (mismo canal que las
migraciones; nunca desde el arranque del contenedor web):

```bash
az containerapp job start -g <rg> -n <job> \
  --command "/bin/sh" "-c" \
  --args "python manage.py cargar_asignaciones planes/<archivo>.tsv \
          --proyecto <CODIGO> --solicitante <usuario> --simular"
```

Siempre `--simular` primero: imprime el reparto de horas fila por fila, marca
lo que ya existe y avisa de los días que superarían la jornada al aprobar.
`--confirmar` es lo que escribe.

Las asignaciones nacen **SOLICITADAS**. Aprobarlas sigue siendo un acto de una
persona, con su validación de capacidad y su entrada en el log de auditoría.

## Qué hay aquí

| Archivo | Proyecto | Cargado |
|---|---|---|
| `2026-09-anecoop-simulador-planes-de-venta.tsv` | `V-25188808/Q` — SIMULADOR PLANES DE VENTA (ANECOOP) | 2026-09-01 |
