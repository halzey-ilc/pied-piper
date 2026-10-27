from django.apps import AppConfig


class SalesKpiConfig(AppConfig):
    name = "modules.sales_kpi.infrastructure"
    label = "sales_kpi"
    verbose_name = "Sales KPI (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Register the event handlers that build the KPI read model from events.
        from modules.sales_kpi.infrastructure.handlers import register_handlers
        from platform_core.application.messaging import event_handlers

        register_handlers(event_handlers)
