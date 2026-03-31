"""
Unit tests for guardrails, RBAC, and chain logic.
Run with: pytest tests/ -v
"""
import pytest
import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")


# ── PII detection tests ────────────────────────────────────────────────────

class TestPIIDetection:

    def test_detects_email(self):
        from guardrails.pii import has_pii
        assert has_pii("Contact john.doe@company.com for details")

    def test_detects_phone(self):
        from guardrails.pii import has_pii
        assert has_pii("Call me at +91 98765 43210")

    def test_clean_text_passes(self):
        from guardrails.pii import has_pii
        assert not has_pii("What is the parental leave policy?")

    def test_scrub_replaces_email(self):
        from guardrails.pii import scrub_pii
        result = scrub_pii("Send to alice@corp.com")
        assert "alice@corp.com" not in result
        assert "<EMAIL_ADDRESS>" in result

    def test_scrub_clean_text_unchanged(self):
        from guardrails.pii import scrub_pii
        text = "What is the Q3 revenue?"
        assert scrub_pii(text) == text


# ── RBAC namespace mapping tests ───────────────────────────────────────────

class TestRBAC:

    def test_hr_gets_hr_collection(self):
        from auth.rbac import ROLE_NAMESPACES
        assert ROLE_NAMESPACES["hr"] == ["hr_docs"]

    def test_finance_gets_finance_and_marketing(self):
        from auth.rbac import ROLE_NAMESPACES
        cols = ROLE_NAMESPACES["finance"]
        assert "finance_docs" in cols
        assert "marketing_docs" in cols

    def test_c_suite_gets_all(self):
        from auth.rbac import ROLE_NAMESPACES
        cols = ROLE_NAMESPACES["c_suite"]
        assert len(cols) == 4
        assert "hr_docs" in cols

    def test_dev_token_hr(self):
        from auth.rbac import DEV_ROLE_TOKENS, ROLE_NAMESPACES
        role = DEV_ROLE_TOKENS["dev-hr-token"]
        assert role == "hr"
        assert "hr_docs" in ROLE_NAMESPACES[role]

    def test_marketing_cannot_access_hr(self):
        from auth.rbac import ROLE_NAMESPACES
        assert "hr_docs" not in ROLE_NAMESPACES["marketing"]

    def test_marketing_cannot_access_finance(self):
        from auth.rbac import ROLE_NAMESPACES
        assert "finance_docs" not in ROLE_NAMESPACES["marketing"]


# ── Guardrail middleware tests ─────────────────────────────────────────────

class TestGuardrailCheck:

    @pytest.mark.asyncio
    async def test_short_query_rejected(self):
        from guardrails.middleware import guardrail_check
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await guardrail_check("hi")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_too_long_query_rejected(self):
        from guardrails.middleware import guardrail_check
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await guardrail_check("x" * 2001)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_pii_query_rejected(self):
        from guardrails.middleware import guardrail_check
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await guardrail_check("What is the salary of john.doe@company.com?")
        assert exc.value.status_code == 400
        assert "sensitive" in exc.value.detail.lower() or "PII" in exc.value.detail


# ── Ingestion schema tests ─────────────────────────────────────────────────

class TestIngestionSchema:

    def test_auto_detect_hr_doc(self):
        from pathlib import Path
        from ingestion.ingest import auto_detect_metadata
        meta = auto_detect_metadata(Path("payroll_2024.xlsx"))
        assert "hr" in meta["role_access"]
        assert meta["department"] == "hr"

    def test_auto_detect_finance_doc(self):
        from pathlib import Path
        from ingestion.ingest import auto_detect_metadata
        meta = auto_detect_metadata(Path("Q3_financial_report.pdf"))
        assert "finance" in meta["role_access"]
        assert meta["department"] == "finance"

    def test_auto_detect_marketing_doc(self):
        from pathlib import Path
        from ingestion.ingest import auto_detect_metadata
        meta = auto_detect_metadata(Path("campaign_performance_q3.docx"))
        assert "marketing" in meta["role_access"]

    def test_unknown_doc_gets_all_roles(self):
        from pathlib import Path
        from ingestion.ingest import auto_detect_metadata
        meta = auto_detect_metadata(Path("random_file.pdf"))
        assert "c_suite" in meta["role_access"]


# ── Cost callback tests ────────────────────────────────────────────────────

class TestCostCallback:

    def test_callback_instantiates(self):
        from monitoring.cost_callback import CostTrackingCallback
        cb = CostTrackingCallback(user_id="test-user", role="finance")
        assert cb.user_id == "test-user"
        assert cb.role == "finance"

    def test_model_pricing_exists(self):
        from monitoring.cost_callback import MODEL_PRICING
        assert "llama-3.1-70b-versatile" in MODEL_PRICING
        assert "gpt-4o" in MODEL_PRICING
        for model, pricing in MODEL_PRICING.items():
            assert "input" in pricing
            assert "output" in pricing
