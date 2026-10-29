"""Central configuration. Everything can be overridden with environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Models (Ollama runs on your PC, reachable over Tailscale) ---
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "gemma4:e2b"
    embed_model: str = "embeddinggemma"
    embed_dim: int = 768
    # embeddinggemma uses task prefixes; for nomic-embed-text use
    # "search_query: " / "search_document: ". For bge-m3 leave them empty.
    embed_query_prefix: str = "task: search result | query: "
    embed_doc_prefix: str = "title: none | text: "
    num_ctx: int = 8192
    temperature: float = 0.2
    llm_timeout: float = 180.0

    # --- Database ---
    database_url: str = "postgresql://support:support@localhost:5432/support"

    # --- RAG ---
    docs_dir: str = "data/docs"
    auto_ingest: bool = True
    top_k: int = 4
    # Cosine similarity threshold. Calibrate it with the evaluation (eval/run_eval.py).
    min_score: float = 0.45
    chunk_chars: int = 900
    chunk_overlap: int = 120

    # --- Agent ---
    max_steps: int = 5
    max_output_retries: int = 1
    max_input_chars: int = 800
    max_history: int = 8

    # --- Security / limits ---
    # If set, the backend requires this value in the X-Internal-Key header
    # (the frontend sends it; the backend is not exposed to the internet).
    internal_api_key: str = ""
    confirm_secret: str = "change-this-in-production"
    confirm_ttl_seconds: int = 600
    rate_limit_per_min: int = 12
    max_concurrent_llm: int = 2
    queue_timeout: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
