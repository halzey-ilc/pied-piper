from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "modules.payments.infrastructure"
    label = "payments"
    verbose_name = "Payments (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
