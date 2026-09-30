class DomainError(Exception):
    """Base for every error the domain raises instead of an HTTP status."""


class NotFoundError(DomainError):
    """No stored row matches the fields a model was opened by."""


class InvalidTransitionError(DomainError):
    """The requested lifecycle move is not allowed from the current status or by this actor."""


class NotAuthorizedError(DomainError):
    """A login or signed request did not check out; never says which check failed."""


class ForbiddenError(DomainError):
    """The caller is known but may not do this."""


class InvalidPasswordError(DomainError):
    """A new password is too short or repeats the old one."""


class ThrottledError(DomainError):
    """Too many failed attempts within the window."""
