from django.contrib import admin, messages
from django.utils.html import format_html, mark_safe, escape
from .models import Recurso, Proyecto, Skill, RecursoSkill, Cluster, TarifaVigente


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ["nombre", "descripcion_corta", "total_recursos"]
    search_fields = ["nombre"]
    fields = ["nombre", "descripcion"]

    @admin.display(description="Descripción")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return format_html('<span style="color:#aaa">—</span>')
        text = obj.descripcion
        return (text[:70] + "…") if len(text) > 70 else text

    @admin.display(description="Recursos activos")
    def total_recursos(self, obj):
        return obj.recurso_skills.filter(recurso__activo=True, recurso__deleted_at__isnull=True).count()


@admin.register(Cluster)
class ClusterAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "total_recursos"]
    search_fields = ["codigo", "nombre"]
    fields = ["codigo", "nombre"]

    @admin.display(description="Recursos")
    def total_recursos(self, obj):
        return obj.recursos.filter(activo=True, deleted_at__isnull=True).count()


class TarifaVigenteInline(admin.TabularInline):
    model = TarifaVigente
    extra = 1
    readonly_fields = ["creado_en"]
    fields = ["valor_hora", "fecha_desde", "creado_en"]
    ordering = ["-fecha_desde"]

    def has_change_permission(self, request, obj=None):
        return False  # append-only: solo agregar, nunca editar


class RecursoSkillInline(admin.TabularInline):
    model = RecursoSkill
    extra = 1
    autocomplete_fields = ["skill"]
    fields = ["skill", "suficiencia", "estrellas_display"]
    readonly_fields = ["estrellas_display"]

    @admin.display(description="")
    def estrellas_display(self, obj):
        if not obj.pk:
            return ""
        colors = ["#dc3545", "#fd7e14", "#ffc107", "#20c997", "#198754"]
        color = colors[obj.suficiencia - 1]
        filled = "★" * obj.suficiencia
        empty = "☆" * (5 - obj.suficiencia)
        return format_html(
            '<span style="color:{};font-size:1.15rem;letter-spacing:1px">{}</span>'
            '<span style="color:#ccc;font-size:1.15rem;letter-spacing:1px">{}</span>',
            color, filled, empty,
        )


@admin.register(Recurso)
class RecursoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "nro_persona_sap", "email", "banda", "clusters_display", "skills_display", "activo"]
    list_filter = ["banda", "activo", "clusters", "skills"]
    search_fields = ["nombre", "email", "nro_persona_sap"]
    inlines = [RecursoSkillInline, TarifaVigenteInline]
    list_per_page = 50
    exclude = ["deleted_at", "created_at", "updated_at", "skills"]
    filter_horizontal = ["clusters"]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        # La tarifa sigue el costo del recurso: al registrar una nueva vigencia,
        # un signal recomputa el costo estimado de las asignaciones activas.
        if formset.model is TarifaVigente and formset.new_objects:
            messages.info(
                request,
                "Nueva tarifa registrada. El costo estimado de las asignaciones activas del "
                "recurso se recomputó automáticamente con la tarifa vigente de cada día "
                "(trazado en el log de auditoría como RECOMPUTO_TARIFA).",
            )

    @admin.display(description="Clusters")
    def clusters_display(self, obj):
        items = list(obj.clusters.all())
        if not items:
            return format_html('<span style="color:#aaa">—</span>')
        return ", ".join(c.codigo for c in items)

    @admin.display(description="Skills")
    def skills_display(self, obj):
        entries = list(obj.recurso_skills.select_related("skill").all())
        if not entries:
            return format_html('<span style="color:#aaa">—</span>')

        count = len(entries)
        label = f"{count} skill{'s' if count != 1 else ''}"

        rows = mark_safe("".join(
            '<div class="inet-skill-row{}">'
            '<span>{}</span>'
            '<span style="color:#e0178a;letter-spacing:1px">{}</span>'
            '</div>'.format(
                "" if i < len(entries) - 1 else " inet-skill-row-last",
                escape(rs.skill.nombre),
                "★" * rs.suficiencia,
            )
            for i, rs in enumerate(entries)
        ))

        return format_html(
            '<div class="inet-skill-wrap">'
            '<button type="button" class="inet-skill-btn" onclick="inetSkillClick(event,this)">{}</button>'
            '<div class="inet-skill-popup">'
            '<div class="inet-skill-popup-hd">Skills</div>{}'
            '</div>'
            '</div>',
            label, rows,
        )


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "codigo_pep", "grafo", "nombre", "cliente", "estado", "pm", "fecha_inicio", "fecha_fin"]
    list_filter = ["estado"]
    search_fields = ["codigo", "codigo_pep", "grafo", "nombre", "cliente"]
    exclude = ["deleted_at", "created_at", "updated_at"]
