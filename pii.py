"""
PII detection and anonymization using Microsoft Presidio.
Covers global + India-specific entities.
"""
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
import structlog

log = structlog.get_logger()

# ── Entity types to detect ─────────────────────────────────────────────────

PII_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
    "US_BANK_NUMBER",
    "US_PASSPORT",
    "IN_PAN",           # India PAN card
    "IN_AADHAAR",       # India Aadhaar
    "IP_ADDRESS",
    "LOCATION",
    "DATE_TIME",        # catch DOB references
    "NRP",              # nationality/religion/political group
    "MEDICAL_LICENSE",
    "URL",
]


# ── Engine setup ───────────────────────────────────────────────────────────

def _build_analyzer() -> AnalyzerEngine:
    nlp_config = {"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]}
    provider = NlpEngineProvider(nlp_configuration=nlp_config)
    nlp_engine = provider.create_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine)


_analyzer: AnalyzerEngine | None = None
_anonymizer: AnonymizerEngine = AnonymizerEngine()


def get_analyzer() -> AnalyzerEngine:
    global _analyzer
    if _analyzer is None:
        try:
            _analyzer = _build_analyzer()
        except Exception as e:
            log.warning("Could not load spacy model, using default NLP engine", error=str(e))
            _analyzer = AnalyzerEngine()
    return _analyzer


# ── Public API ─────────────────────────────────────────────────────────────

def detect_pii(text: str) -> list:
    """Return list of RecognizerResult for any PII found."""
    try:
        return get_analyzer().analyze(text=text, entities=PII_ENTITIES, language="en")
    except Exception as e:
        log.error("PII detection error", error=str(e))
        return []


def has_pii(text: str) -> bool:
    """True if any PII entities found with confidence > 0.6."""
    results = detect_pii(text)
    return any(r.score > 0.6 for r in results)


def scrub_pii(text: str) -> str:
    """Replace detected PII with type placeholders like <PERSON> <EMAIL_ADDRESS>."""
    results = detect_pii(text)
    high_confidence = [r for r in results if r.score > 0.6]
    if not high_confidence:
        return text

    try:
        operators = {
            entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
            for entity in PII_ENTITIES
        }
        anonymized = _anonymizer.anonymize(
            text=text,
            analyzer_results=high_confidence,
            operators=operators,
        )
        return anonymized.text
    except Exception as e:
        log.error("PII anonymization error", error=str(e))
        return text


def get_pii_report(text: str) -> list[dict]:
    """Return human-readable report of detected PII types."""
    results = detect_pii(text)
    return [
        {
            "entity_type": r.entity_type,
            "score": round(r.score, 3),
            "start": r.start,
            "end": r.end,
            "text_snippet": text[r.start:r.end],
        }
        for r in results if r.score > 0.6
    ]
