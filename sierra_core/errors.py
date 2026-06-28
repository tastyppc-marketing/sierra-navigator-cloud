class SierraError(Exception):
    """Base for all sierra_core errors."""

class SierraAuthError(SierraError):
    """Login failed or session could not be (re)established."""

class WriteNotAllowed(SierraError):
    """A write/delete was attempted but the client is read-only (allow_write=False)."""

class IdentityLockError(SierraError):
    """A row action's supplied title did not match the stored title for that id."""

class EndpointError(SierraError):
    """Sierra returned a non-zero responseCode or a transport-level failure."""
    def __init__(self, message, *, raw=None):
        super().__init__(message)
        self.raw = raw
