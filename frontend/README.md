# Target Triage Copilot — Frontend

React UI for the drug-discovery triage agent. Shows the agent pipeline
running live, streams a cited target dossier, renders a ranked molecule
shortlist with provenance, and gates export behind a human sign-off.

## Screenshots

_(placeholders — will drop screenshots in here)_

**Setup screen**

`![setup screen](./docs/screenshots/setup.png)`

**Pipeline running (agent trace + tool calls)**

`![pipeline running](./docs/screenshots/pipeline-running.png)`

**Dossier tab**

`![dossier tab](./docs/screenshots/dossier.png)`

**Shortlist / ranked results**

`![shortlist](./docs/screenshots/shortlist.png)`

**Provider switcher**

`![provider switcher](./docs/screenshots/provider-switcher.png)`

## Run it (30 seconds)

```bash
npm install
npm run dev
```

Open http://localhost:5173. Starts in **DEMO** mode with a bundled EGFR run,
no backend needed — click **Run triage** and watch it go.

## Two modes (header toggle)

- **DEMO** — a fully simulated run (`src/mock.ts`), no backend required. Good
  for showing the UI off standalone, or while the backend's still cooking.
- **LIVE** — talks to the real FastAPI backend via the Vite proxy at `/api`.
  Needs the backend running on `localhost:8000`.

## What's on screen

- **Agent pipeline** (left) — Supervisor → Knowledge → Cheminformatics →
  Critic, lighting up as each one runs. Cheminformatics and Critic also show
  a live tool-call trace underneath — which tool got called, with what
  arguments, and whether it worked (⚠ on error, with the retry right below it).
- **Triage funnel** — input → filtered → ranked, shrinking as the agents work.
- **Dossier tab** — the cited target summary, `PMID:` chips link out to PubMed.
- **Shortlist tab** — ranked rows with rendered 2D structures, score bars,
  confidence badges, a ★ on known actives. Click a row for the full reasoning.
- **Provider switcher** (top right) — pick Ollama or the gateway, and which
  model, live, with a health dot. Only does anything in LIVE mode.
- **Approve bar** — the human gate. Nothing exports until you click it.

## Backend contract (LIVE mode)

| Method | Path | Purpose |
|---|---|---|
| POST | `/run` | multipart `target_name` + `candidates` (CSV) → `{ run_id }` |
| GET  | `/stream/{run_id}` | SSE stream of events |
| POST | `/approve/{run_id}` | write export files |
| GET  | `/download/{run_id}/{csv\|sdf\|report}` | download an artifact |
| GET/POST | `/config/llm` | read/switch the active LLM provider+model |
| GET  | `/config/llm/health` | is that provider actually reachable |

SSE events are JSON, shaped like `StreamEvent` in `src/mock.ts`:

```json
{ "type": "agent_start", "agent": "knowledge" }
{ "type": "dossier_token", "payload": "EGFR " }
{ "type": "funnel", "payload": { "input": 1500, "filtered": 214, "ranked": 20 } }
{ "type": "tool_call", "agent": "cheminformatics", "payload": { "tool": "screen_candidates", "status": "ok", "result_summary": "..." } }
{ "type": "ranked", "payload": [ /* RankedMol[] */ ] }
{ "type": "metric", "payload": { "recovered": 8, "total_actives": 10, "top_n": 20, "screened": 1500 } }
{ "type": "awaiting_approval" }
```

If the backend ever emits a different shape, `subscribe()` in `src/api.ts` is
the one place that needs to change — everything downstream just consumes the
same `StreamEvent` union that DEMO mode uses too.

## Stack

React 18 + TypeScript + Vite. Molecules rendered with `smiles-drawer` (pure
JS, no WASM). Icons from `lucide-react`. No CSS framework — it's all design
tokens in `src/styles.css`.

## Structure

```
src/
├── App.tsx              # state + orchestration for both modes
├── mock.ts               # simulated run (DEMO mode)
├── api.ts                 # live backend client (LIVE mode)
├── types.ts                # shared types
├── styles.css                # design tokens + all component styles
└── components/
    ├── Header.tsx              # brand, mode toggle, provider switcher
    ├── SetupPanel.tsx
    ├── PipelineRail.tsx           # agent cards + funnel + trace + tool calls
    ├── FunnelMeter.tsx
    ├── OutputTabs.tsx
    ├── DossierPanel.tsx
    ├── ResultsTable.tsx
    ├── MoleculeView.tsx             # SMILES → 2D structure
    ├── ConfidenceBadge.tsx
    └── ApproveBar.tsx
```
