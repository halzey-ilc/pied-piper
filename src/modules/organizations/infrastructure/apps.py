from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    name = "modules.organizations.infrastructure"
    label = "organizations"
    verbose_name = "Organizations"
    default_auto_field = "django.db.models.BigAutoField"
