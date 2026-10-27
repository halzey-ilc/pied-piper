from django.apps import AppConfig


class ReportingConfig(AppConfig):
    name = "modules.reporting.infrastructure"
    label = "reporting"
    verbose_name = "Reporting (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
