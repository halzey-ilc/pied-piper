from django.apps import AppConfig


class MessagingConfig(AppConfig):
    name = "platform_core.infrastructure.messaging"
    label = "messaging"
    verbose_name = "Transactional outbox / inbox"
    default_auto_field = "django.db.models.BigAutoField"
