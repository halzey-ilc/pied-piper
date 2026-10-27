"""Local development settings (docker compose). Never use outside a developer machine."""

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *
from config.settings.base import ENV

if ENV.DJANGO_ENV != "local":
    raise ImproperlyConfigured("config.settings.local requires DJANGO_ENV=local")
