"""ScholarShip model settings: edit this section; new tasks reload it without a service restart.

Credentials belong only in .env. Selecting a preset never authorizes inference.
"""

# USER CONFIGURATION
RESEARCH_QA_POLICY = {
    "revision": "adaptive-evidence-v1",
    "max_rounds": 2,
    "max_queries": 5,
    "context_bytes": 8000,
}
AUTO_DOCUMENT_PREPARATION = {
    "enabled": True,
    "recognize_figures": True,
    "translate_document": True,
    "max_local_index_requests": 128,  # Bounded local embedding batches; never applies to cloud.
}
LOCAL_DOCUMENT_POLICY = {
    "ocr_language": "eng",  # Installed Tesseract language; add traineddata before changing.
    "ocr_dpi": 220,
    "ocr_timeout_seconds": 90,
    "max_pages": 1000,
    "max_page_pixels": 24000000,
    "min_native_chars": 40,
    "min_ocr_confidence": 20,
    "max_model_tasks": 200,
    "tesseract_command": "tesseract",
}
LOCAL_FIGURE_MODEL = "ollama_chat"
FIGURE_MODEL = "openrouter_vision"  # Independent visual model for new figure tasks; no automatic calls.
INFERENCE_MODE = "auto"  # auto: mixed roles allowed; local/cloud restrict actual model dispatch.
ACTIVE_PRESET = "local"  # mock / local / free_api (ChatAnywhere) / free_gemma
ALLOW_LOCAL_INFERENCE = True
ALLOW_REAL_REQUESTS = False  # Real dispatch also requires project policy, scope and shared-budget admission.
TIMEOUT_SECONDS = 120
CONCURRENCY = 1
MAX_OUTPUT_TOKENS = 2048
MAX_REQUESTS_PER_RUN = 30
# Explicit transport route for this machine. Set {} on hosts with direct access.
# Never put credentials here; cloud TLS still verifies the destination certificate.
LIVE_PROXY_URLS = {"openrouter": "http://127.0.0.1:7897", "mineru_download": "http://127.0.0.1:7897"}

# Provider plugins: an existing protocol can be reused by another approved host.
# Only environment variable NAMES belong here. Key values stay in .env.
# Public DNS verified on 2026-11-14; original HTTPS host and certificate remain verified.
DOWNLOAD_DNS_OVERRIDES = {"cdn-mineru.openxlab.org.cn": "8.222.82.255"}

PROVIDER_DEFINITIONS = {
    "mock": {
        "deployment": "simulated",
        "roles": {r: "synthetic" for r in ("chat", "translation", "embedding", "rerank", "parser")},
        "base_urls": [""],
    },
    "chatanywhere": {
        "deployment": "cloud",
        "roles": {"chat": "openai_chat", "translation": "openai_chat"},
        "base_urls": ["https://api.chatanywhere.tech/v1", "https://api.chatanywhere.org/v1"],
        "key_env": "CHATANYWHERE_API_KEY",
        "model_aliases": {"gpt-4o-mini": ["gpt-4o-mini-2024-07-18"]},
    },
    "openrouter": {
        "deployment": "cloud",
        "roles": {
            "chat": "openai_chat",
            "translation": "openai_chat",
            "embedding": "openai_embedding",
            "rerank": "rerank",
        },
        "base_urls": ["https://openrouter.ai/api/v1"],
        "key_env": "OPENROUTER_API_KEY",
        "free_only": True,
        "chat_options": {
            "reasoning": {"enabled": False},
            "provider": {"allow_fallbacks": False, "require_parameters": True},
        },
        "embedding_input_types": {"document": "search_document", "query": "search_query"},
    },
    "ollama": {
        "deployment": "local",
        "figure_policy": "nodes_then_edges",
        "roles": {"chat": "ollama_chat", "translation": "ollama_chat", "embedding": "ollama_embedding"},
        "base_urls": ["http://127.0.0.1:11434"],
    },
    "local_reranker": {"deployment": "local", "roles": {"rerank": "rerank"}, "base_urls": ["http://127.0.0.1:18083"]},
    "mineru_local": {
        "deployment": "local",
        "roles": {"parser": "mineru_local"},
        "base_urls": ["http://127.0.0.1:18082"],
    },
    "mineru_cloud": {
        "deployment": "cloud",
        "roles": {"parser": "mineru_cloud"},
        "base_urls": ["https://mineru.net"],
        "key_env": "MINERU_API_KEY",
    },
}

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
    "openrouter_gemma": {
        "provider": "openrouter",
        "model": "google/gemma-4-31b-it:free",
        "base_url": "https://openrouter.ai/api/v1",
        "json_mode": True,
    },
    "openrouter_vision": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3.5-super-vl:free",
        "base_url": "https://openrouter.ai/api/v1",
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
        "context_tokens": 2048,
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

MODEL_CAPABILITIES = {
    "mock_chat": ["chat", "translation"],
    "mock_embedding": ["embedding"],
    "mock_rerank": ["rerank"],
    "mock_parser": ["parser"],
    "chatanywhere_chat": ["chat", "translation"],
    "openrouter_gemma": ["chat", "translation"],
    "openrouter_vision": ["chat"],
    "openrouter_embedding": ["embedding"],
    "openrouter_rerank": ["rerank"],
    "ollama_chat": ["chat", "translation"],
    "ollama_embedding": ["embedding"],
    "local_rerank": ["rerank"],
    "mineru_local": ["parser"],
    "mineru_cloud": ["parser"],
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
        "parser": "mineru_cloud",
    },
    "free_api": {
        "chat": "chatanywhere_chat",
        "translation": "chatanywhere_chat",
        "embedding": "openrouter_embedding",
        "rerank": "openrouter_rerank",
        "parser": "mineru_cloud",
    },
    "free_gemma": {
        "chat": "openrouter_gemma",
        "translation": "openrouter_gemma",
        "embedding": "openrouter_embedding",
        "rerank": "openrouter_rerank",
        "parser": "mineru_cloud",
    },
}
# Override individual roles without editing a preset, e.g. {"parser": "mineru_cloud"}.
ROLE_OVERRIDES = {}

# Models eligible for image-input contract; account access is verified separately.
VISION_MODEL_BINDINGS = [
    ("ollama", "qwen3.5:9b"),
    ("chatanywhere", "gpt-4o-mini"),
    ("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
]
