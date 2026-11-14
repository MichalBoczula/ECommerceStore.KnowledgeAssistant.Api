from knowledge_assistant.domain.errors import EvidenceVerificationError


class SnapshotNotActiveError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class DependencyUnavailableError(Exception):
    """A retrieval or model provider could not complete the request."""


class AnswerAssemblyError(EvidenceVerificationError):
    """An answer plan could not be assembled from its retrieved evidence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
