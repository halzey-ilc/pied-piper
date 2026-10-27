"""Production settings.

These settings only switch on HTTPS/cookie hardening. Environment validation in
config.settings.env rejects placeholder secrets, DEBUG, wildcard hosts and
non-TLS database connections. This alone does NOT make a deployment
production-ready: see docs/architecture/security-model.md.
"""

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *
from config.settings.base import ENV

if ENV.DJANGO_ENV != "production":
    raise ImproperlyConfigured("config.settings.production requires DJANGO_ENV=production")

DEBUG = False
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False  # opt in deliberately once the domain policy is agreed
SILENCED_SYSTEM_CHECKS = ["security.W021"]  # HSTS preload is a domain-level decision (see above)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
