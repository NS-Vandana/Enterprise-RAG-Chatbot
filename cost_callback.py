"""
LangChain callback that tracks token usage and estimated USD cost.
Pushes metrics to Azure Monitor via OpenTelemetry.
Also logs locally for development.
"""
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
import datetime
import os
import structlog

log = structlog.get_logger()

# Pricing per 1K tokens (update as needed)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "llama-3.1-70b-versatile": {"input": 0.00059, "output": 0.00079},
    "llama-3.1-8b-instant":    {"input": 0.00005, "output": 0.00008},
    "gpt-4o":                  {"input": 0.005,   "output": 0.015},
    "gpt-4o-mini":             {"input": 0.00015, "output": 0.0006},
    "default":                 {"input": 0.001,   "output": 0.002},
}

# ── Azure Monitor setup ────────────────────────────────────────────────────

_meter = None
_token_counter = None
_cost_counter = None


def _setup_azure_monitor():
    global _meter, _token_counter, _cost_counter
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn_str:
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry import metrics

        configure_azure_monitor(connection_string=conn_str)
        _meter         = metrics.get_meter("rag-enterprise-chatbot")
        _token_counter = _meter.create_counter("llm_tokens_total",    unit="tokens",    description="Total LLM tokens used")
        _cost_counter  = _meter.create_counter("llm_cost_usd_cents",  unit="usd_cents", description="Estimated LLM cost in US cents")
        log.info("Azure Monitor metrics configured")
    except Exception as e:
        log.warning("Azure Monitor setup failed", error=str(e))


_setup_azure_monitor()


# ── Callback ───────────────────────────────────────────────────────────────

class CostTrackingCallback(BaseCallbackHandler):
    """
    Tracks token usage and cost per LLM call.
    Attach to any LangChain chain: chain.with_config({"callbacks": [cb]})
    """

    def __init__(self, user_id: str = "unknown", role: str = "unknown"):
        self.user_id   = user_id
        self.role      = role
        self._start_ts: datetime.datetime | None = None

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._start_ts = datetime.datetime.utcnow()

    def on_llm_end(self, response: LLMResult, **kwargs):
        usage = response.llm_output or {}
        token_usage = usage.get("token_usage", usage.get("usage", {}))

        in_tok  = token_usage.get("prompt_tokens",     token_usage.get("input_tokens", 0))
        out_tok = token_usage.get("completion_tokens", token_usage.get("output_tokens", 0))
        total   = in_tok + out_tok

        # Detect model name
        model = usage.get("model_name", usage.get("model", "default"))
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        cost_usd = (in_tok / 1000 * pricing["input"]) + (out_tok / 1000 * pricing["output"])

        latency_ms = None
        if self._start_ts:
            latency_ms = (datetime.datetime.utcnow() - self._start_ts).total_seconds() * 1000

        # Structured log (always)
        log.info(
            "LLM call complete",
            user_id=self.user_id,
            role=self.role,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total,
            cost_usd=round(cost_usd, 6),
            latency_ms=round(latency_ms, 1) if latency_ms else None,
            date=datetime.date.today().isoformat(),
        )

        # Azure Monitor metrics
        if _token_counter and _cost_counter:
            attrs = {
                "user_id": self.user_id,
                "role": self.role,
                "model": model,
                "date": datetime.date.today().isoformat(),
            }
            try:
                _token_counter.add(total, attrs)
                _cost_counter.add(int(cost_usd * 100), attrs)  # store as integer cents
            except Exception as e:
                log.warning("Failed to push Azure Monitor metrics", error=str(e))

    def on_llm_error(self, error, **kwargs):
        log.error("LLM error", user_id=self.user_id, role=self.role, error=str(error))
