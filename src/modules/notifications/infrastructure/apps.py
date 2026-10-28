from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "modules.notifications.infrastructure"
    label = "notifications"
    verbose_name = "Notifications (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from modules.notifications.infrastructure.handlers import register_handlers
        from platform_core.application.messaging import event_handlers

        register_handlers(event_handlers)
