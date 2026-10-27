from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "modules.audit.infrastructure"
    label = "audit"
    verbose_name = "Audit log"
    default_auto_field = "django.db.models.BigAutoField"
