# Target Triage Copilot — Backend

FastAPI backend for the drug-discovery triage agent. You give it a target
protein and a pile of candidate molecules, and it comes back with a ranked,
explained shortlist. Four agents run in sequence (Supervisor, Knowledge,
Cheminformatics, Critic), everything streams to the frontend over SSE, and
nothing gets exported until a human approves it.

## Diagrams

_(placeholders — will drop images in here)_

**Global / deployment diagram**

`![global diagram](./docs/diagrams/global.png)`

**Software / architecture diagram**

`![architecture diagram](./docs/diagrams/architecture.png)`

**Agentic workflow diagram**

`![agentic workflow](./docs/diagrams/agentic-workflow.png)`

**Sequence diagram**

`![sequence diagram](./docs/diagrams/sequence.png)`

## Run it

```bash
cd backend
./run.sh
```

Creates a virtualenv, installs deps, starts the server on
**http://localhost:8000**. First run takes a minute — RDKit's the big one.

Manual version if you'd rather:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then in the frontend, flip the header toggle to **LIVE**. Vite proxies `/api`
to `localhost:8000`, so there's no CORS setup to worry about.

## LLM setup

Both agentic agents (Cheminformatics and Critic) and the Knowledge agent's
dossier need an LLM behind them. Two providers, both OpenAI-compatible so it's
one code path either way:

- **Ollama**, local — `ollama serve` and pull a model that actually supports
  tool calling (`ollama pull qwen2.5:7b` — `mistral` looked fine at first but
  quietly breaks once you hand it more than a couple of tools, so don't use it
  for the agentic agents).
- **Quantori Litellm Gateway** — a hosted proxy, currently routes to a mix of
  OpenAI/Anthropic/Google models. See `.env.example` at the repo root for the
  keys it needs.

Config lives in `.env` at the **repo root**, not in `backend/`. Copy
`.env.example`, fill in the gateway key, done. You can also switch providers
live from the UI (top-right dropdown) without restarting anything.

## Test it without the frontend

```bash
curl http://localhost:8000/            # health -> {"ok": true, ...}
```

Or just drive the whole thing with curl and the demo CSV — see the API table
below.

## Demo files

- `demo/egfr_candidates.csv` — small, 51 molecules (10 real EGFR inhibitors +
  41 decoys). Good for a quick run.
- `demo/data.csv` — much bigger, ~20k molecules pulled from DUD-E. Use this if
  you want the shortlist to have more than a handful of rows — the Critic
  drops anything below "Medium" confidence, so a small candidate pool means a
  small shortlist. More candidates in, more chances of clearing the bar.
- `demo/getsmiles.py` — the script that fetched `data.csv`. No deps, stdlib
  only. `./getsmiles.py --target braf` to grab a different target.

The `label` column in either file is hidden ground truth, only used to compute
the recovery metric at the end — the ranking itself never sees it.

## What's real vs. what's not

- **All the chemistry is real.** RDKit does the standardization, Lipinski
  check, PAINS filter, Morgan fingerprints, Tanimoto similarity — on your
  actual uploaded molecules, every time. No LLM anywhere near that math.
- **ChEMBL and PubMed are fetched live** when reachable, with a small bundled
  fallback so a flaky connection doesn't kill a live demo.
- **Cheminformatics and Critic are genuinely agentic** — they call real tools,
  decide their own thresholds/weights, and adapt when a tool call fails. Not
  scripted, not a fixed pipeline of prompts.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/run` | multipart `target_name` + `candidates` (CSV) → `{run_id}` |
| GET  | `/stream/{run_id}` | SSE stream of pipeline events |
| POST | `/approve/{run_id}` | write CSV / SDF / report to disk |
| GET  | `/download/{run_id}/{csv\|sdf\|report}` | download an artifact |
| GET  | `/config/llm` | current provider/model + what's available |
| POST | `/config/llm` | switch provider/model |
| GET  | `/config/llm/health` | ping a provider |

SSE events are JSON: `{ "type": ..., "agent"?: ..., "payload"?: ... }`. Types:
`agent_start`, `agent_done`, `target_resolved`, `log`, `dossier_token`,
`citations`, `funnel`, `tool_call`, `ranked`, `metric`, `awaiting_approval`.

`tool_call` is the interesting one — it's what lets the frontend show, live,
which tool an agent just called, with what arguments, and whether it worked.

## Structure

```
backend/
├── app/
│   ├── main.py        # FastAPI routes, run manager, SSE, CSV parsing
│   ├── graph.py       # LangGraph wiring: nodes + edges + human-gate
│   ├── agents.py       # the 4 agents as async functions
│   ├── tools.py        # tool schemas + implementations for the agentic agents
│   ├── loop.py          # the tool-calling loop shared by those agents
│   ├── store.py         # shared Run object + registry + emit helper
│   ├── chem.py           # RDKit: standardize, filter, similarity, score
│   ├── sources.py        # ChEMBL + PubMed clients (with fallbacks)
│   ├── llm.py             # OpenAI-compatible chat layer, both providers
│   ├── export.py           # CSV / SDF / report writers
│   └── data/
│       └── fallback.py      # bundled actives + target aliases
├── demo/
│   ├── egfr_candidates.csv
│   ├── data.csv
│   └── getsmiles.py
├── requirements.txt
├── run.sh
└── README.md
```

## Orchestration

It's a LangGraph `StateGraph`, one straight line, no branching:

```
supervisor → knowledge → cheminformatics → critic → human_gate → export
```

Each agent is a node. Heavy data (candidates, dossier, ranked results) lives
on a plain `Run` object looked up by id — the graph's own state only carries
that id, so the checkpoint stays tiny.

Cheminformatics and Critic aren't just prompt-fill-in-the-blank nodes — inside
each one, an LLM runs a real tool-calling loop: it picks a tool, sees the
actual result, decides what to do next, up to 6 rounds. Two different runs
against two different targets genuinely make different decisions from the
same starting instructions — that's the whole point.

One honest caveat: the human-approval step doesn't resume the LangGraph
checkpoint the way the docs for `interrupt()` describe — that path is broken
on this Python version. Approval works by pulling the finished data straight
off the `Run` object instead. Everything downstream of it works fine either
way.
