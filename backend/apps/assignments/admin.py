import uuid
from datetime import date as _date
from math import ceil
from decimal import Decimal
from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from apps.accounts.roles import es_admin
from apps.calendar_engine.services import calcular_fecha_fin as _cal_fecha_fin, contar_dias_habiles
from apps.core.models import Proyecto
from .models import Asignacion, CesionHoras, LogAuditoria
from django.shortcuts import render as dj_render
from .services import (
    aprobar_asignacion, rechazar_asignacion, revocar_asignacion,
    calcular_horas_jornada_completa, analizar_conflictos, aprobar_recomputando,
    ceder_horas,
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


class SerieFilter(admin.SimpleListFilter):
    """Permite ?serie=<uuid> en el changelist (enlace de la columna Serie).
    Solo se muestra en la barra lateral cuando el filtro está activo."""
    title = "Serie"
    parameter_name = "serie"

    def lookups(self, request, model_admin):
        valor = request.GET.get(self.parameter_name)
        if not valor:
            return []
        try:
            uuid.UUID(valor)
        except ValueError:
            return []
        return [(valor, f"↻ {valor[:8]}")]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(serie=self.value())
        return queryset


class LogAuditoriaInline(admin.TabularInline):
    model = LogAuditoria
    extra = 0
    readonly_fields = ["accion", "actor", "timestamp", "detalle"]
    can_delete = False
    show_change_link = False


class CesionRealizadaInline(admin.TabularInline):
    """Cesiones de horas hechas desde esta asignación (solo lectura;
    se crean con el botón ⇄ Ceder del listado)."""
    model = CesionHoras
    fk_name = "asignacion_origen"
    extra = 0
    verbose_name_plural = "Cesiones de horas realizadas"
    readonly_fields = [
        "fecha", "horas", "politica", "tarifa_hora",
        "asignacion_destino", "creado_por", "creado_en", "anulada_en",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Asignacion)
class AsignacionAdmin(admin.ModelAdmin):
    form = AsignacionAdminForm
    list_display = [
        "recurso", "acciones_rapidas", "proyecto", "estado_badge", "modo_asignacion",
        "serie_corta", "horas_totales", "intensidad_diaria", "fecha_inicio", "fecha_fin",
    ]
    list_display_links = ["recurso"]
    list_filter = ["estado", "modo_asignacion", "politica_ausencia", "proyecto", SerieFilter]
    search_fields = ["recurso__nombre", "proyecto__codigo"]
    readonly_fields = ["estado", "fecha_fin", "tarifa_aplicada", "costo_estimado", "solicitada_por", "created_at"]
    exclude = ["deleted_at", "updated_at"]
    inlines = [LogAuditoriaInline, CesionRealizadaInline]
    actions = ["action_aprobar", "action_rechazar", "action_revocar"]

    class Media:
        js = ["assignments/admin_asignacion.js"]

    def has_add_permission(self, request):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)

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

    @admin.display(description="Serie", ordering="serie")
    def serie_corta(self, obj):
        """Identificador corto de la serie recurrente; filtra al hacer clic."""
        if not obj.serie:
            return format_html('<span style="color:#aaa">—</span>')
        return format_html(
            '<a href="?serie={}" title="Ver toda la serie" '
            'style="font-family:monospace;font-size:.8em;background:#ede8f5;color:#4a1f7a;'
            'padding:2px 6px;border-radius:4px;text-decoration:none">↻ {}</a>',
            obj.serie, str(obj.serie)[:8],
        )

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
    @staticmethod
    def _btn(href, label, color):
        style = (
            f"background:{color};color:#fff;padding:2px 10px;border-radius:4px;"
            "text-decoration:none;font-size:.75em;font-weight:600;white-space:nowrap;"
            "text-transform:none;letter-spacing:0;font-family:inherit"
        )
        return format_html('<a href="{}" style="{}">{}</a>', href, style, label)

    @admin.display(description="Acciones")
    def acciones_rapidas(self, obj):
        editar = self._btn(f"{obj.pk}/change/", "✎ Editar", "#4f46e5")

        if obj.estado == "SOLICITADA":
            aprobar  = self._btn(f"aprobar/{obj.pk}/",  "✓ Aprobar",  "#16a34a")
            rechazar = self._btn(f"rechazar/{obj.pk}/", "✗ Rechazar", "#dc2626")
            return format_html(
                '<div style="display:flex;gap:4px;align-items:center">{}{}{}</div>',
                editar, aprobar, rechazar,
            )
        if obj.estado == "APROBADA":
            ceder = self._btn(f"ceder/{obj.pk}/", "⇄ Ceder", "#0d9488")
            revocar = self._btn(f"revocar/{obj.pk}/", "↩ Revocar", "#f97316")
            return format_html(
                '<div style="display:flex;gap:4px;align-items:center">{}{}{}</div>',
                editar, ceder, revocar,
            )
        return format_html('<div>{}</div>', editar)

    # ── URLs personalizadas para los botones ─────────────────────────────
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path("aprobar/<int:pk>/", self.admin_site.admin_view(self.view_aprobar), name="asignacion-aprobar"),
            path("aprobar/<int:pk>/confirmar/", self.admin_site.admin_view(self.view_aprobar_confirmar), name="asignacion-aprobar-confirmar"),
            path("rechazar/<int:pk>/", self.admin_site.admin_view(self.view_rechazar), name="asignacion-rechazar"),
            path("revocar/<int:pk>/", self.admin_site.admin_view(self.view_revocar), name="asignacion-revocar"),
            path("ceder/<int:pk>/", self.admin_site.admin_view(self.view_ceder), name="asignacion-ceder"),
        ]
        return custom + urls

    def _redirect_lista(self):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        return HttpResponseRedirect(reverse("admin:assignments_asignacion_changelist"))

    def _es_admin(self, request):
        return es_admin(request.user)

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

    def view_ceder(self, request, pk):
        """Formulario para ceder horas de un día de una asignación APROBADA a otro proyecto."""
        if not self._es_admin(request):
            self.message_user(request, "Se requiere rol Admin para ceder horas.", messages.ERROR)
            return self._redirect_lista()
        asig = Asignacion.objects.select_related("recurso", "proyecto").get(pk=pk)
        if asig.estado != "APROBADA":
            self.message_user(request, "Solo se pueden ceder horas de asignaciones APROBADAS.", messages.ERROR)
            return self._redirect_lista()

        ctx = {
            **self.admin_site.each_context(request),
            "title": f"Ceder horas — Asignación #{pk}",
            "asignacion": asig,
            "proyectos": Proyecto.objects.filter(estado="ACTIVO").exclude(pk=asig.proyecto_id).order_by("codigo"),
            "opts": self.model._meta,
            "post": request.POST if request.method == "POST" else {},
        }

        if request.method == "POST":
            try:
                fecha = _date.fromisoformat(request.POST.get("fecha", ""))
                horas = float((request.POST.get("horas") or "").replace(",", "."))
                proyecto = Proyecto.objects.get(pk=request.POST.get("proyecto"), estado="ACTIVO")
                politica = request.POST.get("politica", "")
            except (ValueError, TypeError, Proyecto.DoesNotExist):
                ctx["error"] = "Formulario incompleto o con valores inválidos."
                return dj_render(request, "admin/assignments/ceder_form.html", ctx)
            try:
                cesion = ceder_horas(asig, proyecto, fecha, horas, politica, request.user)
            except ValueError as e:
                ctx["error"] = str(e)
                return dj_render(request, "admin/assignments/ceder_form.html", ctx)
            self.message_user(
                request,
                format_html(
                    "Cesión registrada: {} h del {} de {} → <b>{}</b>. "
                    "Se creó la solicitud #{} para el proyecto receptor: apruébela para hacerla efectiva.",
                    cesion.horas, fecha.strftime("%d/%m/%Y"), asig.recurso.nombre,
                    proyecto.codigo, cesion.asignacion_destino_id,
                ),
                messages.SUCCESS,
            )
            return self._redirect_lista()

        return dj_render(request, "admin/assignments/ceder_form.html", ctx)

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


@admin.register(CesionHoras)
class CesionHorasAdmin(admin.ModelAdmin):
    """Trazabilidad de cesiones: solo lectura (se crean con el botón ⇄ Ceder)."""
    list_display = [
        "id", "recurso_nombre", "fecha", "horas", "proyecto_origen", "proyecto_destino",
        "politica", "tarifa_hora", "estado_cesion", "creado_por", "creado_en",
    ]
    list_filter = ["politica"]
    date_hierarchy = "fecha"

    @admin.display(description="Recurso")
    def recurso_nombre(self, obj):
        return obj.asignacion_origen.recurso.nombre

    @admin.display(description="Proyecto origen")
    def proyecto_origen(self, obj):
        return obj.asignacion_origen.proyecto.codigo

    @admin.display(description="Proyecto destino")
    def proyecto_destino(self, obj):
        return obj.asignacion_destino.proyecto.codigo

    @admin.display(description="Estado")
    def estado_cesion(self, obj):
        if obj.anulada_en:
            color, texto = "#9ca3af", "ANULADA"
        elif obj.asignacion_destino.estado == "APROBADA":
            color, texto = "#16a34a", "EFECTIVA"
        else:
            color, texto = "#6366f1", "RESERVADA"
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:.8em">{}</span>',
            color, texto,
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "asignacion_origen__recurso", "asignacion_origen__proyecto",
            "asignacion_destino__proyecto", "creado_por",
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
