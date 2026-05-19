"""Package-level exceptions for chemunited-workflow."""


class ConcurrentClientAccessError(RuntimeError):
    """Raised when a ComponentClient is accessed from more than one thread at a time."""
