"""Piezas compartidas por los admin de modelos con soft-delete."""


class SoftDeleteAdminMixin:
    """Hace que el borrado masivo del admin también sea soft-delete.

    `SoftDeleteModel.delete()` es un método de **instancia**. La acción
    "Eliminar seleccionados" del admin no lo usa: llama a `queryset.delete()`,
    que es otro método y ejecuta un DELETE real en la base. El resultado era que
    borrar un objeto desde su ficha lo marcaba como eliminado, pero
    seleccionarlo en la lista lo hacía desaparecer de verdad — el mismo botón,
    dos comportamientos, y ninguna señal de cuál te tocaba.

    Solo se salvaban los objetos protegidos por una clave foránea `PROTECT`, y
    eso es un accidente afortunado, no una salvaguarda: un proyecto sin
    asignaciones se evaporaba sin dejar rastro.

    `delete_queryset` es el punto que usa la acción masiva. Recorriendo los
    objetos uno a uno se llama al `delete()` del modelo y se conserva la fila.
    """

    def delete_queryset(self, request, queryset):
        for objeto in queryset:
            objeto.delete()
