import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()

from platform_core.infrastructure.observability.bootstrap import init_observability  # noqa: E402

init_observability(component="api")
