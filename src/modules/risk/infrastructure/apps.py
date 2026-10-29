from django.apps import AppConfig


class RiskConfig(AppConfig):
    name = "modules.risk.infrastructure"
    label = "risk"
    verbose_name = "Risk rules (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
