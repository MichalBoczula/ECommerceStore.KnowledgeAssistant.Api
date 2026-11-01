from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KA_", env_file=".env", extra="ignore")

    corpus_path: Path = Path("examples/demo-corpus.json")
    retrieval_mode: Literal["local", "azure"] = "local"
    generation_mode: Literal["evidence", "azure"] = "evidence"
    search_endpoint: str = ""
    search_index: str = "knowledge-v1"
    search_key: SecretStr | None = None
    openai_endpoint: str = ""
    openai_key: SecretStr | None = None
    embedding_deployment: str = ""
    embedding_dimensions: int = 1536
    chat_deployment: str = ""
    api_key: SecretStr | None = None
    cors_origins: list[str] = []
    max_candidates: int = 12
    livedocs_base_url: str = ""

    def require_azure(self, *, chat: bool = False) -> None:
        if not self.openai_endpoint or not self.embedding_deployment:
            raise ValueError("Configure KA_OPENAI_ENDPOINT and KA_EMBEDDING_DEPLOYMENT")
        if chat and not self.chat_deployment:
            raise ValueError("Configure KA_CHAT_DEPLOYMENT")
