# Datos maestros cargados en bloque

Listas de RRHH o de SAP que se cargaron con un comando en vez de a mano por el
admin. Para los cronogramas de trabajo, ver [`../planes/`](../planes/README.md).

Están versionados a propósito, por lo mismo que los planes: lo que se aplicó en
producción se revisa en el PR como cualquier otro cambio, se puede repetir, y
tiene que estar dentro de la imagen para poder ejecutarlo desde el Container
Apps Job.

## Dónde va el archivo

Aquí, en `backend/datos/`. Esta carpeta se ve como `datos/` desde dentro del
contenedor (`docker-compose.yml` monta `./backend:/app` y el `Dockerfile` copia
`backend/` entero a `/app`):

```
backend/datos/personas_sap.tsv      →      datos/personas_sap.tsv
   (en el repo)                            (lo que recibe el comando)
```

El separador es un **tabulador**. Para comprobarlo antes de ejecutar —tiene que
imprimir un único número, el de columnas del formato:

```bash
awk -F'\t' '{print NF}' backend/datos/mi-archivo.tsv | sort -u
```

## `personas_sap.tsv` — N° de persona SAP

```
nombre <TAB> correo <TAB> nro_persona_sap
```

La cabecera es opcional. El **correo es la clave** (es único en `Recurso` y no
depende de cómo se escriba el nombre); el nombre viene igualmente y se comprueba
contra el del recurso, para que un archivo con las columnas descuadradas no le
ponga a cada persona el número de otra.

```bash
docker compose exec web python manage.py actualizar_sap \
    datos/personas_sap.tsv --simular
```

En producción, a través del Container Apps Job (mismo canal que las
migraciones):

```bash
az containerapp job start -g <rg> -n <job> --yaml <ejecucion>.yaml
```

donde el YAML reproduce el contenedor `migrate` con su bloque `env` completo y
cambia solo `args`. `job start --yaml` **reemplaza el contenedor entero**: lo
que no esté en el YAML no existe en la ejecución, y sin `env` el contenedor ni
arranca. Los secretos van por `secretRef`, nunca en claro.

### Qué hace y qué no

Solo escribe `nro_persona_sap`. No toca banda, grupos, `is_staff`, skills,
clusters ni tarifas — por eso no se usa `cargar_recursos`, que es un upsert
completo y dejaría todo eso en manos de los valores por defecto.

Un número que ya está y coincide se omite. Uno que ya está y es **distinto** se
reporta como conflicto y **detiene la carga sin escribir nada**: puede ser una
corrección legítima o el archivo equivocado, y no es el comando quien decide.
Para aplicarlos, repetir con `--sobrescribir`; se listan igual antes de escribir.
