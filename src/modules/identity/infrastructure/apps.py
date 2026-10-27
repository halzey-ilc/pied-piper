from django.apps import AppConfig


class IdentityConfig(AppConfig):
    name = "modules.identity.infrastructure"
    label = "identity"
    verbose_name = "Identity"
    default_auto_field = "django.db.models.BigAutoField"
