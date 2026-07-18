# Target Triage Copilot Backend

This backend is a FastAPI service for interactive, human-in-the-loop
small-molecule triage. It combines:

1. Deterministic RDKit cheminformatics for filtering and scoring.
2. Agentic orchestration (Supervisor, Knowledge, Cheminformatics, Critic,
  optional Diversifier rerun).
3. Live SSE streaming to the frontend.
4. Approval-gated export of CSV, SDF, and report artifacts.
5. A tool-calling chat copilot for run introspection and gate-time decisions.

## What It Does

Given a target and a candidate library, the backend:

1. Resolves the target and initializes the funnel.
2. Pulls known actives and literature context (with fallbacks).
3. Screens candidates with RDKit filters.
4. Ranks survivors with configurable ranking profiles.
5. Waits at a human approval gate.
6. On approval, exports:
  1. `shortlist.csv`
  2. `shortlist.sdf` (with 3D conformer generation)
  3. `report.md`

At the gate, operators can also request diversification + retriage before
approving.

## Quick Start

From this folder:

```bash
./run.sh
```

This script:

1. Creates `.venv` if missing.
2. Installs `requirements.txt`.
3. Starts Uvicorn at `http://localhost:8000`.

Manual equivalent:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Environment and LLM Providers

The backend supports two OpenAI-compatible providers via `app/llm.py`:

1. `ollama` (local)
2. `gateway` (hosted)

Frontend and backend can switch active provider/model at runtime through
`/config/llm` endpoints.

Export/cross-reference tuning is centralized in `app/config.py` and can be
overridden via environment variables (see repo-root `.env.example`):

1. `EXPORT_XREF_TIMEOUT`
2. `EXPORT_XREF_BUDGET_SECONDS`
3. `EXPORT_XREF_TOP_LIMIT`
4. `EXPORT_XREF_PROBE_N`
5. `EXPORT_XREF_WORKERS`
6. `EXPORT_EMBED_PARALLEL_MIN`

Additional runtime tunables are also centralized in `app/config.py`:

1. Source acquisition (`SOURCES_TIMEOUT`, `SOURCES_KNOWN_ACTIVES_LIMIT`, `SOURCES_PUBMED_RETMAX`)
2. Screening parallelism (`CHEM_PARALLEL_THRESHOLD`, `CHEM_BATCH_SIZE`, `CHEM_MAX_WORKERS_CAP`)
3. Tool payload limits (`TOOLS_MAX_EXAMPLES`, `TOOLS_MAX_BATCH`)
4. Chat runtime (`CHAT_MAX_ITERS`, `CHAT_POLL_INTERVAL_SECONDS`, `CHAT_HISTORY_TURNS`)
5. Diversify defaults/bounds (`DIVERSIFY_DEFAULT_LAM`, `DIVERSIFY_DEFAULT_CUTOFF`, `DIVERSIFY_DEFAULT_MAX_GENERATED`, `DIVERSIFY_MIN_MAX_GENERATED`, `DIVERSIFY_MAX_MAX_GENERATED`)

Export/cross-reference tuning is centralized in `app/config.py` and can be
overridden via environment variables (see repo-root `.env.example`):

1. `EXPORT_XREF_TIMEOUT`
2. `EXPORT_XREF_BUDGET_SECONDS`
3. `EXPORT_XREF_TOP_LIMIT`
4. `EXPORT_XREF_PROBE_N`
5. `EXPORT_XREF_WORKERS`
6. `EXPORT_EMBED_PARALLEL_MIN`

Additional runtime tunables are also centralized in `app/config.py`:

1. Source acquisition (`SOURCES_TIMEOUT`, `SOURCES_KNOWN_ACTIVES_LIMIT`, `SOURCES_PUBMED_RETMAX`)
2. Screening parallelism (`CHEM_PARALLEL_THRESHOLD`, `CHEM_BATCH_SIZE`, `CHEM_MAX_WORKERS_CAP`)
3. Tool payload limits (`TOOLS_MAX_EXAMPLES`, `TOOLS_MAX_BATCH`)
4. Chat runtime (`CHAT_MAX_ITERS`, `CHAT_POLL_INTERVAL_SECONDS`, `CHAT_HISTORY_TURNS`)
5. Diversify defaults/bounds (`DIVERSIFY_DEFAULT_LAM`, `DIVERSIFY_DEFAULT_CUTOFF`, `DIVERSIFY_DEFAULT_MAX_GENERATED`, `DIVERSIFY_MIN_MAX_GENERATED`, `DIVERSIFY_MAX_MAX_GENERATED`)

## High-Level Architecture

Main modules:

1. `app/main.py`: FastAPI routes, run/session lifecycle, chat and config APIs.
2. `app/graph.py`: Orchestration and gate-resume behavior.
3. `app/agents.py`: Agent implementations.
4. `app/tools.py`: Tool schemas/execution for Cheminformatics and Critic.
5. `app/chat_tools.py`: Read/mutate toolset for chat copilot.
6. `app/chem.py`: RDKit chemistry core (screening, scoring, diversity).
7. `app/export.py`: CSV/SDF/report generation and cross-reference enrichment.
8. `app/store.py`: Shared `Run` dataclass, registry, event emitter.
9. `app/sources.py`: ChEMBL and PubMed acquisition with fallbacks.

## Orchestration Flow

Primary path:

1. `supervisor`
2. `knowledge`
3. `cheminformatics`
4. `critic`
5. `human_gate`
6. `export`

Gate-time alternate path (operator-triggered):

1. `diversifier`
2. `cheminformatics`
3. `critic`
4. back to `human_gate`

Note: due to runtime limitations with `interrupt()` resume in this environment,
approval export runs from `Run` state directly in `graph.resume()`.

## Public API

### Core run lifecycle

1. `POST /run`
   1. multipart fields: `target_name`, `candidates`, optional `ranking_profile`
   2. returns `{ "run_id": "..." }`
2. `GET /stream/{run_id}`: SSE stream of run events.
1. `POST /approve/{run_id}`
   1. body: optional `{ "rankingProfile": "balanced|quality|explore|strict" }`
   2. applies deterministic gate rerank then exports.
2. `POST /diversify/{run_id}`: body: diversification mode/options and ranking profile.
1. `POST /rerank/{run_id}`
   1. body: `{ "rankingProfile": ... }`
   2. reranks at gate without exporting.
2. `GET /download/{run_id}/{kind}`
   1. `kind`: `csv | sdf | report`.

### Chat/session endpoints

1. `POST /session`: create setup-phase run for chat-first UX.
1. `POST /upload/{run_id}`: attach candidate file to setup run without starting pipeline.
2. `POST /chat/{run_id}`: SSE-style streamed chat response with tool-call events.
3. `POST /steer/{run_id}`: queue operator guidance while run is `running`.

### LLM/runtime config

1. `GET /config/llm`
2. `POST /config/llm`
3. `GET /config/llm/health`

### Utility endpoints

1. `GET /mol3d/{run_id}/{rank}`: on-demand 3D conformer molblock for viewer.
2. `GET /`: health check.

## SSE Event Model

Core event envelope:

```json
{ "type": "...", "agent": "...", "payload": {} }
```

Common event types:

1. `agent_start`
2. `agent_done`
3. `target_resolved`
4. `log`
5. `funnel`
6. `dossier_token`
7. `citations`
8. `grounding`
9. `tool_call`
10. `ranked`
11. `metric`
12. `diversity`
13. `awaiting_approval`
14. `export_progress`

`export_progress` is emitted during approval export and includes stage/message
for UX and chat introspection.

## Ranking Profiles

Supported profiles:

1. `balanced`
2. `quality`
3. `explore`
4. `strict`

Profiles influence scoring weights, confidence policy, and are carried through:

1. initial run (`/run` optional `ranking_profile`)
2. gate rerank (`/rerank`)
3. approve export (`/approve`)
4. diversification rerun (`/diversify`)

## Export Behavior

On approve, export does the following:

1. Builds enriched CSV rows (descriptors + cross-reference fields).
2. Performs cross-reference checks for ChEMBL/PubChem in parallel.
3. Generates SDF with 3D conformers (ETKDG + force-field optimization,
  with fallback behavior).
4. Writes report with provenance, funnel, cross-reference summary, dossier,
  citations, and ranked shortlist.

Cross-reference metadata is persisted on `Run` for chat tools:

1. `xref_summary`
2. `xref_by_smiles`
3. `export_artifacts`
4. `export_progress`

## Chat Copilot Tooling

Chat has status-gated tools:

1. Setup: status/read-only essentials.
2. Running/exported: read tools.
3. Awaiting approval: read + mutate preview/confirm tools.

Key read categories include:

1. Run status and trace.
2. Ranked/survivor inspection.
3. Dossier/citations.
4. Funnel/scaffold/metric summaries.
5. Methods and report metadata.
6. Cross-reference coverage and per-compound IDs.
7. Export stage/status introspection.
8. Score component explanation and profile comparison.

Mutate tools at gate use preview-then-confirm semantics (`confirmed=true` to
commit):

1. `rerank`
2. `focus_scaffold`
3. `diversify_shortlist`

## Dependencies

From `requirements.txt`:

1. `fastapi`
2. `uvicorn[standard]`
3. `sse-starlette`
4. `python-multipart`
5. `requests`
6. `rdkit`
7. `langgraph`
8. `openai`
9. `python-dotenv`
10. `pydantic-settings`

## File Layout

```text
backend/
  app/
   agents.py
   chat_tools.py
   chem.py
   conformers.py
   export.py
   graph.py
   llm.py
   loop.py
   main.py
   sources.py
   store.py
   tools.py
   data/
    fallback.py
  demo/
   data.csv
   egfr_candidates.csv
   getsmiles.py
  runs/
  requirements.txt
  run.sh
  README.md
```

## Notes and Caveats

1. `Run` objects are in-memory process state.
2. SSE stream closes at `awaiting_approval`; frontend re-subscribes as needed.
3. Setup-phase sessions intentionally do not enqueue pipeline SSE events to
  avoid unbounded queue growth before run start.
4. Cross-reference lookups and 3D generation can be the longest export stages,
  which is why explicit `export_progress` events are emitted.
