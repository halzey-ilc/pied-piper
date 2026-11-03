from django.apps import AppConfig


class ReferenceListsConfig(AppConfig):
    name = "modules.reference_lists.infrastructure"
    label = "reference_lists"
    verbose_name = "Company reference lists (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
