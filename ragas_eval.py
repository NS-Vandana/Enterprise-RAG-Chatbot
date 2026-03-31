"""
RAGAS evaluation harness.
Runs a golden test set through the live RAG chain and measures:
  - Faithfulness       (answer grounded in retrieved context)
  - Answer Relevancy   (answer relevant to the question)
  - Context Recall     (retrieved context covers the ground truth)

Exits with code 1 if any metric falls below threshold — blocks CI deploy.

Usage:
    python -m evals.ragas_eval
    python -m evals.ragas_eval --golden evals/golden_set.json --threshold-faithfulness 0.75
"""
import asyncio
import argparse
import json
import sys
import os
from pathlib import Path
import structlog

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall
from datasets import Dataset

log = structlog.get_logger()

# ── Default thresholds ─────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "faithfulness":     0.80,
    "answer_relevancy": 0.75,
    "context_recall":   0.70,
}


# ── Golden set loading ─────────────────────────────────────────────────────

def load_golden_set(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    required = {"question", "ground_truth", "role"}
    for i, item in enumerate(data):
        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"Golden set item {i} missing fields: {missing}")
    return data


# ── Eval runner ────────────────────────────────────────────────────────────

async def run_single(item: dict) -> dict | None:
    """Run one golden set item through the RAG chain."""
    from auth.rbac import ROLE_NAMESPACES
    from rag.chain import run_rag_chain

    role = item["role"]
    collections = ROLE_NAMESPACES.get(role, ["all_docs"])

    try:
        result = await run_rag_chain(
            query=item["question"],
            role=role,
            allowed_collections=collections,
        )
        return {
            "question":     item["question"],
            "answer":       result["answer"],
            "contexts":     [d.page_content for d in result.get("docs", [])],
            "ground_truth": item["ground_truth"],
        }
    except Exception as e:
        log.error("RAG chain error on golden item", question=item["question"][:60], error=str(e))
        return None


async def collect_results(golden: list[dict]) -> list[dict]:
    tasks = [run_single(item) for item in golden]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ── Main eval ──────────────────────────────────────────────────────────────

async def run_evals(golden_path: str, thresholds: dict) -> bool:
    """Returns True if all metrics pass."""
    log.info("Loading golden set", path=golden_path)
    golden = load_golden_set(golden_path)
    log.info("Running golden set through RAG chain", count=len(golden))

    results = await collect_results(golden)
    if not results:
        log.error("No results collected — aborting eval")
        return False

    log.info("Collected RAG results", collected=len(results), total=len(golden))

    dataset = Dataset.from_dict({
        "question":     [r["question"]     for r in results],
        "answer":       [r["answer"]       for r in results],
        "contexts":     [r["contexts"]     for r in results],
        "ground_truth": [r["ground_truth"] for r in results],
    })

    log.info("Running RAGAS evaluation...")
    scores = evaluate(dataset, metrics=[faithfulness, answer_relevancy, context_recall])

    print("\n" + "=" * 50)
    print("RAGAS EVALUATION RESULTS")
    print("=" * 50)

    failed_metrics = []
    for metric_name, threshold in thresholds.items():
        score = float(scores[metric_name])
        status = "PASS" if score >= threshold else "FAIL"
        marker = "" if status == "PASS" else "  <-- BELOW THRESHOLD"
        print(f"  {metric_name:<25} {score:.4f}  (threshold: {threshold})  {status}{marker}")
        if status == "FAIL":
            failed_metrics.append(metric_name)

    print("=" * 50)

    # Push to LangSmith dataset (optional)
    try:
        from langsmith import Client
        ls_client = Client()
        ls_client.create_run(
            name="ragas-eval",
            project_name=os.getenv("LANGCHAIN_PROJECT", "rag-enterprise-chatbot"),
            inputs={"n_questions": len(golden), "n_evaluated": len(results)},
            outputs={k: float(scores[k]) for k in thresholds},
            run_type="chain",
            tags=["eval", "ragas"],
        )
        log.info("Eval results pushed to LangSmith")
    except Exception as e:
        log.warning("Could not push to LangSmith", error=str(e))

    if failed_metrics:
        print(f"\nEVAL FAILED: {failed_metrics}")
        print("Deployment blocked.\n")
        return False

    print("\nAll evaluations PASSED. Proceeding with deployment.\n")
    return True


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run RAGAS evaluations")
    parser.add_argument("--golden", default="evals/golden_set.json")
    parser.add_argument("--threshold-faithfulness",     type=float, default=0.80)
    parser.add_argument("--threshold-answer-relevancy", type=float, default=0.75)
    parser.add_argument("--threshold-context-recall",   type=float, default=0.70)
    args = parser.parse_args()

    thresholds = {
        "faithfulness":     args.threshold_faithfulness,
        "answer_relevancy": args.threshold_answer_relevancy,
        "context_recall":   args.threshold_context_recall,
    }

    passed = asyncio.run(run_evals(args.golden, thresholds))
    sys.exit(0 if passed else 1)
