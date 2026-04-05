from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from mem0 import AsyncMemory


logger = logging.getLogger("hermes-companion-memory")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


DATA_DIR = Path(os.environ.get("HERMES_MEMORY_DATA_DIR", "~/.hermes/companion-memory")).expanduser()
VECTOR_DIR = Path(os.environ.get("HERMES_MEMORY_VECTOR_DIR", str(DATA_DIR / "chroma"))).expanduser()
HISTORY_DB_PATH = Path(
    os.environ.get("HERMES_MEMORY_HISTORY_DB_PATH", str(DATA_DIR / "history.db"))
).expanduser()
COLLECTION_NAME = os.environ.get("HERMES_MEMORY_COLLECTION_NAME", "central-hermes").strip() or "central-hermes"
API_KEY = os.environ.get("HERMES_MEMORY_API_KEY", "").strip()
SEARCH_LIMIT_MAX = max(1, int(os.environ.get("HERMES_MEMORY_SEARCH_LIMIT_MAX", "10")))
WRITE_INFER = os.environ.get("HERMES_MEMORY_INFER", "0").strip() == "1"

LLM_MODEL = os.environ.get("HERMES_MEMORY_LLM_MODEL", "gpt-5").strip() or "gpt-5"
LLM_API_KEY = os.environ.get("HERMES_MEMORY_LLM_API_KEY", "").strip() or "unused-memory-key"
LLM_BASE_URL = os.environ.get("HERMES_MEMORY_LLM_BASE_URL", "http://127.0.0.1:8788/v1").strip()
EMBEDDER_PROVIDER = os.environ.get("HERMES_MEMORY_EMBEDDER_PROVIDER", "fastembed").strip() or "fastembed"
EMBEDDER_MODEL = os.environ.get("HERMES_MEMORY_EMBEDDER_MODEL", "").strip()

for path in (DATA_DIR, VECTOR_DIR):
    path.mkdir(parents=True, exist_ok=True)
if not HISTORY_DB_PATH.parent.exists():
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class SearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=SEARCH_LIMIT_MAX)
    filters: Optional[Dict[str, Any]] = None
    threshold: Optional[float] = None
    metadata_filters: Optional[Dict[str, Any]] = None
    rerank: bool = True


class AddMemoryRequest(BaseModel):
    messages: Any
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    infer: Optional[bool] = None
    memory_type: Optional[str] = None
    prompt: Optional[str] = None


for model in (SearchRequest, AddMemoryRequest):
    model.model_rebuild()


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key.")


def memory_config() -> Dict[str, Any]:
    embedder_config: Dict[str, Any] = {}
    embedder_model = EMBEDDER_MODEL
    if not embedder_model and EMBEDDER_PROVIDER == "fastembed":
        embedder_model = "BAAI/bge-small-en-v1.5"
    if embedder_model:
        embedder_config["model"] = embedder_model

    return {
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": COLLECTION_NAME,
                "path": str(VECTOR_DIR),
            },
        },
        "embedder": {
            "provider": EMBEDDER_PROVIDER,
            "config": embedder_config,
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": LLM_MODEL,
                "api_key": LLM_API_KEY,
                "openai_base_url": LLM_BASE_URL,
            },
        },
        "history_db_path": str(HISTORY_DB_PATH),
    }


@lru_cache(maxsize=1)
def get_memory() -> AsyncMemory:
    logger.info(
        "Starting memory backend with collection=%s embedder=%s llm_model=%s infer=%s",
        COLLECTION_NAME,
        EMBEDDER_PROVIDER,
        LLM_MODEL,
        WRITE_INFER,
    )
    return AsyncMemory.from_config(memory_config())


app = FastAPI(title="Hermes Companion Memory Service", version="0.1.0")


@app.get("/")
async def root() -> Dict[str, Any]:
    get_memory()
    return {
        "status": "ok",
        "collection": COLLECTION_NAME,
        "embedderProvider": EMBEDDER_PROVIDER,
        "writeInfer": WRITE_INFER,
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    return await root()


@app.post("/search", dependencies=[Depends(require_api_key)])
async def search(request: SearchRequest) -> Dict[str, Any]:
    memory = get_memory()
    result = await memory.search(
        request.query,
        user_id=request.user_id,
        agent_id=request.agent_id,
        run_id=request.run_id,
        limit=request.limit,
        filters=request.filters,
        threshold=request.threshold,
        metadata_filters=request.metadata_filters,
        rerank=request.rerank,
    )
    return result if isinstance(result, dict) else {"results": result}


@app.post("/memories", dependencies=[Depends(require_api_key)])
async def add_memory(request: AddMemoryRequest) -> Dict[str, Any]:
    memory = get_memory()
    result = await memory.add(
        request.messages,
        user_id=request.user_id,
        agent_id=request.agent_id,
        run_id=request.run_id,
        metadata=request.metadata,
        infer=WRITE_INFER if request.infer is None else request.infer,
        memory_type=request.memory_type,
        prompt=request.prompt,
    )
    return result if isinstance(result, dict) else {"results": result}
