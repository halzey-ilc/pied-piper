"""Domain error hierarchy. Presentation maps these to HTTP responses."""


class DomainError(Exception):
    """Base class for expected business errors. Messages must be safe to show to clients."""

    code = "domain_error"


class DomainValidationError(DomainError):
    code = "validation_error"


class NotFoundError(DomainError):
    code = "not_found"


class AccessDeniedError(DomainError):
    code = "access_denied"


class ConflictError(DomainError):
    code = "conflict"
