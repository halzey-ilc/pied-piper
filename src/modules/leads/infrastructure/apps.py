from django.apps import AppConfig


class LeadsConfig(AppConfig):
    name = "modules.leads.infrastructure"
    label = "leads"
    verbose_name = "Leads (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
