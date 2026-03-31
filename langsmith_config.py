"""
LangSmith observability setup.
Configures tracing so every RAG call is captured with:
- full prompt + context sent to LLM
- LLM response
- retrieved documents
- latency at each step
"""
import os
import structlog

log = structlog.get_logger()


def setup_langsmith():
    """Configure LangSmith tracing via environment variables."""
    api_key = os.getenv("LANGCHAIN_API_KEY")
    project = os.getenv("LANGCHAIN_PROJECT", "rag-enterprise-chatbot")
    enabled = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"

    if not enabled or not api_key:
        log.info("LangSmith tracing disabled (set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY)")
        return

    # LangChain reads these env vars automatically
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"]     = project
    os.environ["LANGCHAIN_API_KEY"]     = api_key

    log.info("LangSmith tracing enabled", project=project)
