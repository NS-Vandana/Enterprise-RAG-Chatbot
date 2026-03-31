"""
LangChain RAG chain.
Supports both Groq (Llama) and Azure OpenAI as LLM backends.
Multi-turn conversation with session memory.
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.messages import HumanMessage, AIMessage
from langchain_groq import ChatGroq
from langchain_openai import AzureChatOpenAI
import os
import structlog

from rag.retriever import retrieve, format_docs, docs_to_source_list
from rag.prompts import SYSTEM_PROMPT, CONDENSE_QUESTION_PROMPT

log = structlog.get_logger()

# ── Conversation memory (in-memory; use Redis for production) ──────────────

_session_history: dict[str, list] = {}

MAX_HISTORY_TURNS = 6  # keep last 6 turns (12 messages)


def get_history(session_id: str | None) -> list:
    if not session_id:
        return []
    return _session_history.get(session_id, [])


def save_history(session_id: str | None, human: str, ai: str):
    if not session_id:
        return
    history = _session_history.setdefault(session_id, [])
    history.append(HumanMessage(content=human))
    history.append(AIMessage(content=ai))
    # Trim to max turns
    if len(history) > MAX_HISTORY_TURNS * 2:
        _session_history[session_id] = history[-(MAX_HISTORY_TURNS * 2):]


# ── LLM factory ───────────────────────────────────────────────────────────

def get_llm():
    """Return Groq (Llama) or Azure OpenAI depending on config."""
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        log.info("Using Groq LLM", model="llama-3.1-70b-versatile")
        return ChatGroq(
            api_key=groq_key,
            model="llama-3.1-70b-versatile",
            temperature=0.1,
            max_tokens=1024,
        )

    log.info("Using Azure OpenAI LLM", deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"))
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        temperature=0.1,
        max_tokens=1024,
    )


_llm = None


def llm():
    global _llm
    if _llm is None:
        _llm = get_llm()
    return _llm


# ── Condense question (for multi-turn) ────────────────────────────────────

async def condense_question(query: str, history: list) -> str:
    """If there's history, rewrite the question to be standalone."""
    if not history:
        return query

    condense_prompt = ChatPromptTemplate.from_template(CONDENSE_QUESTION_PROMPT)
    history_str = "\n".join(
        f"{'Human' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}"
        for m in history[-4:]
    )

    chain = condense_prompt | llm() | StrOutputParser()
    return await chain.ainvoke({"chat_history": history_str, "question": query})


# ── Main RAG chain ─────────────────────────────────────────────────────────

async def run_rag_chain(
    query: str,
    role: str,
    allowed_collections: list[str],
    callbacks: list | None = None,
    session_id: str | None = None,
) -> dict:
    """
    Full RAG pipeline:
    1. Condense question (if multi-turn)
    2. Retrieve from role-permitted Qdrant collections
    3. Format context
    4. Generate answer with LLM
    5. Save to session history
    """
    history = get_history(session_id)

    # 1. Condense if multi-turn
    standalone_query = await condense_question(query, history)
    log.info("Running RAG", role=role, collections=allowed_collections,
             condensed=standalone_query != query)

    # 2. Retrieve
    docs = await retrieve(standalone_query, allowed_collections, k=5)
    log.info("Retrieved documents", count=len(docs))

    if not docs:
        answer = (
            "I couldn't find any relevant information in the documents you have access to. "
            "Please check with your administrator if you need access to additional data."
        )
        return {"answer": answer, "sources": [], "docs": []}

    # 3. Format context
    context = format_docs(docs)
    departments = list({d.metadata.get("department", "unknown") for d in docs})

    # 4. Build prompt and generate
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{question}"),
    ])

    current_llm = llm()
    if callbacks:
        current_llm = current_llm.with_config({"callbacks": callbacks})

    chain = prompt | current_llm | StrOutputParser()

    answer = await chain.ainvoke({
        "role": role,
        "departments": ", ".join(departments),
        "context": context,
        "history": history,
        "question": standalone_query,
    })

    # 5. Save history
    save_history(session_id, query, answer)

    return {
        "answer": answer,
        "sources": docs_to_source_list(docs),
        "docs": docs,  # raw docs for eval harness
    }
