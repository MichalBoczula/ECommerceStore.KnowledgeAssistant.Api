from .text import tokens

METHOD_ACTIONS = {
    "GET": ("get", "read", "fetch", "retrieve"),
    "HEAD": ("head", "headers"),
    "OPTIONS": ("options",),
    "POST": ("post",),
    "PUT": ("put", "update", "replace"),
    "PATCH": ("patch", "update", "edit", "change"),
    "DELETE": ("delete", "remove"),
}

DESCRIBED_ACTIONS = {
    "GET": (("list", "browse"), ("download",)),
    "POST": (("create", "register", "add"), ("search", "filter", "query")),
}


def operation_actions(method: str, operation_id: str, summary: str) -> tuple[str, ...]:
    method = method.upper()
    words = tokens(f"{operation_id} {summary}")
    return METHOD_ACTIONS.get(method, (method.casefold(),)) + tuple(
        action
        for group in DESCRIBED_ACTIONS.get(method, ())
        if words.intersection(group)
        for action in group
    )
