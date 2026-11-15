from .models import Document


def faq_aliases(document: Document) -> dict[str, str]:
    return document.payload.get("faqAliases", {})


def question_key(question: str) -> str:
    return " ".join(question.casefold().split()).rstrip("?.!")
