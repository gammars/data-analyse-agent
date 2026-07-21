# Evaluation Pipeline

This directory contains a lightweight, reproducible evaluation pipeline for the local data analysis agent.

The first version focuses on deterministic checks that can run without calling an LLM:

- ScopeRouter rule routing
- Planner fallback intent and tool policy
- Tool routing expectations
- SQLService read-only execution and safety
- Python sandbox static safety checks and optional Docker execution

Run:

```powershell
conda run -n data-analyse-agent python evals/runners/run_evals.py
```

Run Docker sandbox smoke evaluation too:

```powershell
conda run -n data-analyse-agent python evals/runners/run_evals.py --run-docker
```

Outputs are written to:

```text
evals/reports/latest.json
```

The bundled fixture dataset is created in a temporary directory at runtime. It mimics a small e-commerce schema with orders, order_items, payments, products, customers, and reviews.
