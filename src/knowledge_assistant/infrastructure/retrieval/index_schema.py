"""Search stores retrievable text and identities; canonical payloads remain in the corpus."""

from azure.search.documents.indexes.models import (
    ExhaustiveKnnAlgorithmConfiguration,
    ExhaustiveKnnParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)


def index_definition(name: str, dimensions: int) -> SearchIndex:
    fields = [SimpleField(name="id", type="Edm.String", key=True)]
    fields += [
        SimpleField(name=field, type="Edm.String", filterable=True)
        for field in (
            "service",
            "snapshotId",
            "kind",
            "embeddingProfile",
            "corpusHash",
            "documentId",
            "documentHash",
        )
    ]
    fields += [SearchableField(name=field, type="Edm.String") for field in ("title", "content")]
    fields += [
        SimpleField(name=field, type="Edm.Int32") for field in ("chunkCount", "sourceDocumentCount")
    ]
    fields.append(
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            hidden=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="knowledge-profile",
        )
    )
    return SearchIndex(
        name=name,
        fields=fields,
        vector_search=VectorSearch(
            algorithms=[
                ExhaustiveKnnAlgorithmConfiguration(
                    name="exact-cosine", parameters=ExhaustiveKnnParameters(metric="cosine")
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name="knowledge-profile", algorithm_configuration_name="exact-cosine"
                )
            ],
        ),
    )


def compatible(actual: SearchIndex, expected: SearchIndex) -> bool:
    fields = {f.name: f for f in actual.fields}
    flags = ("key", "searchable", "filterable", "hidden")
    for wanted in expected.fields:
        found = fields.get(wanted.name)
        if found is None or found.type != wanted.type:
            return False
        if any(bool(getattr(found, p)) != bool(getattr(wanted, p)) for p in flags):
            return False
        if any(
            getattr(found, p) != getattr(wanted, p)
            for p in (
                "vector_search_dimensions",
                "vector_search_profile_name",
                "analyzer_name",
            )
        ):
            return False
    vectors = actual.vector_search
    return bool(
        vectors
        and any(
            p.name == "knowledge-profile" and p.algorithm_configuration_name == "exact-cosine"
            for p in vectors.profiles
        )
        and any(
            a.name == "exact-cosine"
            and a.kind == "exhaustiveKnn"
            and a.parameters
            and a.parameters.metric == "cosine"
            for a in vectors.algorithms
        )
    )
