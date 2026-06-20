# Plataforma de Gestión de Asignación de Recursos — Documento técnico completo

> Documento de referencia para entender el proyecto en profundidad: qué hace, cómo está construido, qué hace cada archivo, función y decisión de diseño.

---

## 1. ¿Qué es este proyecto?

Es una plataforma web interna para **gestionar la asignación de personas a proyectos**. Permite saber quién está disponible, cuándo, en qué proyectos están trabajando, y cuánta carga horaria tienen. Resuelve el problema clásico de los equipos de consultoría: saber en tiempo real quién tiene capacidad para ser asignado a un nuevo proyecto sin sobrecargar a nadie.

### Problema que resuelve

- Un PM necesita un Senior con habilidades en Java para el próximo mes. ¿Quién está libre?
- ¿Puedo asignar a Laura al proyecto X si ya está al 50% en el proyecto Y?
- ¿Cuántos días hábiles tiene el recurso disponible entre el 1 y el 30 de julio, descontando feriados colombianos y sus vacaciones?

### Lo que hace la plataforma

1. Mantiene un catálogo de recursos (personas) con sus skills y nivel de dominio
2. Mantiene proyectos con clientes y PM asignado
3. Calcula disponibilidad real: descuenta fines de semana, feriados colombianos (Ley Emiliani), días no laborables globales y períodos de vacaciones/permisos individuales
4. Valida que nunca se supere la jornada diaria por persona (8.5 h lun–jue, 8 h vie)
5. Maneja un flujo de aprobación: una asignación se solicita y alguien la aprueba
6. Muestra un dashboard heatmap para ver de un vistazo la ocupación del equipo
7. Permite buscar recursos disponibles por rango de fechas y skills requeridos

---

## 2. Stack tecnológico

| Componente | Tecnología | Versión |
|---|---|---|
| Lenguaje | Python | 3.12 |
| Framework web | Django | 5.2 |
| API REST | Django REST Framework (DRF) | 3.x |
| Base de datos | PostgreSQL | 16 |
| Contenedor app | Docker / Docker Compose | — |
| Base de datos | PostgreSQL local (fuera de Docker) | 16 |
| Frontend CSS | Bootstrap 5.3 | CDN |
| Iconos | Bootstrap Icons | CDN |
| Tipografía | Montserrat (Google Fonts) | — |
| Datepicker | Flatpickr | CDN |
| Selector múltiple | TomSelect | CDN |
| Calendario feriados | librería `holidays` (PyPI) | — |
| Variables de entorno | `django-environ` | — |

### ¿Por qué este stack?

- **Django** es un framework "batteries included": tiene ORM, admin, autenticación, formularios y migraciones incluidos. Permite construir rápido.
- **DRF** agrega una capa de API REST sobre Django con serialización, autenticación y throttling.
- **PostgreSQL** es robusto, soporta `select_for_update` (necesario para la aprobación concurrente) y tiene buen soporte en Azure.
- **Docker** permite que el contenedor de la app sea idéntico en desarrollo y producción. La base de datos está fuera de Docker para que los datos sobrevivan si el contenedor se recrea.
- La librería `holidays` calcula feriados colombianos automáticamente siguiendo la Ley Emiliani (feriados que se trasladan al lunes).

---

## 3. Arquitectura general

```
plataforma_gestion/
├── docker-compose.yml          ← Levanta el contenedor web
├── .env                        ← Variables de entorno (NO va al repo)
├── .env.example                ← Plantilla del .env
├── .gitignore
├── docs/
│   ├── SPEC.md                 ← Especificación funcional del producto
│   ├── SETUP.md                ← Guía de instalación paso a paso
│   └── PROYECTO.md             ← Este archivo
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py               ← CLI de Django
    ├── config/
    │   ├── settings/
    │   │   ├── base.py         ← Configuración base (compartida)
    │   │   └── local.py        ← Configuración local/dev
    │   ├── urls.py             ← Enrutador principal
    │   └── wsgi.py
    ├── apps/
    │   ├── core/               ← Modelos maestros (Recurso, Proyecto, Skill)
    │   ├── calendar_engine/    ← Motor de calendario y días hábiles
    │   ├── assignments/        ← Asignaciones, aprobación, auditoría
    │   ├── dashboard/          ← Vistas web de usuario
    │   └── accounts/           ← Autenticación (extiende Django auth)
    └── templates/
        ├── base.html           ← Layout con navbar Inetum
        ├── dashboard/          ← Plantillas de las vistas web
        └── admin/              ← Personalización del admin de Django
```

### Filosofía: monolito modular

El proyecto es un **monolito** (una sola aplicación Django), pero organizado en **módulos (apps)** con responsabilidades claras y separadas. Esto permite:
- Entender cada parte por separado
- Escalar o extraer módulos en el futuro si fuera necesario
- No tener la complejidad de microservicios para un prototipo

---

## 4. Cómo fluye una petición

```
Navegador → Docker (puerto 8000) → Django → URL router → View → Service → Model → BD PostgreSQL
                                                                     ↓
                                                               Template HTML
                                                                     ↓
                                                             Respuesta al navegador
```

Para las llamadas a la API (desde JavaScript del dashboard):
```
JavaScript fetch() → DRF APIView → Serializer → QuerySet → BD → JSON → JavaScript
```

---

## 5. Configuración (`backend/config/`)

### `settings/base.py`

Configuración compartida entre todos los entornos. Los valores sensibles vienen de variables de entorno, leídas con `django-environ`.

```python
env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env", overwrite=False)
```

Esto lee el archivo `.env` de la raíz del proyecto. Si la variable ya existe en el entorno del sistema, `overwrite=False` la respeta (útil en producción donde las vars vienen del sistema operativo).

**Base de datos:**
```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        ...
    }
}
```
El `HOST` en desarrollo es `host.docker.internal` — una dirección especial que Docker Desktop resuelve automáticamente a la IP de la máquina anfitriona (Windows). Así el contenedor Django puede conectarse al PostgreSQL instalado localmente.

**Locale:**
```python
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_L10N = True
```
Importante: `USE_L10N=True` hace que los números en plantillas usen el separador local. En español colombiano, `54.4` se renderiza como `54,4`. Esto causa problemas si se usa ese número en CSS (`width:54,4%` es CSS inválido). Por eso en las plantillas se usa `{{ pct|floatformat:0 }}` para obtener un entero sin separadores.

### `urls.py`

El enrutador principal conecta cada URL con su vista:

```python
urlpatterns = [
    path("admin/", admin.site.urls),                    # Admin de Django
    path("api/", include("apps.core.urls")),             # API de Recursos y Proyectos
    path("api/", include("apps.assignments.urls")),      # API de Asignaciones
    path("api/dashboard/ocupacion/", OcupacionAPIView),  # API del heatmap
    path("solicitud/", SolicitudView),                   # Buscador de disponibilidad
    path("solicitud/crear/", SolicitudCrearView),        # Crear solicitud
    path("recurso/<int:pk>/", RecursoDetalleView),       # Detalle de recurso
    path("dashboard/", OcupacionDashboardView),          # Dashboard heatmap
    path("", OcupacionDashboardView),                    # Raíz → dashboard
]
```

---

## 6. App `core` — Modelos maestros

**Ubicación:** `backend/apps/core/`

Esta app contiene las entidades principales del negocio: las "fichas maestras" que todo lo demás referencia.

### `models.py`

#### `SoftDeleteModel` (clase abstracta)

Base que heredan todos los modelos que necesitan soft-delete (nunca se borran físicamente).

```python
class SoftDeleteModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()       # Solo registros activos (deleted_at=NULL)
    all_objects = models.Manager()      # Todos, incluso borrados
```

El método `delete()` no borra de la BD; en cambio, guarda la fecha actual en `deleted_at`. El manager `SoftDeleteManager` filtra automáticamente los registros con `deleted_at` nulo, así el resto del código los ignora sin saberlo.

`all_objects` se usa en casos especiales: por ejemplo, en la aprobación con `select_for_update` se necesita bloquear el registro aunque esté "borrado" lógicamente.

#### `Skill`

Habilidad técnica. En producción se sincronizaría desde un sistema externo de Skills vía adaptador.

```python
class Skill(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.CharField(max_length=300, blank=True)
```

- `unique=True` en `nombre`: no puede haber dos skills con el mismo nombre.

#### `Recurso` (hereda `SoftDeleteModel`)

Una persona del equipo que puede ser asignada a proyectos.

```python
class Recurso(SoftDeleteModel):
    BANDA_CHOICES = [("JR", "Junior"), ("SSR", "Semi-Senior"), ("SR", "Senior"), ("LEAD", "Tech Lead")]
    nombre = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    banda = models.CharField(max_length=10, choices=BANDA_CHOICES)
    activo = models.BooleanField(default=True)
    skills = models.ManyToManyField(Skill, through="RecursoSkill", blank=True)
    usuario = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
```

- `banda`: nivel de seniority. Influye en la tarifa.
- `skills`: relación muchos-a-muchos CON tabla intermedia (`through="RecursoSkill"`). Esto permite guardar datos adicionales en la relación (el nivel de dominio `suficiencia`).
- `usuario`: vincula al Recurso con el usuario de Django para autenticación. `OneToOneField` significa que un usuario tiene exactamente un recurso asociado (o ninguno).

#### `RecursoSkill` (tabla intermedia)

Relaciona un Recurso con un Skill y guarda el nivel de dominio.

```python
class RecursoSkill(models.Model):
    SUFICIENCIA_CHOICES = [
        (1, "★ Básico"), (2, "★★ Elemental"), (3, "★★★ Intermedio"),
        (4, "★★★★ Avanzado"), (5, "★★★★★ Experto - Certificado"),
    ]
    recurso = models.ForeignKey(Recurso, on_delete=models.CASCADE, related_name="recurso_skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="recurso_skills")
    suficiencia = models.PositiveSmallIntegerField(choices=SUFICIENCIA_CHOICES, default=3)

    class Meta:
        unique_together = [("recurso", "skill")]  # Un recurso no puede tener el mismo skill dos veces
        ordering = ["-suficiencia", "skill__nombre"]  # Más experto primero

    @property
    def estrellas(self):
        return "★" * self.suficiencia + "☆" * (5 - self.suficiencia)
```

- `related_name="recurso_skills"`: permite hacer `recurso.recurso_skills.all()` para obtener todos los skills de un recurso con su suficiencia.
- `@property estrellas`: genera la cadena visual `★★★☆☆` sin consultas adicionales.

#### `Proyecto` (hereda `SoftDeleteModel`)

Un proyecto al que se pueden asignar recursos.

```python
class Proyecto(SoftDeleteModel):
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=200)
    cliente = models.CharField(max_length=200)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    estado = models.CharField(choices=[("ACTIVO","Activo"),("EN_PAUSA","En Pausa"),("CERRADO","Cerrado")])
    pm = models.ForeignKey(User, on_delete=models.PROTECT, related_name="proyectos_pm")
```

- `on_delete=models.PROTECT` en `pm`: no se puede borrar un usuario si tiene proyectos asignados como PM.

### `serializers.py`

Convierten objetos Python (modelos Django) a JSON y viceversa para la API.

- `RecursoSerializer`: expone id, nombre, email, banda, banda_display (legible), activo, created_at.
- `ProyectoSerializer`: expone todos los campos más `pm_username` (el nombre del PM, no solo su ID).

`banda_display = serializers.CharField(source="get_banda_display", read_only=True)` — `get_banda_display()` es un método que Django genera automáticamente en los modelos con `choices` para obtener el label ("Junior" en vez de "JR").

### `views.py`

ViewSets de DRF: cada uno expone automáticamente los endpoints GET, POST, PUT, PATCH, DELETE.

```python
class RecursoViewSet(viewsets.ModelViewSet):
    def perform_destroy(self, instance):
        instance.delete()  # Llama al soft-delete, no a DELETE SQL
```

El override de `perform_destroy` es clave: sin él, DRF ejecutaría `DELETE FROM core_recurso`, borrando el registro físicamente. Con el override, llama al método `delete()` del modelo que en realidad hace `UPDATE SET deleted_at=NOW()`.

### `admin.py`

Personalización del panel de administración de Django para Core.

**`SkillAdmin`**: lista skills con descripción corta (truncada a 70 chars) y contador de recursos activos que tienen ese skill.

**`RecursoSkillInline`**: permite gestionar los skills de un recurso directamente desde la ficha del recurso. Muestra las estrellas de suficiencia con colores según nivel:
- Rojo (básico) → naranja → amarillo → verde menta → verde oscuro (experto)

**`RecursoAdmin.skills_display`**: en el listado de recursos, muestra un botón tipo chip ("2 skills") que al hacer clic despliega un popup con el detalle de cada skill y sus estrellas. Implementado con CSS puro (sin Bootstrap) porque el admin de Django no carga Bootstrap.

---

## 7. App `calendar_engine` — Motor de calendario

**Ubicación:** `backend/apps/calendar_engine/`

La lógica más delicada del proyecto. Determina qué días son hábiles para un recurso dado.

### `models.py`

#### `DiaNoLaborable`

Día no laborable **global**: aplica a todos los recursos (ej. cierre de empresa, puente adicional).

```python
class DiaNoLaborable(models.Model):
    fecha = models.DateField(unique=True)
    descripcion = models.CharField(max_length=200)
    creado_por = models.ForeignKey(User, on_delete=models.PROTECT)
```

#### `Indisponibilidad` (hereda `SoftDeleteModel`)

Período de no disponibilidad de **un recurso específico** (vacaciones, permisos).

```python
class Indisponibilidad(SoftDeleteModel):
    recurso = models.ForeignKey(Recurso, on_delete=models.CASCADE)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    tipo = models.CharField(choices=[("VACACION","Vacación"),("PERMISO","Permiso")])
    origen = models.CharField(choices=[("MANUAL","Manual"),("SAP","SAP")])
    external_id = models.CharField(null=True, blank=True)  # ID en el sistema SAP si vino de allá
```

- `origen` y `external_id`: preparados para cuando se integre con SAP. Los registros que vienen de SAP tienen `origen="SAP"` y el ID del registro en SAP.

### `services.py`

El cerebro del calendario. Todas las funciones de fecha pasan por aquí.

#### `_feriados_colombia(year)` — función privada con caché

```python
@lru_cache(maxsize=10)
def _feriados_colombia(year: int) -> frozenset:
    return frozenset(holidays.Colombia(years=year).keys())
```

- `@lru_cache`: memoriza el resultado. La primera vez que se llama con `year=2026`, consulta la librería `holidays` y guarda el resultado. Las siguientes llamadas con el mismo año devuelven el resultado cacheado sin recalcular. `maxsize=10` guarda hasta 10 años distintos.
- `frozenset`: conjunto inmutable de fechas. Más eficiente que una lista para las búsquedas `if fecha in conjunto`.
- La librería `holidays` conoce la Ley Emiliani: feriados como el Día de la Raza se trasladan al lunes siguiente si no caen en lunes.

#### `es_habil(fecha, recurso=None)` — función principal

```python
def es_habil(fecha: date, recurso=None) -> bool:
```

Devuelve `True` solo si la fecha cumple TODAS las condiciones:
1. No es sábado ni domingo (`fecha.weekday() >= 5` → False)
2. No es feriado colombiano
3. No es día no laborable global
4. No está dentro de una indisponibilidad del recurso (si se pasa `recurso`)

El orden importa: primero los chequeos más baratos (fines de semana, caché de feriados) y al final los que requieren queries a BD.

#### `calcular_fecha_fin(fecha_inicio, dias_necesarios, recurso=None)`

Dado un punto de inicio y un número de días hábiles necesarios, calcula cuándo termina la asignación. Avanza día a día y cuenta solo los hábiles.

```python
def calcular_fecha_fin(fecha_inicio, dias_necesarios, recurso=None):
    fecha = fecha_inicio
    habiles = 0
    while habiles < dias_necesarios:
        if es_habil(fecha, recurso):
            habiles += 1
            if habiles == dias_necesarios:
                break
        fecha += timedelta(days=1)
    return fecha
```

Ejemplo: si el recurso necesita 10 días hábiles desde el lunes 28 de julio (con feriado el 7 de agosto), la fecha fin será el 11 de agosto, no el 8.

#### `contar_dias_habiles(fecha_inicio, fecha_fin, recurso=None)`

Cuenta cuántos días hábiles hay entre dos fechas (ambas inclusive). Usado para calcular horas totales en el modo RANGO.

#### `feriados_en_rango(fecha_inicio, fecha_fin)`

Devuelve una lista de feriados colombianos en el rango, con fecha y nombre. Usado por la API para que el frontend pueda mostrarlos.

---

## 8. App `assignments` — Asignaciones y aprobación

**Ubicación:** `backend/apps/assignments/`

El corazón del flujo de negocio: crear asignaciones, aprobarlas, validar capacidad.

### `models.py`

#### `Asignacion` (hereda `SoftDeleteModel`)

El modelo más complejo. Representa la asignación de un recurso a un proyecto en un período dado.

```python
class Asignacion(SoftDeleteModel):
    MODO_CHOICES = [("HORAS","Por horas totales"), ("DIAS","Por días hábiles"), ("RANGO","Por rango de fechas")]
    ESTADO_CHOICES = [("SOLICITADA","Solicitada"), ("APROBADA","Aprobada"), ("RECHAZADA","Rechazada"),
                      ("REVOCADA","Revocada"), ("INVALIDADA","Invalidada")]

    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT)
    modo_asignacion = models.CharField(choices=MODO_CHOICES, default="HORAS")
    horas_totales = models.PositiveIntegerField(null=True, blank=True)
    dias_habiles = models.PositiveIntegerField(null=True, blank=True)
    intensidad_diaria = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    jornada_completa = models.BooleanField(default=False)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    tarifa_aplicada = models.DecimalField(null=True, blank=True)
    costo_estimado = models.DecimalField(null=True, blank=True)
    estado = models.CharField(choices=ESTADO_CHOICES, default="SOLICITADA")
    solicitada_por = models.ForeignKey(User, on_delete=models.PROTECT)
```

**Tres modos de asignación:**
- `HORAS`: se indica total de horas e intensidad diaria → el sistema calcula `fecha_fin`
- `DIAS`: se indica cantidad de días hábiles e intensidad → sistema calcula `fecha_fin`
- `RANGO`: se indica `fecha_inicio` y `fecha_fin` → sistema calcula días y horas

**Campos financieros (snapshot):** `tarifa_aplicada` y `costo_estimado` guardan la tarifa vigente en el momento de la aprobación. Así aunque la tarifa del recurso cambie en el futuro, el historial de costos queda intacto.

**`jornada_completa`:** si es `True`, el recurso trabaja su máxima jornada cada día hábil del rango (8.5 h lun–jue, 8 h vie). La intensidad varía por día.

#### `LogAuditoria` — append-only

```python
class LogAuditoria(models.Model):
    asignacion = models.ForeignKey(Asignacion, on_delete=models.PROTECT)
    accion = models.CharField(choices=[("CREAR","Crear"),("APROBAR","Aprobar"),("RECHAZAR","Rechazar"),
                                       ("REVOCAR","Revocar"),("INVALIDAR","Invalidar")])
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)
    detalle = models.JSONField(default=dict)
```

Registra cada cambio de estado. Es **append-only**: el admin tiene bloqueados `has_add_permission`, `has_change_permission` y `has_delete_permission` para que nadie pueda editar ni borrar logs.

`detalle` es un campo JSON flexible: guarda información contextual diferente según la acción (ej. en APROBAR guarda la `fecha_fin` calculada; en RECHAZAR guarda el `motivo`).

### `services.py`

La lógica de negocio de asignaciones. Nunca se llama directamente desde la BD; siempre a través de estas funciones.

#### `capacidad_maxima_dia(fecha)`

```python
def capacidad_maxima_dia(fecha: date) -> float:
    if fecha.weekday() == 4:  # viernes
        return 8.0
    return 8.5  # lunes a jueves
```

Jornada real de Inetum: 8.5 horas de lunes a jueves, 8 horas el viernes. Esta función centraliza esa regla para que si cambia, se cambie en un solo lugar.

#### `carga_en_fecha(recurso, fecha, excluir_id=None)`

Suma todas las horas que tiene asignadas el recurso en una fecha específica, considerando solo asignaciones APROBADAS. `excluir_id` se usa para no contar la propia asignación que se está evaluando.

#### `puede_asignar(asignacion)`

Verifica que en NINGÚN día hábil del rango, la carga total del recurso supere la jornada máxima del día. Devuelve `(True, None)` si cabe, o `(False, fecha_conflicto)` si hay problema.

```python
def puede_asignar(asignacion) -> tuple[bool, object]:
    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if es_habil(fecha, asignacion.recurso):
            carga = carga_en_fecha(asignacion.recurso, fecha, excluir_id=asignacion.pk)
            if carga + _carga_propia(asignacion, fecha) > capacidad_maxima_dia(fecha):
                return False, fecha
        fecha += timedelta(days=1)
    return True, None
```

#### `aprobar_asignacion(asignacion, actor)` — función crítica

```python
def aprobar_asignacion(asignacion, actor):
    with transaction.atomic():
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            raise ValueError(f"Sobreasignación...")
        asignacion.estado = "APROBADA"
        asignacion.save(update_fields=["estado", "updated_at"])
        LogAuditoria.objects.create(...)
```

**Por qué `select_for_update`:** Si dos usuarios aprueban simultáneamente dos asignaciones del mismo recurso, podría darse una condición de carrera (race condition): ambos leen que hay capacidad, ambos aprueban, y el recurso queda sobreasignado. `select_for_update()` bloquea el registro del recurso en la base de datos hasta que termina la transacción. El segundo usuario espera en la fila hasta que el primero termina. Al ejecutar `puede_asignar`, ya contará la primera asignación y detectará el conflicto.

`update_fields=["estado", "updated_at"]`: solo actualiza esos dos campos, no todo el registro. Más eficiente y evita escribir datos obsoletos.

#### `analizar_conflictos(asignacion)`

Antes de intentar aprobar, detecta los días con conflicto y calcula una **nueva fecha fin** que salta esos días. Permite ofrecer al usuario la opción de "aprobar extendiendo la fecha fin".

Retorna `(conflict_dates, nueva_fecha_fin, nuevas_horas)`.

#### `disponibilidad_recursos(fecha_inicio, fecha_fin, skills=None)`

La función principal del buscador de solicitudes. Para cada recurso activo (opcionalmente filtrado por skills), calcula:
- Horas totales de capacidad en el rango
- Horas ya ocupadas por asignaciones APROBADAS
- Horas libres
- % libre y % ocupado
- Días sin ningún cupo libre

Ordena de más a menos disponible (los más libres primero).

#### `crear_solicitud(recurso, proyecto, ...)`

Crea una `Asignacion` en estado SOLICITADA y registra el evento en `LogAuditoria`. Es el único punto de entrada para crear solicitudes desde el flujo web (no desde el admin).

### `admin.py`

El admin de asignaciones es el más complejo. Permite a los administradores gestionar el ciclo de vida completo de las asignaciones.

**`AsignacionAdminForm`**: formulario personalizado que adapta los campos según el `modo_asignacion`:
- Modo HORAS: muestra `horas_totales` + `intensidad_diaria`
- Modo DIAS: muestra `dias_habiles` + `intensidad_diaria`
- Modo RANGO: muestra un campo extra `fecha_fin_rango`

**`AsignacionAdmin.save_model`**: cuando se guarda desde el admin, calcula automáticamente los campos derivados según el modo. Por ejemplo, en modo HORAS calcula `dias_habiles` y `fecha_fin` a partir de `horas_totales` e `intensidad_diaria`.

**Flujo de aprobación desde el admin:**

El admin agrega URLs personalizadas para las acciones de aprobación:

```
/admin/assignments/asignacion/aprobar/<pk>/           → view_aprobar
/admin/assignments/asignacion/aprobar/<pk>/confirmar/ → view_aprobar_confirmar
/admin/assignments/asignacion/rechazar/<pk>/          → view_rechazar
/admin/assignments/asignacion/revocar/<pk>/           → view_revocar
```

El flujo es:
1. Admin hace clic en "✓ Aprobar" en la lista
2. `view_aprobar`: analiza si hay conflictos
   - Sin conflictos → aprueba directo
   - Con conflictos → redirige a `view_aprobar_confirmar`
3. `view_aprobar_confirmar`: muestra los días conflictivos y pregunta si aprobar recomputando fecha fin
   - Si el admin confirma → `aprobar_recomputando`

---

## 9. App `dashboard` — Vistas web

**Ubicación:** `backend/apps/dashboard/`

Las vistas que los usuarios finales (PM, Admin) usan directamente en el navegador. No son API: devuelven HTML renderizado.

### `views.py`

#### `OcupacionDashboardView`

Vista simple que solo sirve la plantilla HTML del dashboard. No recibe datos desde el servidor: el JavaScript de la página los pide a la API.

```python
class OcupacionDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/ocupacion.html"
    login_url = "/admin/login/"
```

`LoginRequiredMixin`: si el usuario no está autenticado, lo redirige al login. Es un mixin de Django que se agrega en la herencia de clase (no como decorador).

#### `OcupacionAPIView`

La API que alimenta el heatmap. Recibe `fecha_inicio` y `fecha_fin` por query params y devuelve JSON con el detalle de ocupación por recurso y por día.

Para cada recurso, genera `detalle_por_dia`: una entrada por cada día del rango con el porcentaje de ocupación, las horas, y los códigos de proyectos. El JavaScript del frontend convierte este JSON en la tabla del heatmap con colores.

Optimización clave:
```python
asignaciones = list(
    Asignacion.objects.filter(
        estado="APROBADA",
        fecha_inicio__lte=fecha_fin,
        fecha_fin__gte=fecha_inicio,
    ).select_related("recurso", "proyecto")
)
```
Carga TODAS las asignaciones del período en una sola query, luego filtra en Python por recurso. Evita hacer N queries (una por recurso).

#### `SolicitudView`

Buscador de disponibilidad. Acepta `fecha_inicio`, `fecha_fin` y opcionalmente una lista de `skills`. Llama a `disponibilidad_recursos()` y devuelve la lista ordenada para que el usuario elija a quién asignar.

#### `SolicitudCrearView`

Formulario para crear la solicitud. Recibe el recurso y las fechas por GET (desde el buscador), muestra los datos del recurso y el detalle día a día de su disponibilidad. Al hacer POST, llama a `crear_solicitud()`.

Si hay conflictos con asignaciones existentes, los muestra y pregunta confirmación antes de crear la solicitud de todas formas (el PM puede decidir si quiere pedirla igualmente; la aprobación final resolverá si cabe o no).

#### `RecursoDetalleView`

Página de detalle de un recurso. Accesible haciendo clic en el nombre en el heatmap. Muestra:
- Skills del recurso con estrellas de suficiencia
- Asignaciones en curso (hoy cae entre fecha_inicio y fecha_fin)
- Próximas asignaciones (fecha_inicio > hoy)

Solo muestra asignaciones APROBADAS o SOLICITADAS que no hayan terminado.

---

## 10. Templates HTML

### `base.html` — Layout principal

El esqueleto que todas las páginas extienden. Define:
- Variables CSS de la marca Inetum (`--inet-pink: #e0178a`, `--inet-yellow: #d5e500`, etc.)
- Navbar con logo Inetum, links a Dashboard, Solicitudes, Asignaciones
- Bloque `{% block content %}` donde cada página pone su contenido
- Carga Bootstrap 5.3, Bootstrap Icons y Montserrat (Google Fonts)
- Bloque `{% block scripts %}` al final para el JavaScript de cada página

### `dashboard/ocupacion.html` — Dashboard heatmap

La página principal. Usa un patrón "shell + API":
1. La página HTML llega vacía (solo el esqueleto con inputs de fecha)
2. Al cargar, JavaScript hace `fetch()` a `/api/dashboard/ocupacion/`
3. Con la respuesta JSON, renderiza dinámicamente el heatmap usando `innerHTML`

El heatmap se construye como una tabla HTML donde:
- Cada fila es un recurso
- Cada columna es un día del período
- El color de cada celda depende del porcentaje de ocupación:
  - Blanco: bench (0%)
  - Verde claro: < 25%
  - Amarillo: 25–50%
  - Naranja: 50–75%
  - Rojo: 75–100%
  - Gris: no hábil

Los nombres de recursos son links clickables (`/recurso/<id>/`) con hover en rosa Inetum.

### `dashboard/solicitud.html` — Buscador de disponibilidad

Formulario con:
- Selectores de fecha de inicio y fin (Flatpickr para un datepicker elegante)
- Selector múltiple de skills con TomSelect
- Botones de rango rápido (1 semana, 2 semanas, etc.)
- Tabla de resultados con barras de disponibilidad y popup de skills por hover

### `dashboard/solicitud_crear.html` — Crear solicitud

Formulario de dos partes:
1. Cabecera: datos del recurso elegido, sus skills, su disponibilidad en el período
2. Formulario: elegir proyecto, intensidad diaria o jornada completa

Si hay conflictos, muestra un aviso con los días problemáticos antes de confirmar.

### `dashboard/recurso_detalle.html` — Detalle de recurso

Página de perfil del recurso. Diseño en dos secciones: "En curso" y "Próximas". Cada fila de asignación muestra el proyecto, cliente, período, días hábiles y una barra de progreso que indica el % de ocupación diaria. El color de la barra varía según la carga (verde → amarillo → naranja → rojo).

### `admin/base_site.html` — Tema del admin

Extiende el template base del admin de Django para aplicar la identidad visual de Inetum:
- Sidebar con colores de la marca
- Logo Inetum en el header
- Link activo resaltado en el menú lateral
- CSS del popup de skills (`inet-skill-*`)
- Función JavaScript `inetSkillClick` en el `<head>` (disponible globalmente antes de cualquier interacción)

---

## 11. Docker y despliegue

### `docker-compose.yml`

```yaml
services:
  web:
    build: ./backend
    ports: ["8000:8000"]
    env_file: [.env]
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

- `extra_hosts`: agrega `host.docker.internal` en el `/etc/hosts` del contenedor, apuntando al gateway del host. Necesario en Linux; en Mac/Windows Docker Desktop lo agrega automáticamente. Permite que Django dentro del contenedor llegue a PostgreSQL corriendo en el Windows anfitrión.
- Ya no hay servicio `db`: PostgreSQL corre directamente en Windows (instalado localmente en puerto 5434).

### Por qué la BD está fuera de Docker

Si Docker corre en una VM y la VM se destruye, se pierden los datos del volumen. Al tener PostgreSQL instalado en el sistema operativo anfitrión:
- Los datos persisten independientemente del ciclo de vida de los contenedores
- Se puede hacer backup con `pg_dump` directamente
- Cuando se migre a Azure PostgreSQL, solo cambia `POSTGRES_HOST` en el `.env`

### `Dockerfile` (en `backend/`)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
```

Imagen mínima de Python. Las dependencias se instalan primero (capa cacheada por Docker) y el código se copia después.

---

## 12. Reglas de negocio no negociables

Estas reglas están en `CLAUDE.md` y se respetan en todo el código:

| Regla | Dónde se implementa |
|---|---|
| Soft-delete: nunca borrado físico | `SoftDeleteModel` en `core/models.py` |
| RBAC: Ingeniero nunca ve costos | `has_change_permission` en admin, serializers sin campos de costo para ese rol |
| Capacidad: máx 8.5h lun–jue, 8h vie por persona | `puede_asignar()` en `assignments/services.py` |
| Solo asignaciones APROBADAS cuentan para capacidad | `carga_en_fecha()` filtra `estado="APROBADA"` |
| Aprobación: primero en aprobar gana | `select_for_update` en `aprobar_asignacion()` |
| Feriados Colombia con `holidays` (Ley Emiliani) | `_feriados_colombia()` en `calendar_engine/services.py` |
| `LogAuditoria` es append-only | `has_add/change/delete_permission = False` en `LogAuditoriaAdmin` |
| Sin credenciales en el repo | `.env` en `.gitignore`; `environ.Env` para leer vars |

---

## 13. Flujo completo: de la solicitud a la aprobación

```
1. PM abre /solicitud/
   ↓ Elige fechas y skills requeridos
   ↓ El sistema llama a disponibilidad_recursos()
   ↓ Muestra lista de recursos disponibles ordenados de más a menos libre

2. PM elige un recurso → /solicitud/crear/?recurso=3&fecha_inicio=...
   ↓ Ve el detalle de disponibilidad día a día del recurso
   ↓ Elige proyecto e intensidad diaria
   ↓ POST → crear_solicitud() → Asignacion(estado="SOLICITADA")

3. Admin abre /admin/assignments/asignacion/
   ↓ Ve la asignación SOLICITADA (badge morado)
   ↓ Hace clic en "✓ Aprobar"
   ↓ view_aprobar → analizar_conflictos()
      Si sin conflictos → aprobar_asignacion() → estado="APROBADA"
      Si con conflictos → muestra pantalla de confirmación
         Admin confirma → aprobar_recomputando() → fecha_fin extendida + estado="APROBADA"

4. En el dashboard aparece la celda coloreada en el heatmap
   ↓ Al hacer clic en el nombre del recurso → /recurso/3/
   ↓ Se ven las asignaciones en curso y próximas
```

---

## 14. API REST — Endpoints disponibles

| Método | URL | Descripción |
|---|---|---|
| GET | `/api/recursos/` | Lista recursos (filtra por `?activo=true&banda=SR`) |
| POST | `/api/recursos/` | Crear recurso |
| GET | `/api/recursos/<id>/` | Detalle de un recurso |
| PUT/PATCH | `/api/recursos/<id>/` | Editar recurso |
| DELETE | `/api/recursos/<id>/` | Soft-delete de recurso |
| GET | `/api/proyectos/` | Lista proyectos (filtra por `?estado=ACTIVO`) |
| GET | `/api/asignaciones/` | Lista asignaciones (filtra por `?recurso=3&estado=APROBADA`) |
| POST | `/api/asignaciones/` | Crear asignación (calcula fecha_fin automáticamente) |
| POST | `/api/asignaciones/<id>/aprobar/` | Aprobar asignación |
| POST | `/api/asignaciones/<id>/rechazar/` | Rechazar asignación |
| POST | `/api/asignaciones/<id>/revocar/` | Revocar asignación aprobada |
| GET | `/api/asignaciones/<id>/log/` | Ver log de auditoría de una asignación |
| GET | `/api/dashboard/ocupacion/` | Heatmap data (`?fecha_inicio=2026-06-01&fecha_fin=2026-06-30`) |

Todas las APIs requieren autenticación. En desarrollo se usa autenticación de sesión (cookie de Django tras hacer login en `/admin/`).

---

## 15. Migraciones de base de datos

Las migraciones son el historial de cambios en el esquema de la BD. Están en `apps/<app>/migrations/`.

| Migración | Qué hace |
|---|---|
| `core/0001_initial` | Crea tablas Recurso, Proyecto, Skill con M2M implícita |
| `core/0002_skill_recurso_skills` | Agrega M2M de Recurso→Skill (tabla automática) |
| `core/0003_sql_skill_to_all_recursos` | Data migration: asigna skills a recursos existentes |
| `core/0004_skill_descripcion_recursosskill` | Agrega `descripcion` a Skill; crea `RecursoSkill` como through model; migra datos de la tabla M2M anterior; recrea la relación con through |
| `assignments/0001_initial` | Crea tablas Asignacion y LogAuditoria |
| `calendar_engine/0001_initial` | Crea DiaNoLaborable e Indisponibilidad |

**Por qué la migración 0004 es compleja:** Django no permite cambiar una M2M de tabla implícita a una with `through` model con `AlterField`. La solución fue: 1) crear la nueva tabla `RecursoSkill`, 2) copiar los datos de la tabla vieja con un `RunPython`, 3) borrar el campo viejo, 4) recrearlo apuntando al through model.

---

## 16. Glosario del negocio

| Término | Significado |
|---|---|
| **Recurso** | Persona del equipo asignable a proyectos |
| **Banda** | Nivel de seniority: JR, SSR, SR, LEAD |
| **Skill** | Habilidad técnica (ej. "Java", "AWS", "Scrum") |
| **Suficiencia** | Nivel de dominio del skill: 1 (básico) a 5 (experto-certificado) |
| **Bench** | Recurso sin asignaciones activas hoy |
| **Jornada completa** | El recurso trabaja su máximo en cada día hábil del rango |
| **Intensidad diaria** | Horas por día que trabaja el recurso en ese proyecto |
| **Capacidad cruzada** | Validación que suma cargas de TODAS las asignaciones del recurso |
| **Feriado Emiliani** | Feriado colombiano que se traslada al lunes siguiente si no cae en ese día |
| **Recomputar** | Extender fecha fin para compensar días conflictivos en lugar de rechazar |
| **Soft-delete** | Marcar como borrado sin eliminar de la BD |
| **Snapshot** | Copia del valor en el momento (tarifa/costo al aprobar) |
