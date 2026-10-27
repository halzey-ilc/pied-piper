from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    name = "modules.subscriptions.infrastructure"
    label = "subscriptions"
    verbose_name = "Subscriptions (control plane)"
    default_auto_field = "django.db.models.BigAutoField"
