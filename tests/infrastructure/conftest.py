import re
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceNotFoundError

from knowledge_assistant.infrastructure.configuration.settings import Settings


class Results(list):
    def __init__(self, rows=(), count=None):
        super().__init__(rows)
        self.count = len(self) if count is None else count

    def get_count(self):
        return self.count


class SearchFake:
    def __init__(self):
        self.rows, self.uploads, self.queries = {}, [], []
        self.fail_upload = None
        self.count_override = None
        self.search_rows = None

    def upload_documents(self, documents):
        self.uploads.append(documents)
        success = len(self.uploads) != self.fail_upload
        if success:
            self.rows.update({r["id"]: r for r in documents})
        return [SimpleNamespace(key=r["id"], succeeded=success) for r in documents]

    def get_document(self, key):
        if key not in self.rows:
            raise ResourceNotFoundError()
        return self.rows[key]

    def search(self, **kwargs):
        self.queries.append(kwargs)
        rows = list(self.rows.values())
        for field in ("corpusHash", "embeddingProfile", "service", "snapshotId"):
            value = re.search(field + r" eq '([^']+)'", kwargs["filter"])
            if value:
                rows = [r for r in rows if r.get(field) == value[1]]
        rows = [r for r in rows if r["kind"] != "__snapshot__"]
        if kwargs.get("top") == 0:
            return Results(rows, self.count_override)
        return Results(self.search_rows if self.search_rows is not None else rows)

    def close(self):
        pass


class IndexFake:
    def __init__(self):
        self.index = None
        self.created = []

    def get_index(self, name):
        if self.index is None:
            raise ResourceNotFoundError()
        return self.index

    def create_index(self, definition):
        self.index = definition
        self.created.append(definition)

    def get_index_statistics(self, name):
        return SimpleNamespace(storage_size=100, vector_index_size=0)

    def close(self):
        pass


class EmbeddingsFake:
    def __init__(self, dimensions=4):
        self.dimensions = dimensions
        self.requests = self.input_tokens = 0
        self.inputs = []

    def embed(self, texts):
        self.requests += 1
        self.input_tokens += sum(len(t) for t in texts)
        self.inputs.extend(texts)
        return [[0.1] * self.dimensions for _ in texts]

    def close(self):
        pass


@pytest.fixture
def cloud():
    return SimpleNamespace(
        settings=Settings(
            search_endpoint="https://example.search.windows.net",
            openai_endpoint="https://example.openai.azure.com/openai/v1/",
            embedding_deployment="test-embedding",
            embedding_dimensions=4,
        ),
        search=SearchFake(),
        index=IndexFake(),
        embeddings=EmbeddingsFake(),
    )
