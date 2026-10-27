from django.apps import AppConfig


class TenantRegistryConfig(AppConfig):
    name = "modules.tenant_registry.infrastructure"
    label = "tenant_registry"
    verbose_name = "Tenant registry"
    default_auto_field = "django.db.models.BigAutoField"
