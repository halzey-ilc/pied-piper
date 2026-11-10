from django.apps import AppConfig


class AiInsightsConfig(AppConfig):
    name = "modules.ai_insights.infrastructure"
    label = "ai_insights"
    verbose_name = "AI insights (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from modules.ai_insights.infrastructure.handlers import register_handlers
        from platform_core.application.messaging import event_handlers

        register_handlers(event_handlers)
