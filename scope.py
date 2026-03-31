"""
Out-of-scope query classifier.
Uses a small fast LLM (Llama 8B on Groq) to classify
whether a query is within the chatbot's allowed domain.
"""
from langchain_groq import ChatGroq
from langchain_openai import AzureChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
import os
import structlog

from rag.prompts import SCOPE_CLASSIFIER_PROMPT

log = structlog.get_logger()


def get_classifier_llm():
    """Use smallest/cheapest model for the scope check."""
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        return ChatGroq(
            api_key=groq_key,
            model="llama-3.1-8b-instant",  # fast + cheap
            temperature=0,
            max_tokens=10,
        )
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        temperature=0,
        max_tokens=10,
    )


_classifier = None


def classifier():
    global _classifier
    if _classifier is None:
        _classifier = get_classifier_llm()
    return _classifier


prompt = ChatPromptTemplate.from_template(SCOPE_CLASSIFIER_PROMPT)


async def is_in_scope(query: str) -> bool:
    """Return True if query is within chatbot's allowed domain."""
    try:
        chain = prompt | classifier() | StrOutputParser()
        result = await chain.ainvoke({"query": query})
        verdict = result.strip().upper()
        log.info("Scope check", query=query[:60], verdict=verdict)
        return verdict == "ALLOWED"
    except Exception as e:
        log.error("Scope classifier error", error=str(e))
        # Fail open — don't block if classifier errors
        return True
