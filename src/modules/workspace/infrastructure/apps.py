from django.apps import AppConfig


class WorkspaceConfig(AppConfig):
    name = "modules.workspace.infrastructure"
    label = "workspace"
    verbose_name = "Workspace layouts (tenant plane)"
    default_auto_field = "django.db.models.BigAutoField"
