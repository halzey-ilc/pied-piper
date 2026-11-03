from django.apps import AppConfig


class ImportsConfig(AppConfig):
    name = "modules.imports.infrastructure"
    label = "imports"
    verbose_name = "Imports of leads and customers (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
