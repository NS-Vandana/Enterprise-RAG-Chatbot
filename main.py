"""
Enterprise RAG Chatbot — FastAPI entry point
"""
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
import structlog
import os

from auth.rbac import rbac_middleware, get_current_user, UserContext
from rag.chain import run_rag_chain
from guardrails.middleware import guardrail_check, guardrail_output
from monitoring.langsmith_config import setup_langsmith
from monitoring.cost_callback import CostTrackingCallback

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_langsmith()
    log.info("RAG Chatbot started", env=os.getenv("APP_ENV", "development"))
    yield
    log.info("RAG Chatbot shutting down")


app = FastAPI(
    title="Enterprise RAG Chatbot",
    description="Internal company chatbot with RBAC, guardrails, and full observability",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RBAC middleware — validates JWT and injects role on every request
app.middleware("http")(rbac_middleware)


# ── Request / Response models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    role: str
    sources: list[dict]
    session_id: str | None = None


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "rag-enterprise-chatbot"}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    log.info("Chat request", user_id=user.user_id, role=user.role, query=body.query[:80])

    # 1. Guardrail: check input (PII + scope)
    safe_query = await guardrail_check(body.query)

    # 2. Cost tracking callback
    cost_cb = CostTrackingCallback(user_id=user.user_id, role=user.role)

    # 3. Run RAG chain with role-filtered retrieval
    result = await run_rag_chain(
        query=safe_query,
        role=user.role,
        allowed_collections=user.allowed_collections,
        callbacks=[cost_cb],
        session_id=body.session_id,
    )

    # 4. Guardrail: scrub output PII
    clean_answer = await guardrail_output(result["answer"])

    log.info("Chat response", user_id=user.user_id, sources=len(result["sources"]))

    return ChatResponse(
        answer=clean_answer,
        role=user.role,
        sources=result["sources"],
        session_id=body.session_id,
    )


@app.get("/me")
async def me(user: UserContext = Depends(get_current_user)):
    """Return current user's role and permissions."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "allowed_collections": user.allowed_collections,
    }


@app.get("/collections")
async def list_collections(user: UserContext = Depends(get_current_user)):
    """List collections the current user can access."""
    return {"collections": user.allowed_collections}
