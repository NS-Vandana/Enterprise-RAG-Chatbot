# Enterprise RAG Chatbot

Internal company chatbot with Role-Based Access Control, guardrails, evaluation, and Azure deployment.

## Architecture

```
Frontend (React) → FastAPI (RBAC Auth) → Guardrails → LangChain RAG → Qdrant + Groq/Azure OpenAI
                                                    ↓
                              LangSmith (tracing) + RAGAS (evals) + Azure Monitor (cost)
```

## Roles & Data Access

| Role       | Collections Accessible                              |
|------------|-----------------------------------------------------|
| `hr`       | HR docs, payroll                                    |
| `finance`  | Financial reports, marketing expenses               |
| `marketing`| Marketing docs, campaigns                           |
| `c_suite`  | All collections (full access)                       |

## Quick Start

### 1. Prerequisites
- Python 3.11+
- Node.js 18+ (frontend)
- Docker + kubectl (deployment)
- Azure subscription
- Groq API key (free at console.groq.com)

### 2. Environment Setup

```bash
cp .env.example .env
# Fill in all values in .env
```

### 3. Install & Run Locally

```bash
# Backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Ingest sample documents
python -m ingestion.ingest --dir ./sample_docs

# Frontend
cd frontend && npm install && npm run dev
```

### 4. Run Evaluations

```bash
python -m evals.ragas_eval
```

### 5. Deploy to Azure

```bash
# One-time infra setup
az deployment group create --resource-group rag-rg --template-file infra/main.bicep

# Then CI/CD handles deploys via GitHub Actions on push to main
```

## Project Structure

```
rag-enterprise/
├── main.py                    # FastAPI app entry point
├── requirements.txt
├── Dockerfile
├── .env.example
├── auth/
│   ├── rbac.py                # JWT validation + role extraction
│   └── models.py              # Pydantic auth models
├── rag/
│   ├── chain.py               # LangChain RAG chain
│   ├── retriever.py           # Qdrant multi-collection retriever
│   └── prompts.py             # System prompts
├── guardrails/
│   ├── pii.py                 # Presidio PII detection + scrubbing
│   ├── scope.py               # Out-of-scope classifier
│   └── middleware.py          # Guardrail wrappers
├── ingestion/
│   ├── ingest.py              # Docling → Qdrant pipeline
│   └── schema.py              # Document metadata schema
├── monitoring/
│   ├── cost_callback.py       # Token cost tracking callback
│   └── langsmith_config.py    # LangSmith tracing setup
├── evals/
│   ├── ragas_eval.py          # RAGAS evaluation harness
│   └── golden_set.json        # Golden test set
├── frontend/                  # React app
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── RoleBadge.tsx
│   │   │   └── Sidebar.tsx
│   │   ├── pages/
│   │   │   ├── Login.tsx
│   │   │   └── Chat.tsx
│   │   └── hooks/
│   │       ├── useChat.ts
│   │       └── useAuth.ts
│   ├── package.json
│   └── vite.config.ts
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── qdrant.yaml
│   └── secrets.yaml
├── infra/
│   └── main.bicep             # Azure infra as code
└── .github/
    └── workflows/
        └── deploy.yml         # CI/CD pipeline
```

## Guardrails

- **PII Detection**: Microsoft Presidio scans every query and response for names, SSNs, emails, phone numbers, Aadhaar, PAN, credit cards
- **Scope Filter**: Small Llama model classifies whether the question relates to company data
- **RBAC Enforcement**: JWT role claim checked on every request; wrong role = 403

## Monitoring

- **LangSmith**: Full trace of every RAG call — retrieved chunks, prompt, LLM output, latency
- **RAGAS Metrics**: Faithfulness > 0.80, Answer Relevancy > 0.75, Context Recall > 0.70
- **Azure Monitor**: Token cost per user/role, daily spend alerts
- **CI Quality Gate**: Evals run before every deploy; fail = block deploy
