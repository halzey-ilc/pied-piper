"""Platform kernel: tenancy runtime, messaging (outbox/inbox), observability, API plumbing.

platform_core never imports business modules; it reaches them only through
ports configured in settings (dependency inversion).
"""
