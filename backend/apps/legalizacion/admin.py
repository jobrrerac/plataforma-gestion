from django.contrib import admin

from apps.core.admin_mixins import SoftDeleteAdminMixin

from .models import (
    DiaLegalizado,
    ParametrosLegalizacion,
    ReaperturaDia,
    RegistroHoras,
    TipoActividad,
)


@admin.register(TipoActividad)
class TipoActividadAdmin(admin.ModelAdmin):
    list_display = ["nombre", "descripcion", "requiere_proyecto", "activo", "orden"]
    list_filter = ["requiere_proyecto", "activo"]
    list_editable = ["activo", "orden"]
    ordering = ["orden", "nombre"]


class RegistroHorasInline(admin.TabularInline):
    """Los renglones del día, para verlos. No para escribirlos.

    Este inline era editable, y con eso un Admin podía cambiar las horas de un
    día ya firmado sin que quedara rastro de nada: ni quién lo cambió, ni qué
    decía antes, ni por qué. Borraba además la firma del PM por el camino, que
    es justo lo que hace falta para responder «¿quién aprobó esto?».

    Corregir un día firmado se hace reabriéndolo (`services.reabrir_dia`): eso
    guarda una copia de las firmas deshechas, exige un motivo, y devuelve los
    renglones a quien los escribió para que los arregle. Más pasos, y cada uno
    deja constancia.

    Los permisos del grupo Admin ya no incluyen escritura aquí, pero eso solo
    no basta: un superusuario se los salta todos. Por eso lo niega la clase.
    """

    model = RegistroHoras
    extra = 0
    max_num = 0
    can_delete = False
    fields = ["tipo_actividad", "proyecto", "horas", "detalle", "estado", "aprobado_por"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegistroHoras)
class RegistroHorasAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Solo para leer qué avisos del triaje se están anulando.

    Pedir un motivo al firmar un día con avisos no sirve de nada si después
    nadie puede leerlo. Filtrando por «aprobación forzada» sale la lista de qué
    regla se salta todo el mundo, que es justo la que hay que corregir antes de
    añadir ninguna otra.

    **No se escribe desde aquí.** Aprobar es `aprobar_registro`, que relee bajo
    bloqueo y comprueba quién puede firmar qué; un formulario de admin sobre
    `estado` sería una segunda vía de aprobación sin ninguna de esas dos cosas.
    """

    list_display = [
        "dia", "destino", "horas", "estado",
        "aprobacion_forzada", "reglas_anuladas", "motivo_aprobacion",
    ]
    list_filter = ["aprobacion_forzada", "estado", "dia__fecha"]
    search_fields = ["dia__recurso__nombre", "detalle", "motivo_aprobacion"]
    date_hierarchy = "dia__fecha"
    ordering = ["-dia__fecha"]

    @admin.display(description="Destino")
    def destino(self, obj):
        return obj.proyecto.codigo if obj.proyecto_id else obj.tipo_actividad.nombre

    @admin.display(description="Avisos anulados")
    def reglas_anuladas(self, obj):
        return ", ".join(obj.senales_anuladas) or "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DiaLegalizado)
class DiaLegalizadoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Para consultar un día y sus renglones. Se mira, no se toca.

    Con `estado` editable, bajar un día de APROBADO a ABIERTO desde este
    formulario era una reapertura sin motivo, sin autor y sin copia de las
    firmas: exactamente la acción que `reabrir_dia` existe para dejar anotada.
    Y `total_horas` es un campo calculado que este formulario no recalculaba,
    así que editar el inline dejaba el total mintiendo sobre sus propios
    renglones.

    Lo que se hace en su lugar:

    - Un día firmado se corrige **reabriéndolo** desde la ficha del recurso.
    - Un día abierto lo corrige quien lo registró, en su propia pantalla.

    Se conserva `SoftDeleteAdminMixin` aunque ahora mismo no borre nada: si
    alguien vuelve a activar el borrado, que al menos no destruya las filas.
    """

    list_display = ["recurso", "fecha", "estado", "total_horas", "jornada_esperada", "registrado_en"]
    list_filter = ["estado", "fecha"]
    search_fields = ["recurso__nombre", "recurso__email"]
    date_hierarchy = "fecha"
    inlines = [RegistroHorasInline]
    exclude = ["deleted_at", "created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name not in self.exclude]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReaperturaDia)
class ReaperturaDiaAdmin(admin.ModelAdmin):
    """Quién deshizo firmas, cuándo y por qué. Solo lectura, y de verdad.

    No lleva `SoftDeleteAdminMixin` porque no es un `SoftDeleteModel`: es
    append-only. Ni el admin, ni el shell, ni `psql` pueden tocar estas filas —
    el modelo lo impide y un disparador de PostgreSQL lo respalda.
    """

    list_display = ["dia", "actor", "creado_en", "estado_anterior", "cuantas_firmas", "motivo"]
    list_filter = ["creado_en", "estado_anterior"]
    search_fields = ["dia__recurso__nombre", "motivo", "actor__username"]
    date_hierarchy = "creado_en"

    @admin.display(description="Firmas deshechas")
    def cuantas_firmas(self, obj):
        return len(obj.firmas_revertidas or [])

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ParametrosLegalizacion)
class ParametrosLegalizacionAdmin(admin.ModelAdmin):
    """La única pantalla de este módulo donde un Admin sí escribe.

    Es el punto del ajuste que no debería costar un despliegue: mover la fecha
    desde la que se reclaman días pendientes.

    Una sola fila, así que no se añade ni se borra: se entra y se cambia. El
    botón de «Añadir» se esconde en cuanto existe, para que nadie cree una
    segunda que no se leería nunca.
    """

    list_display = ["inicio_exigencia", "actualizado_en"]
    readonly_fields = ["actualizado_en"]

    def has_add_permission(self, request):
        return not ParametrosLegalizacion.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
