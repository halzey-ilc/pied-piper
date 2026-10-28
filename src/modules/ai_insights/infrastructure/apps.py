from django.apps import AppConfig


class AiInsightsConfig(AppConfig):
    name = "modules.ai_insights.infrastructure"
    label = "ai_insights"
    verbose_name = "AI insights (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
