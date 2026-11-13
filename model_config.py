"""ScholarShip model settings: edit this section, then restart project services.

Credentials belong only in .env. Selecting a preset never authorizes inference.
"""

# USER CONFIGURATION
ACTIVE_PRESET = "free_api"  # mock / local / free_api
ALLOW_LOCAL_INFERENCE = False
ALLOW_REAL_REQUESTS = False  # Future opt-in; real dispatch also requires a separately reviewed executor.
TIMEOUT_SECONDS = 120
CONCURRENCY = 1
MAX_OUTPUT_TOKENS = 2048
MAX_REQUESTS_PER_RUN = 30

MODELS = {
    "mock_chat": {"provider": "mock", "model": "synthetic-answer-v1"},
    "mock_embedding": {"provider": "mock", "model": "hash-bow-sha256-v1", "dimensions": 1024},
    "mock_rerank": {"provider": "mock", "model": "synthetic-overlap-v1"},
    "mock_parser": {"provider": "mock", "model": "scholarship.synthetic-layout/v1"},
    "chatanywhere_chat": {
        "provider": "chatanywhere",
        "model": "gpt-4o-mini",
        "base_url": "https://api.chatanywhere.tech/v1",
        "json_mode": True,
    },
    "openrouter_embedding": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3.5-embed-8b:free",
        "base_url": "https://openrouter.ai/api/v1",
        "dimensions": None,
    },
    "openrouter_rerank": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3.5-rerank-8b:free",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama_chat": {
        "provider": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://127.0.0.1:11434",
        "json_mode": True,
        "context_tokens": 8192,
    },
    "ollama_embedding": {
        "provider": "ollama",
        "model": "qwen3-embedding:0.6b",
        "base_url": "http://127.0.0.1:11434",
        "dimensions": 1024,
    },
    "local_rerank": {
        "provider": "local_reranker",
        "model": "Qwen/Qwen3-Reranker-0.6B",
        "base_url": "http://127.0.0.1:18083",
    },
    "mineru_local": {
        "provider": "mineru_local",
        "model": "mineru-4.1-basic",
        "base_url": "http://127.0.0.1:18082",
        "tier": "basic",
    },
    "mineru_cloud": {"provider": "mineru_cloud", "model": "vlm", "base_url": "https://mineru.net"},
}

PRESETS = {
    "mock": {
        "chat": "mock_chat",
        "translation": "mock_chat",
        "embedding": "mock_embedding",
        "rerank": "mock_rerank",
        "parser": "mock_parser",
    },
    "local": {
        "chat": "ollama_chat",
        "translation": "ollama_chat",
        "embedding": "ollama_embedding",
        "rerank": "local_rerank",
        "parser": "mineru_local",
    },
    "free_api": {
        "chat": "chatanywhere_chat",
        "translation": "chatanywhere_chat",
        "embedding": "openrouter_embedding",
        "rerank": "openrouter_rerank",
        "parser": "mineru_local",
    },
}
# Override individual roles without editing a preset, e.g. {"parser": "mineru_cloud"}.
ROLE_OVERRIDES = {}
