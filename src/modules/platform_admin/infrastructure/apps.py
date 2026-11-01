from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    name = "modules.platform_admin.infrastructure"
    label = "platform_admin"
    verbose_name = "Platform administration (control plane)"
    default_auto_field = "django.db.models.BigAutoField"
