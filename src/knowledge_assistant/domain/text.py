import re


def tokens(text: str) -> set[str]:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = set(re.findall(r"[a-z0-9]+", (text + " " + split).lower()))
    # Keep exact identifiers as well as their words, without changing source identities.
    aliases = {
        "phones": "phone",
        "creating": "create",
        "created": "create",
        "filtering": "filter",
        "filtered": "filter",
        "updating": "update",
        "deleting": "delete",
        "reading": "read",
        "adding": "add",
    }
    words.update(aliases[w] for w in list(words) if w in aliases)
    return words - {
        "the",
        "a",
        "an",
        "and",
        "or",
        "how",
        "does",
        "for",
        "me",
        "give",
        "to",
        "of",
        "in",
        "works",
        "work",
        "tell",
        "please",
        "is",
        "it",
        "this",
        "show",
        "what",
        "explain",
        "about",
        "on",
        "with",
        "at",
        "be",
        "are",
        "do",
        "can",
        "my",
        "which",
        "when",
        "then",
        "its",
        "their",
        "say",
        "apply",
    }


FORMAT_TERMS = {
    "request",
    "requests",
    "contract",
    "contracts",
    "json",
    "schema",
    "payload",
    "validation",
    "validate",
    "validations",
    "rules",
    "constraints",
}
