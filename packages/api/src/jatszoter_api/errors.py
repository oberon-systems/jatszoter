class DomainError(Exception):
    """Base for every error the domain raises instead of an HTTP status."""


class NotFoundError(DomainError):
    """No stored row matches the fields a model was opened by."""


class InvalidTransitionError(DomainError):
    """The requested lifecycle move is not allowed from the current status or by this actor."""
