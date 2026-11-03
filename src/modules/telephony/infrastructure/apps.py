from django.apps import AppConfig


class TelephonyConfig(AppConfig):
    name = "modules.telephony.infrastructure"
    label = "telephony"
    verbose_name = "Telephony (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from modules.telephony.infrastructure.handlers import register_handlers
        from platform_core.application.messaging import event_handlers

        register_handlers(event_handlers)
