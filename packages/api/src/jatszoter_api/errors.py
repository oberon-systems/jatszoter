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


class ConflictError(DomainError):
    """The request collides with what is already registered."""


class InvalidBundleError(DomainError):
    """A scenario bundle, or the URL naming it, is refused; the message says why."""


class TooLargeError(DomainError):
    """A body or a bundle is over its size limit."""


# The driver's error code, SQLSTATE or its own name, to the domain error it means.
DB_ERRORS: dict[str | None, type[DomainError]] = {
    "23505": ConflictError,
    "SQLITE_CONSTRAINT_UNIQUE": ConflictError,
    "SQLITE_CONSTRAINT_PRIMARYKEY": ConflictError,
}
