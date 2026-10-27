from django.apps import AppConfig


class TelephonyConfig(AppConfig):
    name = "modules.telephony.infrastructure"
    label = "telephony"
    verbose_name = "Telephony (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
