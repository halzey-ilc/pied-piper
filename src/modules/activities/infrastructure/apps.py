from django.apps import AppConfig


class ActivitiesConfig(AppConfig):
    name = "modules.activities.infrastructure"
    label = "activities"
    verbose_name = "Activities (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
