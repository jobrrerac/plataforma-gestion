from django.apps import AppConfig


class AssignmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assignments"
    verbose_name = "Asignaciones"

    def ready(self):
        from . import signals  # noqa: F401 — conecta el recomputo por cambio de tarifa
