"""
Guardrail wrappers for input and output.

Input guardrails (pre-LLM):
  1. Reject if user query contains PII (prevent data exfiltration via prompt)
  2. Reject if query is out of scope

Output guardrails (post-LLM):
  3. Scrub any PII that leaked into the LLM answer
"""
from fastapi import HTTPException
import structlog

from guardrails.pii import has_pii, scrub_pii, get_pii_report
from guardrails.scope import is_in_scope

log = structlog.get_logger()


async def guardrail_check(query: str) -> str:
    """
    Validate user query before passing to RAG chain.
    Raises HTTPException if query fails any check.
    Returns the (possibly cleaned) query if safe.
    """
    # 1. Length check
    if len(query.strip()) < 3:
        raise HTTPException(status_code=400, detail="Query too short.")

    if len(query) > 2000:
        raise HTTPException(status_code=400, detail="Query too long (max 2000 chars).")

    # 2. PII in query — reject (prevent prompt injection with personal data)
    if has_pii(query):
        report = get_pii_report(query)
        pii_types = list({r["entity_type"] for r in report})
        log.warning("PII detected in query", pii_types=pii_types)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Your query appears to contain sensitive personal data "
                f"({', '.join(pii_types)}). Please remove personal information and try again."
            ),
        )

    # 3. Scope check
    in_scope = await is_in_scope(query)
    if not in_scope:
        log.info("Out-of-scope query blocked", query=query[:80])
        raise HTTPException(
            status_code=400,
            detail=(
                "This question is outside the scope of the internal company assistant. "
                "I can only answer questions about HR policies, financial data, "
                "marketing information, and internal company operations."
            ),
        )

    return query


async def guardrail_output(answer: str) -> str:
    """
    Post-process LLM answer before returning to user.
    Scrubs any PII that leaked through.
    """
    if has_pii(answer):
        log.warning("PII detected in LLM output — scrubbing")
        answer = scrub_pii(answer)

    return answer
