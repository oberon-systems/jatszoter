class DomainError(Exception):
    """Base for every error the domain raises instead of an HTTP status. The message is logged
    only; a caller is told `code`, the class's CODE unless the raise names its own."""

    CODE = "refused"

    def __init__(self, message: str = "", code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or self.CODE


class NotFoundError(DomainError):
    """No stored row matches the fields a model was opened by."""

    CODE = "not-found"


class InvalidTransitionError(DomainError):
    """The requested lifecycle move is not allowed from the current status or by this actor."""

    CODE = "invalid-transition"


class NotAuthorizedError(DomainError):
    """A login or signed request did not check out; never says which check failed."""

    CODE = "not-authorized"


class ForbiddenError(DomainError):
    """The caller is known but may not do this."""

    CODE = "forbidden"


class InvalidPasswordError(DomainError):
    """A new password is too short or repeats the old one."""

    CODE = "password-invalid"


class ThrottledError(DomainError):
    """Too many failed attempts within the window."""

    CODE = "throttled"


class ConflictError(DomainError):
    """The request collides with what is already registered."""

    CODE = "conflict"


class InvalidBundleError(DomainError):
    """A scenario bundle, or the URL naming it, is refused."""

    CODE = "bundle-invalid"


class TooLargeError(DomainError):
    """A body or a bundle is over its size limit."""

    CODE = "too-large"


class InvalidReferenceError(DomainError):
    """A body names a row that does not exist or cannot be used."""

    CODE = "reference-invalid"


# The driver's error code, SQLSTATE or its own name, to the domain error it means.
DB_ERRORS: dict[str | None, type[DomainError]] = {
    "23505": ConflictError,
    "SQLITE_CONSTRAINT_UNIQUE": ConflictError,
    "SQLITE_CONSTRAINT_PRIMARYKEY": ConflictError,
}
