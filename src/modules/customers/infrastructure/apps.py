from django.apps import AppConfig


class CustomersConfig(AppConfig):
    name = "modules.customers.infrastructure"
    label = "customers"
    verbose_name = "Customers (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
