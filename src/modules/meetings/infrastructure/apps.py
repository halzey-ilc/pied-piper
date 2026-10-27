from django.apps import AppConfig


class MeetingsConfig(AppConfig):
    name = "modules.meetings.infrastructure"
    label = "meetings"
    verbose_name = "Meetings (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
