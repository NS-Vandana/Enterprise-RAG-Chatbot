"""
Document ingestion pipeline.
Parses PDFs, Word, Excel, etc. via Docling,
splits into chunks, and loads into role-namespaced Qdrant collections.

Usage:
    python -m ingestion.ingest --file reports/Q3.pdf --roles finance c_suite --dept finance --type financial_report
    python -m ingestion.ingest --dir ./sample_docs   # batch ingest with auto-detection
"""
import argparse
import json
import os
from pathlib import Path
import structlog

from docling.document_converter import DocumentConverter
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document

from rag.retriever import qdrant_client, embeddings, ensure_collections
from ingestion.schema import DocumentMeta, ROLE_COLLECTION_MAP, AUTO_DETECT_RULES

log = structlog.get_logger()

# ── Splitter config ────────────────────────────────────────────────────────

SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=120,
    separators=["\n\n", "\n", ". ", " ", ""],
)

# ── Converter ─────────────────────────────────────────────────────────────

converter = DocumentConverter()


# ── Core ingest function ───────────────────────────────────────────────────

def ingest_document(
    file_path: str | Path,
    role_access: list[str],
    department: str,
    doc_type: str,
    extra_metadata: dict | None = None,
) -> int:
    """
    Parse a document, split into chunks, embed, and store in Qdrant.
    Returns number of chunks ingested.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    log.info("Ingesting document", file=str(file_path), roles=role_access, dept=department)

    # 1. Parse with Docling
    result = converter.convert(str(file_path))
    markdown_text = result.document.export_to_markdown()

    if not markdown_text.strip():
        log.warning("Empty document after parsing", file=str(file_path))
        return 0

    # 2. Build metadata
    metadata = {
        "source": file_path.name,
        "source_path": str(file_path),
        "role_access": role_access,
        "department": department,
        "doc_type": doc_type,
        **(extra_metadata or {}),
    }

    # 3. Split into chunks
    chunks = SPLITTER.create_documents(
        texts=[markdown_text],
        metadatas=[metadata],
    )
    log.info("Chunked document", file=file_path.name, chunks=len(chunks))

    # 4. Determine target collections
    target_collections = list({
        ROLE_COLLECTION_MAP.get(role, "all_docs")
        for role in role_access
    })

    # 5. Store in each permitted collection
    client = qdrant_client()
    emb    = embeddings()

    for collection in target_collections:
        QdrantVectorStore.from_documents(
            documents=chunks,
            embedding=emb,
            collection_name=collection,
            client=client,
        )
        log.info("Stored chunks", collection=collection, count=len(chunks))

    return len(chunks)


def auto_detect_metadata(file_path: Path) -> dict:
    """Guess role_access, department, doc_type from filename patterns."""
    name_lower = file_path.name.lower()
    for rule in AUTO_DETECT_RULES:
        if any(kw in name_lower for kw in rule["keywords"]):
            return {
                "role_access": rule["role_access"],
                "department": rule["department"],
                "doc_type": rule["doc_type"],
            }
    # Default: all roles
    return {
        "role_access": ["hr", "finance", "marketing", "c_suite"],
        "department": "general",
        "doc_type": "general_document",
    }


def ingest_directory(dir_path: str | Path, dry_run: bool = False) -> dict:
    """Batch ingest all supported files in a directory."""
    dir_path = Path(dir_path)
    supported = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
    files = [f for f in dir_path.rglob("*") if f.suffix.lower() in supported]

    log.info("Batch ingestion", directory=str(dir_path), file_count=len(files))

    results = {"success": [], "failed": [], "total_chunks": 0}

    for file_path in files:
        meta = auto_detect_metadata(file_path)
        if dry_run:
            log.info("[DRY RUN] Would ingest", file=file_path.name, **meta)
            continue
        try:
            n = ingest_document(file_path, **meta)
            results["success"].append(str(file_path))
            results["total_chunks"] += n
        except Exception as e:
            log.error("Failed to ingest", file=str(file_path), error=str(e))
            results["failed"].append({"file": str(file_path), "error": str(e)})

    return results


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Ingest documents into RAG system")
    parser.add_argument("--file", help="Single file to ingest")
    parser.add_argument("--dir",  help="Directory to batch ingest")
    parser.add_argument("--roles", nargs="+", default=["c_suite"],
                        help="Roles that can access this document")
    parser.add_argument("--dept", default="general", help="Department")
    parser.add_argument("--type", default="document", dest="doc_type", help="Document type")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_collections()

    if args.file:
        n = ingest_document(args.file, args.roles, args.dept, args.doc_type)
        print(f"Ingested {n} chunks from {args.file}")

    elif args.dir:
        results = ingest_directory(args.dir, dry_run=args.dry_run)
        print(json.dumps(results, indent=2))

    else:
        parser.print_help()
