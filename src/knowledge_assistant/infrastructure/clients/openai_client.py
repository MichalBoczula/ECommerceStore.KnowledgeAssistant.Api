from collections.abc import Callable

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

from ..configuration.settings import Settings


def openai_client(settings: Settings) -> OpenAI:
    key: str | Callable[[], str] = (
        settings.openai_key.get_secret_value()
        if settings.openai_key
        else get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")
    )
    return OpenAI(
        base_url=settings.openai_endpoint.rstrip("/") + "/", api_key=key, timeout=45, max_retries=2
    )
