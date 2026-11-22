"""Loopback-only optional local reranker; loaded weights must already be cached."""

from functools import lru_cache
from pathlib import Path
import argparse
import runpy
from threading import Lock

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parents[1]
MODEL = "Qwen/Qwen3-Reranker-0.6B"
PORT = 18083
DEVICE = "auto"  # Prefer Apple MPS, otherwise CPU; never uses a remote provider.
BATCH_SIZE = 2
CPU_THREADS = 4
LOCK = Lock()
app = FastAPI(title="ScholarShip local reranker")


@lru_cache
def model():
    from sentence_transformers import CrossEncoder

    import torch

    torch.set_num_threads(CPU_THREADS)
    device = ("mps" if torch.backends.mps.is_available() else "cpu") if DEVICE == "auto" else DEVICE
    options = {"dtype": torch.float16} if device == "mps" else {}
    weights = ROOT / ".tools/model-cache/reranker"
    return CrossEncoder(str(weights), device=device, local_files_only=True, model_kwargs=options)


class Rerank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    query: str = Field(min_length=1, max_length=4096)
    documents: list[str] = Field(min_length=1, max_length=20)
    top_n: int = Field(ge=1, le=20)


@app.get("/health")
def health():
    weights = ROOT / ".tools/model-cache/reranker"
    present = all(
        (weights / name).is_file() for name in ("verified.json", "model.safetensors", "config.json", "tokenizer.json")
    )
    return {
        "configured": True,
        "model": MODEL,
        "weights_present": present,
        "weights_loaded": model.cache_info().currsize > 0,
        "device_policy": DEVICE,
        "batch_size": BATCH_SIZE,
        "inference_verified": False,
    }


@app.post("/rerank")
def rerank(body: Rerank):
    if not runpy.run_path(str(ROOT / "model_config.py"))["ALLOW_LOCAL_INFERENCE"]:
        raise HTTPException(403, "local_inference_disabled")
    if (
        body.model != MODEL
        or body.top_n > len(body.documents)
        or any(not t or len(t.encode()) > 4000 for t in body.documents)
    ):
        raise HTTPException(422, "invalid_rerank_input")
    with LOCK:
        try:
            encoder = model()
            usage = {"prompt_tokens": 0, "completion_tokens": 0}

            def count_tokens(module, args, kwargs):
                mask = kwargs.get("attention_mask")
                if mask is None:
                    raise ValueError("rerank_token_usage_unavailable")
                usage["prompt_tokens"] += int(mask.sum().item())

            hook = encoder.model.register_forward_pre_hook(count_tokens, with_kwargs=True)
            try:
                scores = encoder.predict([(body.query, t) for t in body.documents], batch_size=BATCH_SIZE).tolist()
            finally:
                hook.remove()
            usage["total_tokens"] = usage["prompt_tokens"]
        except Exception:
            raise HTTPException(503, "local_reranker_unavailable") from None
    rows = sorted(
        [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)],
        key=lambda r: r["relevance_score"],
        reverse=True,
    )
    return {"model": MODEL, "results": rows[: body.top_n], "usage": usage}


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port, workers=1, log_level="warning")
