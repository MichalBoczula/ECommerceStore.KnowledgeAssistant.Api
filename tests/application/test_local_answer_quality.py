import copy

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.services.evidence_catalog import select_contract
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.application.services.knowledge_service import KnowledgeService
from knowledge_assistant.infrastructure.retrieval.local_retriever import LocalRetriever


def ask(corpus, question):
    return KnowledgeService(corpus, LocalRetriever(corpus), EvidencePlanner()).query(
        Query(question=question, service="products", snapshot_id=corpus.snapshot_id)
    )


def test_format_words_do_not_authorize_an_unrelated_contract(demo):
    answer = ask(demo.corpus, "Show the teleportation warranty request contract")
    assert answer.status == "not_found" and not answer.sections and not answer.sources


def test_camel_case_operation_selects_the_original_endpoint_with_metadata_noise(demo):
    corpus = copy.deepcopy(demo.corpus)
    contract = next(d for d in corpus.documents if d.kind == "api_contract")
    metadata = contract.model_copy(deep=True)
    metadata.id = metadata.source.id = "documentation-endpoint"
    metadata.title = "Describe all request flows"
    metadata.operation_id = "DescribeAllFlows"
    metadata.content = "request contract schema json request flows"
    metadata.payload.update(
        operationId="DescribeAllFlows", method="GET", path="/documentation/flows"
    )
    corpus.documents.append(metadata)
    answer = ask(corpus, "Show the CreateMobilePhone request contract in JSON")
    assert [s.operation_id for s in answer.sections if s.type == "api_contract"] == [
        "createMobilePhone"
    ]
    assert contract.source.id in {s.id for s in answer.sources}


def test_creating_phone_with_noisy_executions_keeps_contract_and_linked_policy(demo):
    corpus = copy.deepcopy(demo.corpus)
    for i in range(30):
        noise = corpus.documents[0].model_copy(deep=True)
        noise.id = noise.source.id = f"noisy-run-{i}"
        noise.title = "Creating a mobile phone validates the request"
        noise.content = "mobile phone validation request contract JSON creating new phone"
        corpus.documents.append(noise)
    answer = ask(
        corpus,
        "How does mobile phone validation work? Give me the JSON contract for "
        "creating a mobile phone.",
    )
    assert [s.type for s in answer.sections] == ["validation", "api_contract"]
    assert answer.sections[1].operation_id == "createMobilePhone"


def test_same_method_operations_need_positive_entity_evidence(demo):
    docs = copy.deepcopy(demo.corpus.documents)
    assert select_contract("Give the request contract to create an invoice", docs) is None
    assert select_contract("Give the JSON contract", docs) is None
