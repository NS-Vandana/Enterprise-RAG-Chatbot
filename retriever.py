"""
Multi-collection Qdrant retriever.
Merges results across all role-permitted collections,
re-ranks by score, and returns top-k unique chunks.
"""
from langchain_qdrant import QdrantVectorStore
from langchain_openai import AzureOpenAIEmbeddings
from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
import os
import structlog

log = structlog.get_logger()

# ── Clients (module-level singletons) ─────────────────────────────────────

def get_qdrant_client() -> QdrantClient:
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=30,
    )


def get_embeddings() -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_deployment=os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-large"),
    )


_qdrant_client: QdrantClient | None = None
_embeddings: AzureOpenAIEmbeddings | None = None


def qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = get_qdrant_client()
    return _qdrant_client


def embeddings() -> AzureOpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = get_embeddings()
    return _embeddings


# ── Collection management ──────────────────────────────────────────────────

VECTOR_SIZE = 3072  # text-embedding-3-large output dimension

ALL_COLLECTIONS = ["hr_docs", "finance_docs", "marketing_docs", "all_docs"]


def ensure_collections():
    """Create Qdrant collections if they don't exist."""
    client = qdrant_client()
    existing = {c.name for c in client.get_collections().collections}

    for name in ALL_COLLECTIONS:
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            log.info("Created Qdrant collection", collection=name)


# ── Retrieval ──────────────────────────────────────────────────────────────

async def retrieve(
    query: str,
    allowed_collections: list[str],
    k: int = 5,
) -> list[Document]:
    """
    Search across all permitted collections, merge results,
    deduplicate by content hash, sort by score, return top-k.
    """
    client = qdrant_client()
    emb    = embeddings()

    all_docs: list[tuple[float, Document]] = []

    for collection in allowed_collections:
        try:
            store = QdrantVectorStore(
                client=client,
                collection_name=collection,
                embedding=emb,
            )
            results = store.similarity_search_with_score(query, k=k)
            for doc, score in results:
                all_docs.append((score, doc))
        except Exception as e:
            log.warning("Collection search failed", collection=collection, error=str(e))

    # Deduplicate by content
    seen: set[str] = set()
    unique_docs: list[tuple[float, Document]] = []
    for score, doc in all_docs:
        key = doc.page_content[:200]
        if key not in seen:
            seen.add(key)
            unique_docs.append((score, doc))

    # Sort by score descending, return top-k
    unique_docs.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in unique_docs[:k]]


def format_docs(docs: list[Document]) -> str:
    """Format retrieved docs with source attribution."""
    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        source = meta.get("source", "unknown")
        dept   = meta.get("department", "unknown")
        dtype  = meta.get("doc_type", "document")
        parts.append(
            f"[{i}] Source: {source} | Department: {dept} | Type: {dtype}\n{doc.page_content}"
        )
    return "\n\n---\n\n".join(parts)


def docs_to_source_list(docs: list[Document]) -> list[dict]:
    """Convert docs to serializable source references for the API response."""
    return [
        {
            "source": doc.metadata.get("source", "unknown"),
            "department": doc.metadata.get("department", "unknown"),
            "doc_type": doc.metadata.get("doc_type", "document"),
            "excerpt": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
        }
        for doc in docs
    ]
