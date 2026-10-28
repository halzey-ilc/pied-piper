from django.apps import AppConfig


class OperationsConfig(AppConfig):
    name = "modules.operations.infrastructure"
    label = "operations"
    verbose_name = "Operations rules (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
