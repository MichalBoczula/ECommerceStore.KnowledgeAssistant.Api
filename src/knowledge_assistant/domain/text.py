import re


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - {
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
    }
