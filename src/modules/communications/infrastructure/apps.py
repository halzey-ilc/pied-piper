from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    name = "modules.communications.infrastructure"
    label = "communications"
    verbose_name = "Communications (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
