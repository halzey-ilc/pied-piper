from django.apps import AppConfig


class WebFormsConfig(AppConfig):
    name = "modules.web_forms.infrastructure"
    label = "web_forms"
    verbose_name = "Website lead forms (control plane)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from modules.web_forms.infrastructure.cors import connect_cors

        connect_cors()
