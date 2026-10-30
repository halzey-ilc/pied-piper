from django.apps import AppConfig


class PlanningConfig(AppConfig):
    name = "modules.planning.infrastructure"
    label = "planning"
    verbose_name = "Planning (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
