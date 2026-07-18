# Quantori Target Triage Copilot

Target Triage Copilot is an interactive, human-in-the-loop small-molecule
triage application. It combines deterministic RDKit chemistry with agentic
workflow orchestration, live streaming telemetry, and gate-controlled export.

The system is split into:

1. `frontend/`: React + TypeScript + Vite UI
2. `backend/`: FastAPI + LangGraph + RDKit service

## What the Project Does

Given a target and a candidate library, the app:

1. Resolves the target and gathers known-active/literature context.
2. Screens compounds using RDKit filters (parse validity, Lipinski, PAINS).
3. Scores and ranks survivors with selectable ranking profiles.
4. Pauses at a human approval gate.
5. Exports artifacts only after approval:
	1. `shortlist.csv`
	2. `shortlist.sdf` (with 3D conformers)
	3. `report.md`

At the gate, operators can rerank, diversify, and rerun triage before export.

## Repository Layout

```text
quantori-triage-copilot/
  backend/
	 app/
	 demo/
	 runs/
	 README.md
	 requirements.txt
	 run.sh
  frontend/
	 src/
	 README.md
	 package.json
  .env.example
  README.md
```

## Quick Start

### 1) Configure environment

Copy `.env.example` to `.env` at repository root and set values as needed.

Default provider setup supports:

1. Ollama (`LLM_PROVIDER=ollama`)
2. Gateway (`LLM_PROVIDER=gateway` with API key)

### Optional: Local LLM with Ollama

If you want to run the copilot fully local, you must install Ollama and pull a
model before starting the backend.

1. Install Ollama:
	1. macOS: `brew install ollama` or download from `https://ollama.com`
2. Start Ollama service:

```bash
ollama serve
```

3. Pull at least one model (example):

```bash
ollama pull llama3.1:8b
```

4. Set `.env` for local mode (example values):

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.1:8b
```

5. Quick check that model is available:

```bash
ollama list
```

If Ollama is not running or no model is pulled, LIVE chat/agent steps that use
the LLM will fail.

### 2) Start backend

```bash
cd backend
./run.sh
```

Backend runs at `http://localhost:8000`.

### 3) Start frontend

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`.

Vite proxies `/api` requests to backend (`localhost:8000`) in live mode.

## Frontend Modes

The UI supports two modes via header toggle:

1. DEMO: uses mocked stream data (`frontend/src/mock.ts`).
2. LIVE: uses real backend APIs via `/api` proxy.

## End-to-End Workflow

1. Choose target.
2. Upload candidate CSV/SMI.
3. Select ranking profile (balanced, quality, explore, strict).
4. Run triage.
5. Observe live stream:
	1. Supervisor
	2. Knowledge
	3. Cheminformatics
	4. Critic
6. Review dossier + shortlist.
7. At human gate:
	1. Approve and export, or
	2. Diversify and retriage.

## Key Backend APIs

Core run lifecycle:

1. `POST /run`
2. `GET /stream/{run_id}`
3. `POST /rerank/{run_id}`
4. `POST /diversify/{run_id}`
5. `POST /approve/{run_id}`
6. `GET /download/{run_id}/{kind}` (`csv|sdf|report`)

Chat/session:

1. `POST /session`
2. `POST /upload/{run_id}`
3. `POST /chat/{run_id}`
4. `POST /steer/{run_id}`

Runtime/model config:

1. `GET /config/llm`
2. `POST /config/llm`
3. `GET /config/llm/health`

3D utility:

1. `GET /mol3d/{run_id}/{rank}`

## Streaming and Observability

The backend streams structured SSE events (for example `agent_start`,
`tool_call`, `ranked`, `awaiting_approval`, `export_progress`).

This powers:

1. Live pipeline rail updates
2. Tool-call trace panels
3. Approve/export stage messaging
4. Audit trail download

## Artifacts and Runs

Approved runs are saved under `backend/runs/<run_id>/` with:

1. `shortlist.csv`
2. `shortlist.sdf`
3. `report.md`

## Documentation by Component

1. Backend details: `backend/README.md`
2. Frontend details: `frontend/README.md`

## Technology Stack

Backend:

1. FastAPI
2. LangGraph
3. RDKit
4. OpenAI-compatible provider abstraction

Frontend:

1. React 18
2. TypeScript
3. Vite
4. smiles-drawer + Mol* (viewer)

## Notes

1. Run state is in-memory in backend process (`Run` store), suitable for
	local/dev workflows.
2. Export can take time on larger sets due to parallel cross-reference and 3D
	conformer generation.
3. Human gate is intentionally required before export.
