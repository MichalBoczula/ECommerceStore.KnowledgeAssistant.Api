from .models import Document


def faq_aliases(document: Document) -> dict[str, str]:
    return document.payload.get("faqAliases", {})


def faq_allure_examples(document: Document) -> dict[str, list[dict]]:
    return document.payload.get("faqAllureExamples", {})


def question_key(question: str) -> str:
    return " ".join(question.casefold().split()).rstrip("?.!")


def exact_faq_examples(document: Document, normalized_question: str) -> list[dict]:
    entries = faq_allure_examples(document)
    for faq_id, alias in faq_aliases(document).items():
        if question_key(alias) == normalized_question:
            return entries.get(faq_id, [])
    return []


def operation_example(document: Document) -> list[dict]:
    for references in faq_allure_examples(document).values():
        for example in references:
            if example["coverage"] == "direct" and example["executionDocumentId"]:
                return [{**example, "coverage": "related"}]
    return []
