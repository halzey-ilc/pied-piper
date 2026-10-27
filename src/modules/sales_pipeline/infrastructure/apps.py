from django.apps import AppConfig


class SalesPipelineConfig(AppConfig):
    name = "modules.sales_pipeline.infrastructure"
    label = "sales_pipeline"
    verbose_name = "Sales pipeline (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
