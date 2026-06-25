from math import ceil
from decimal import Decimal
from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from apps.calendar_engine.services import calcular_fecha_fin as _cal_fecha_fin, contar_dias_habiles
from .models import Asignacion, LogAuditoria
from django.shortcuts import render as dj_render
from .services import (
    aprobar_asignacion, rechazar_asignacion, revocar_asignacion,
    calcular_horas_jornada_completa, analizar_conflictos, aprobar_recomputando,
)


class AsignacionAdminForm(forms.ModelForm):
    fecha_fin_rango = forms.DateField(
        required=False,
        label="Fecha fin",
        help_text="Fecha de fin del rango (solo en modo 'Por rango de fechas').",
        widget=forms.DateInput(attrs={"type": "date", "style": "width:150px"}),
    )

    class Meta:
        model = Asignacion
        fields = "__all__"
        widgets = {
            "horas_totales": forms.NumberInput(attrs={"step": "1", "min": "1", "style": "width:120px"}),
            "dias_habiles": forms.NumberInput(attrs={"step": "1", "min": "1", "style": "width:120px"}),
            "intensidad_diaria": forms.NumberInput(attrs={"step": "0.5", "min": "0.5", "max": "8.5", "style": "width:120px"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["horas_totales"].required = False
        self.fields["dias_habiles"].required = False
        self.fields["intensidad_diaria"].required = False  # validado en clean()
        inst = getattr(self, "instance", None)
        if inst and inst.pk and inst.modo_asignacion == "RANGO" and inst.fecha_fin:
            self.fields["fecha_fin_rango"].initial = inst.fecha_fin

    def clean(self):
        cleaned = super().clean()
        modo = cleaned.get("modo_asignacion", "HORAS")
        intensidad = cleaned.get("intensidad_diaria")
        fecha_inicio = cleaned.get("fecha_inicio")
        jornada_completa = cleaned.get("jornada_completa", False)

        if modo == "HORAS":
            if not cleaned.get("horas_totales"):
                self.add_error("horas_totales", "Requerido en modo 'Por horas totales'.")
            if not intensidad:
                self.add_error("intensidad_diaria", "Requerido en modo 'Por horas totales'.")
        elif modo == "DIAS":
            if not cleaned.get("dias_habiles"):
                self.add_error("dias_habiles", "Requerido en modo 'Por días hábiles'.")
            if not intensidad:
                self.add_error("intensidad_diaria", "Requerido en modo 'Por días hábiles'.")
        elif modo == "RANGO":
            fecha_fin_rango = cleaned.get("fecha_fin_rango")
            if not fecha_fin_rango:
                self.add_error("fecha_fin_rango", "Requerido en modo 'Por rango de fechas'.")
            elif fecha_inicio and fecha_fin_rango < fecha_inicio:
                self.add_error("fecha_fin_rango", "La fecha fin debe ser posterior a la fecha de inicio.")
            if not jornada_completa and not intensidad:
                self.add_error("intensidad_diaria", "Requerido cuando no es jornada completa.")

        if intensidad and not jornada_completa and float(intensidad) > 8.5:
            self.add_error("intensidad_diaria", "Máximo 8.5 h/día (jornada lun–jue).")

        return cleaned


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
        "recurso", "proyecto", "estado_badge", "modo_asignacion",
        "horas_totales", "dias_habiles", "intensidad_diaria",
        "fecha_inicio", "fecha_fin", "acciones_rapidas",
    ]
    list_filter = ["estado", "modo_asignacion", "politica_ausencia", "proyecto"]
    search_fields = ["recurso__nombre", "proyecto__codigo"]
    readonly_fields = ["estado", "fecha_fin", "tarifa_aplicada", "costo_estimado", "solicitada_por", "created_at"]
    exclude = ["deleted_at", "updated_at"]
    inlines = [LogAuditoriaInline]
    actions = ["action_aprobar", "action_rechazar", "action_revocar"]

    class Media:
        js = ["assignments/admin_asignacion.js"]

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.groups.filter(name="Admin").exists()

    def changelist_view(self, request, extra_context=None):
        if not self.has_add_permission(request):
            self.message_user(
                request,
                format_html(
                    'Para crear una solicitud de asignación usá el '
                    '<a href="/solicitud/" style="font-weight:600">flujo de solicitud de recursos</a>.'
                ),
                level=messages.INFO,
            )
        return super().changelist_view(request, extra_context)

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
            path("aprobar/<int:pk>/confirmar/", self.admin_site.admin_view(self.view_aprobar_confirmar), name="asignacion-aprobar-confirmar"),
            path("rechazar/<int:pk>/", self.admin_site.admin_view(self.view_rechazar), name="asignacion-rechazar"),
            path("revocar/<int:pk>/", self.admin_site.admin_view(self.view_revocar), name="asignacion-revocar"),
        ]
        return custom + urls

    def _redirect_lista(self):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        return HttpResponseRedirect(reverse("admin:assignments_asignacion_changelist"))

    def _es_admin(self, request):
        return request.user.is_superuser or request.user.groups.filter(name="Admin").exists()

    def view_aprobar(self, request, pk):
        if not self._es_admin(request):
            self.message_user(request, "Se requiere rol Admin para aprobar asignaciones.", messages.ERROR)
            return self._redirect_lista()
        asig = Asignacion.objects.get(pk=pk)
        if asig.estado != "SOLICITADA":
            self.message_user(request, "Solo se pueden aprobar asignaciones en estado SOLICITADA.", messages.ERROR)
            return self._redirect_lista()
        conflict_dates, nueva_fecha_fin, nuevas_horas = analizar_conflictos(asig)
        if conflict_dates:
            from django.http import HttpResponseRedirect
            from django.urls import reverse
            return HttpResponseRedirect(reverse("admin:asignacion-aprobar-confirmar", kwargs={"pk": pk}))
        try:
            aprobar_asignacion(asig, request.user)
            self.message_user(request, f"Asignación #{pk} aprobada.", messages.SUCCESS)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        return self._redirect_lista()

    def view_aprobar_confirmar(self, request, pk):
        if not self._es_admin(request):
            self.message_user(request, "Se requiere rol Admin para aprobar asignaciones.", messages.ERROR)
            return self._redirect_lista()
        asig = Asignacion.objects.get(pk=pk)
        conflict_dates, nueva_fecha_fin, nuevas_horas = analizar_conflictos(asig)

        if not conflict_dates:
            # Ya no hay conflictos (otro usuario los resolvió), aprobar directo
            try:
                aprobar_asignacion(asig, request.user)
                self.message_user(request, f"Asignación #{pk} aprobada.", messages.SUCCESS)
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
            return self._redirect_lista()

        if request.method == "POST":
            try:
                aprobar_recomputando(asig, request.user, nueva_fecha_fin, nuevas_horas)
                self.message_user(
                    request,
                    f"Asignación #{pk} aprobada recomputando fechas — nueva fecha fin: {nueva_fecha_fin.strftime('%d/%m/%Y')}.",
                    messages.SUCCESS,
                )
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
            return self._redirect_lista()

        ctx = {
            **self.admin_site.each_context(request),
            "title": f"Confirmar aprobación — Asignación #{pk}",
            "asignacion": asig,
            "conflict_dates": conflict_dates,
            "nueva_fecha_fin": nueva_fecha_fin,
            "nuevas_horas": nuevas_horas,
            "opts": self.model._meta,
        }
        return dj_render(request, "admin/assignments/recomputo_confirmar.html", ctx)

    def view_rechazar(self, request, pk):
        if not self._es_admin(request):
            self.message_user(request, "Se requiere rol Admin para rechazar asignaciones.", messages.ERROR)
            return self._redirect_lista()
        asig = Asignacion.objects.get(pk=pk)
        rechazar_asignacion(asig, request.user)
        self.message_user(request, f"Asignación #{pk} rechazada.", messages.WARNING)
        return self._redirect_lista()

    def view_revocar(self, request, pk):
        if not self._es_admin(request):
            self.message_user(request, "Se requiere rol Admin para revocar asignaciones.", messages.ERROR)
            return self._redirect_lista()
        asig = Asignacion.objects.get(pk=pk)
        revocar_asignacion(asig, request.user)
        self.message_user(request, f"Asignación #{pk} revocada.", messages.WARNING)
        return self._redirect_lista()

    # ── Acciones masivas ─────────────────────────────────────────────────
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

    # ── Guardar: calcular campos según modo ──────────────────────────────
    def save_model(self, request, obj, form, change):
        if not obj.solicitada_por_id:
            obj.solicitada_por = request.user

        modo = obj.modo_asignacion

        if modo == "HORAS":
            if obj.horas_totales and obj.intensidad_diaria and obj.fecha_inicio:
                dias = ceil(float(obj.horas_totales) / float(obj.intensidad_diaria))
                obj.dias_habiles = dias
                obj.fecha_fin = _cal_fecha_fin(obj.fecha_inicio, dias, obj.recurso)

        elif modo == "DIAS":
            if obj.dias_habiles and obj.intensidad_diaria and obj.fecha_inicio:
                obj.horas_totales = ceil(obj.dias_habiles * float(obj.intensidad_diaria))
                obj.fecha_fin = _cal_fecha_fin(obj.fecha_inicio, obj.dias_habiles, obj.recurso)

        elif modo == "RANGO":
            fecha_fin_rango = form.cleaned_data.get("fecha_fin_rango")
            if fecha_fin_rango and obj.fecha_inicio:
                obj.fecha_fin = fecha_fin_rango
                obj.dias_habiles = contar_dias_habiles(obj.fecha_inicio, fecha_fin_rango, obj.recurso)
                if obj.jornada_completa:
                    obj.intensidad_diaria = Decimal("8.0")  # placeholder; carga real varía por día
                    obj.horas_totales = calcular_horas_jornada_completa(
                        obj.fecha_inicio, fecha_fin_rango, obj.recurso
                    )
                elif obj.intensidad_diaria:
                    obj.horas_totales = ceil(obj.dias_habiles * float(obj.intensidad_diaria))

        super().save_model(request, obj, form, change)

    def get_fieldsets(self, request, obj=None):
        return [
            ("Asignación", {
                "fields": [
                    "modo_asignacion",
                    "recurso", "proyecto", "cluster",
                    "fecha_inicio",
                    "horas_totales",
                    "dias_habiles",
                    "fecha_fin_rango",
                    "jornada_completa",
                    "intensidad_diaria",
                    "politica_ausencia",
                    "fecha_fin",
                ]
            }),
            ("Auditoría", {
                "fields": ["estado", "solicitada_por", "created_at", "tarifa_aplicada", "costo_estimado"],
                "classes": ["collapse"],
            }),
        ]


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
