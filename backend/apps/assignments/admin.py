from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from .models import Asignacion, LogAuditoria
from .services import calcular_fecha_fin, aprobar_asignacion, rechazar_asignacion, revocar_asignacion


class AsignacionAdminForm(forms.ModelForm):
    class Meta:
        model = Asignacion
        fields = "__all__"
        widgets = {
            "horas_totales": forms.NumberInput(attrs={"step": "1", "min": "1", "style": "width:120px"}),
            "intensidad_diaria": forms.NumberInput(attrs={"step": "0.5", "min": "0.5", "max": "8", "style": "width:120px"}),
        }


class LogAuditoriaInline(admin.TabularInline):
    model = LogAuditoria
    extra = 0
    readonly_fields = ["accion", "actor", "timestamp", "detalle"]
    can_delete = False
    show_change_link = False


@admin.register(Asignacion)
class AsignacionAdmin(admin.ModelAdmin):
    form = AsignacionAdminForm
    list_display = [
        "recurso", "proyecto", "estado_badge",
        "horas_totales", "intensidad_diaria",
        "fecha_inicio", "fecha_fin", "acciones_rapidas",
    ]
    list_filter = ["estado", "politica_ausencia", "proyecto"]
    search_fields = ["recurso__nombre", "proyecto__codigo"]
    readonly_fields = ["estado", "fecha_fin", "tarifa_aplicada", "costo_estimado", "solicitada_por", "created_at"]
    exclude = ["deleted_at", "updated_at"]
    inlines = [LogAuditoriaInline]
    actions = ["action_aprobar", "action_rechazar", "action_revocar"]

    # ── Columna de estado con color ──────────────────────────────────────
    @admin.display(description="Estado", ordering="estado")
    def estado_badge(self, obj):
        colores = {
            "SOLICITADA": "#6366f1",
            "APROBADA":   "#16a34a",
            "RECHAZADA":  "#dc2626",
            "REVOCADA":   "#9ca3af",
            "INVALIDADA": "#f97316",
        }
        color = colores.get(obj.estado, "#6b7280")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:.8em">{}</span>',
            color, obj.get_estado_display()
        )

    # ── Botones de acción en la columna de lista ─────────────────────────
    @admin.display(description="Acciones")
    def acciones_rapidas(self, obj):
        if obj.estado == "SOLICITADA":
            return format_html(
                '<a href="aprobar/{}/" class="button" style="background:#16a34a;color:#fff;padding:2px 8px;border-radius:4px;text-decoration:none;font-size:.8em;margin-right:4px">✓ Aprobar</a>'
                '<a href="rechazar/{}/" class="button" style="background:#dc2626;color:#fff;padding:2px 8px;border-radius:4px;text-decoration:none;font-size:.8em">✗ Rechazar</a>',
                obj.pk, obj.pk
            )
        if obj.estado == "APROBADA":
            return format_html(
                '<a href="revocar/{}/" class="button" style="background:#f97316;color:#fff;padding:2px 8px;border-radius:4px;text-decoration:none;font-size:.8em">↩ Revocar</a>',
                obj.pk
            )
        return "—"

    # ── URLs personalizadas para los botones ─────────────────────────────
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path("aprobar/<int:pk>/", self.admin_site.admin_view(self.view_aprobar), name="asignacion-aprobar"),
            path("rechazar/<int:pk>/", self.admin_site.admin_view(self.view_rechazar), name="asignacion-rechazar"),
            path("revocar/<int:pk>/", self.admin_site.admin_view(self.view_revocar), name="asignacion-revocar"),
        ]
        return custom + urls

    def _redirect_lista(self):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        return HttpResponseRedirect(reverse("admin:assignments_asignacion_changelist"))

    def view_aprobar(self, request, pk):
        asig = Asignacion.objects.get(pk=pk)
        try:
            aprobar_asignacion(asig, request.user)
            self.message_user(request, f"Asignación #{pk} aprobada.", messages.SUCCESS)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        return self._redirect_lista()

    def view_rechazar(self, request, pk):
        asig = Asignacion.objects.get(pk=pk)
        rechazar_asignacion(asig, request.user)
        self.message_user(request, f"Asignación #{pk} rechazada.", messages.WARNING)
        return self._redirect_lista()

    def view_revocar(self, request, pk):
        asig = Asignacion.objects.get(pk=pk)
        revocar_asignacion(asig, request.user)
        self.message_user(request, f"Asignación #{pk} revocada.", messages.WARNING)
        return self._redirect_lista()

    # ── Acciones masivas (checkbox + dropdown) ───────────────────────────
    @admin.action(description="✓ Aprobar asignaciones seleccionadas")
    def action_aprobar(self, request, queryset):
        ok = err = 0
        for asig in queryset.filter(estado="SOLICITADA"):
            try:
                aprobar_asignacion(asig, request.user)
                ok += 1
            except ValueError as e:
                self.message_user(request, f"#{asig.pk} — {e}", messages.ERROR)
                err += 1
        if ok:
            self.message_user(request, f"{ok} asignación(es) aprobada(s).", messages.SUCCESS)

    @admin.action(description="✗ Rechazar asignaciones seleccionadas")
    def action_rechazar(self, request, queryset):
        n = 0
        for asig in queryset.filter(estado="SOLICITADA"):
            rechazar_asignacion(asig, request.user)
            n += 1
        self.message_user(request, f"{n} asignación(es) rechazada(s).", messages.WARNING)

    @admin.action(description="↩ Revocar asignaciones seleccionadas")
    def action_revocar(self, request, queryset):
        n = 0
        for asig in queryset.filter(estado="APROBADA"):
            revocar_asignacion(asig, request.user)
            n += 1
        self.message_user(request, f"{n} asignación(es) revocada(s).", messages.WARNING)

    # ── Guardar: calcular fecha_fin y registrar solicitante ──────────────
    def save_model(self, request, obj, form, change):
        if not obj.solicitada_por_id:
            obj.solicitada_por = request.user
        if obj.horas_totales and obj.intensidad_diaria and obj.fecha_inicio:
            obj.fecha_fin = calcular_fecha_fin(
                obj.recurso, obj.fecha_inicio, obj.horas_totales, obj.intensidad_diaria
            )
        super().save_model(request, obj, form, change)


@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ["asignacion", "accion", "actor", "timestamp"]
    readonly_fields = ["asignacion", "accion", "actor", "timestamp", "detalle"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
