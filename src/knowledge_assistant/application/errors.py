class SnapshotNotActiveError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class DependencyUnavailableError(Exception):
    """A retrieval or model provider could not complete the request."""
