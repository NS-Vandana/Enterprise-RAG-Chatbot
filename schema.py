"""
Document metadata schema and auto-detection rules for ingestion.
"""
from pydantic import BaseModel
from typing import Literal


class DocumentMeta(BaseModel):
    source: str
    role_access: list[str]
    department: str
    doc_type: str
    extra_metadata: dict = {}


ROLE_COLLECTION_MAP = {
    "hr":        "hr_docs",
    "finance":   "finance_docs",
    "marketing": "marketing_docs",
    "c_suite":   "all_docs",
}

# Rules for auto-detecting metadata from filenames during batch ingestion
AUTO_DETECT_RULES = [
    {
        "keywords": ["payroll", "salary", "compensation", "employee", "hr_", "leave", "policy"],
        "role_access": ["hr", "c_suite"],
        "department": "hr",
        "doc_type": "hr_document",
    },
    {
        "keywords": ["financial", "revenue", "budget", "p&l", "balance_sheet", "income",
                     "q1_", "q2_", "q3_", "q4_", "annual_report", "forecast"],
        "role_access": ["finance", "c_suite"],
        "department": "finance",
        "doc_type": "financial_report",
    },
    {
        "keywords": ["marketing", "campaign", "ads", "spend", "cac", "conversion", "brand"],
        "role_access": ["marketing", "finance", "c_suite"],
        "department": "marketing",
        "doc_type": "marketing_document",
    },
    {
        "keywords": ["strategy", "roadmap", "board", "exec", "all_hands", "company_wide"],
        "role_access": ["hr", "finance", "marketing", "c_suite"],
        "department": "executive",
        "doc_type": "strategy_document",
    },
]
