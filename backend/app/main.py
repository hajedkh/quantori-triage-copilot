"""FastAPI app — matches the frontend contract exactly.

Routes (frontend calls them via the Vite /api proxy):
  POST /run                       -> {run_id}
  GET  /stream/{run_id}           -> SSE event stream
    POST /approve/{run_id}          -> writes the export files
    POST /diversify/{run_id}        -> diversifier -> cheminformatics -> critic -> gate
  GET  /download/{run_id}/{kind}  -> csv | sdf | report

Orchestration is a LangGraph graph (see graph.py) with an interrupt at the
human gate. This module parses input, kicks off the graph, streams events,
and triggers export on approval.
"""

from __future__ import annotations
import asyncio
import io
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import graph, llm, loop, chat_tools, conformers
from .config import load_chat_config, load_diversify_config
from .store import Run, RUNS

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

_CHAT_CFG = load_chat_config()
_DIVERSIFY_CFG = load_diversify_config()

app = FastAPI(title="Target Triage Copilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_candidates(raw: bytes) -> list[dict]:
    """
    Parse an uploaded CSV/SMI into [{smiles,label}]. Flexible about columns.
    """
    text = raw.decode("utf-8", errors="ignore")
    out: list[dict] = []
    sniff = text.splitlines()

    if sniff and ("," in sniff[0]) and ("smiles" in sniff[0].lower()):
        reader = csv.DictReader(io.StringIO(text))
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        smi_col = cols.get("smiles")
        label_col = cols.get("label") or cols.get("activity")
        for row in reader:
            smi = (row.get(smi_col) or "").strip()
            if not smi:
                continue
            label = None
            if label_col:
                v = (row.get(label_col) or "").strip().lower()
                label = v in ("active", "1", "true", "yes")
            out.append({"smiles": smi, "label": label})
        return out

    for line in sniff:
        line = line.strip()
        if not line or line.lower().startswith("smiles"):
            continue
        smi = line.split()[0].split(",")[0].strip()
        if smi:
            out.append({"smiles": smi, "label": None})
    return out


class DiversifyIn(BaseModel):
    mode: str = "scaffold"
    lam: float | None = None
    cutoff: float | None = None
    maxGenerated: int = 200
    rankingProfile: str = "balanced"


class ApproveIn(BaseModel):
    rankingProfile: str = "balanced"


class RerankOut(BaseModel):
    ok: bool
    rankingProfile: str
    ranked: list[dict]


@app.post("/run")
async def start_run(
    target_name: str = Form(...),
    candidates: UploadFile = File(...),
    ranking_profile: str = Form("balanced"),
):
    raw = await candidates.read()
    parsed = parse_candidates(raw)
    if not parsed:
        raise HTTPException(400, "No SMILES found in the uploaded file.")
    # Never trust the client value blindly, even though the frontend only
    # ever sends one of these four (a <select>, not free text) — same
    # whitelist check already used by /rerank and /diversify.
    ranking_profile = (ranking_profile or "balanced").strip().lower()
    if ranking_profile not in ("balanced", "quality", "explore", "strict"):
        raise HTTPException(400, f"unknown ranking profile: {ranking_profile!r}")
    run_id = uuid.uuid4().hex[:8]
    cfg = llm.get_active_config()
    run = Run(
        id=run_id,
        target_name=target_name.strip(),
        candidates=parsed,
        ranking_profile=ranking_profile,
    )
    run.provenance = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": cfg.model,
        "provider": cfg.provider,
    }
    RUNS[run_id] = run
    # run the graph until it pauses at the human gate
    asyncio.create_task(graph.run_until_gate(run_id))
    return {"run_id": run_id}


@app.get("/stream/{run_id}")
async def stream(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    async def gen():
        while True:
            event = await run.queue.get()
            if event is None:  # sentinel -> close
                break
            yield json.dumps(event)

    return EventSourceResponse(gen())


@app.post("/approve/{run_id}")
async def approve(run_id: str, body: ApproveIn | None = None):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            400, f"can only approve at the human gate (status={run.status!r})"
        )

    ranking_profile = (
        ((body.rankingProfile if body else "balanced") or "balanced").strip().lower()
    )
    if ranking_profile not in ("balanced", "quality", "explore", "strict"):
        raise HTTPException(400, f"unknown ranking profile: {ranking_profile!r}")

    await graph.apply_gate_rerank(run_id, ranking_profile)
    await graph.resume(run_id)  # writes the export files
    return {"ok": True}


@app.post("/diversify/{run_id}")
async def diversify(run_id: str, body: DiversifyIn):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            400, f"can only diversify at the human gate (status={run.status!r})"
        )

    mode = (body.mode or "scaffold").strip().lower()
    if mode not in ("off", "scaffold", "mmr", "cluster"):
        raise HTTPException(400, f"unknown diversify mode: {body.mode!r}")

    lam = _DIVERSIFY_CFG.default_lam if body.lam is None else float(body.lam)
    lam = max(0.0, min(1.0, lam))

    cutoff = (
        _DIVERSIFY_CFG.default_cutoff if body.cutoff is None else float(body.cutoff)
    )
    cutoff = max(0.1, min(0.9, cutoff))

    max_generated = int(body.maxGenerated or _DIVERSIFY_CFG.default_max_generated)
    max_generated = max(
        _DIVERSIFY_CFG.min_max_generated,
        min(_DIVERSIFY_CFG.max_max_generated, max_generated),
    )

    ranking_profile = (body.rankingProfile or "balanced").strip().lower()
    if ranking_profile not in ("balanced", "quality", "explore", "strict"):
        raise HTTPException(400, f"unknown ranking profile: {body.rankingProfile!r}")

    opts = {
        "mode": mode,
        "lam": lam,
        "cutoff": cutoff,
        "max_generated": max_generated,
        "ranking_profile": ranking_profile,
    }

    asyncio.create_task(graph.diversify_and_retriage(run_id, opts))
    return {"ok": True}


@app.post("/rerank/{run_id}", response_model=RerankOut)
async def rerank(run_id: str, body: ApproveIn):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            400, f"can only rerank at the human gate (status={run.status!r})"
        )

    ranking_profile = (body.rankingProfile or "balanced").strip().lower()
    if ranking_profile not in ("balanced", "quality", "explore", "strict"):
        raise HTTPException(400, f"unknown ranking profile: {ranking_profile!r}")

    await graph.apply_gate_rerank(run_id, ranking_profile)
    return {
        "ok": True,
        "rankingProfile": ranking_profile,
        "ranked": run.ranked,
    }


@app.get("/download/{run_id}/{kind}")
async def download(run_id: str, kind: str):
    ext = {"csv": "csv", "sdf": "sdf", "report": "md"}.get(kind)
    if not ext:
        raise HTTPException(400, "unknown export kind")
    name = "report" if kind == "report" else "shortlist"
    path = RUNS_DIR / run_id / f"{name}.{ext}"
    if not path.exists():
        raise HTTPException(404, "not exported yet - approve first")
    return FileResponse(path, filename=path.name)


# ------------------------------------------------------------- 3D molecule view --
# Purely additive, on-demand: nothing here runs during a triage run, nothing
# here can affect the ranked table, the 2D depiction, or any export if it
# fails — see conformers.py. Cached per (run_id, smiles) in memory, same
# lifetime/style as RUNS itself, so re-opening a molecule doesn't re-embed.
# Keyed by smiles, not rank: rank is mutable (rerank/diversify can put a
# different molecule at the same rank), so a rank-only key would risk
# serving a stale, wrong structure after the shortlist gets re-ranked.
_mol3d_cache: dict[tuple[str, str], dict] = {}


@app.get("/mol3d/{run_id}/{rank}")
async def mol3d(run_id: str, rank: int):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if rank < 1 or rank > len(run.ranked):
        raise HTTPException(
            404, f"rank {rank} not found — only {len(run.ranked)} ranked results exist"
        )

    smiles = run.ranked[rank - 1]["smiles"]
    cache_key = (run_id, smiles)
    cached = _mol3d_cache.get(cache_key)
    if cached is not None:
        return cached

    # CPU-bound RDKit work (embed + force-field optimization) — off the event
    # loop so a slow/failing embed can't stall the SSE stream or other requests.
    molblock = await asyncio.to_thread(conformers.to_molblock_3d, smiles)
    result = (
        {"ok": True, "molblock": molblock} if molblock is not None else {"ok": False}
    )
    _mol3d_cache[cache_key] = result
    return result


# ---------------------------------------------------------------- chat copilot --
# Not a new agent: reuses loop.run_tool_loop with a different tool set
# (chat_tools.py). Present from the first screen (POST /session creates an
# empty Run in "setup" status before any pipeline work exists) and stays
# useful through the run and the approval gate.

CHAT_SYSTEM_PROMPT = (
    "You are the Triage Copilot for a drug-discovery screening pipeline. "
    "Answer ONLY from your tools. Never invent a number, a molecule, a rank, "
    "an IC50, or a rationale — if it isn't in reach of a tool, say you don't "
    "know. You have no general chemistry knowledge to offer: everything you "
    "state must trace to a tool result. Always cite: rank #, ChEMBL id, tool "
    "name, or PMID. "
    "For workflow/status questions use get_run_status and get_agent_trace; for "
    "the screening funnel use get_funnel_breakdown (invalid/Lipinski/PAINS/"
    "survivors); for chemotype coverage use get_scaffold_summary; for the "
    "held-out validation use get_metric; to explain ranking profiles use "
    "get_ranking_options; for methods/database questions use "
    "get_methods_summary; for export stage and report metadata use "
    "get_export_status/get_report_metadata; for cross-reference coverage and "
    "IDs use get_crossref_summary/get_compound_crossref; for profile deltas "
    "use compare_ranking_profiles; for threshold policy use "
    "get_filter_thresholds; for score decomposition use "
    "explain_score_components; for a single compound use "
    "get_molecule/explain/why_not/similar_to. "
    "Before any change, preview it and ask. A mutate tool (rerank, "
    "focus_scaffold, diversify_shortlist) called without confirmed=true returns "
    "a preview, not a result — relay exactly what would change (which molecules "
    "enter/leave the top 20, how many scaffolds) and wait for the operator's "
    "yes before calling again with confirmed=true. diversify_shortlist takes a "
    "mode (off/scaffold/mmr/cluster) and, for mmr, a lambda from 0 (spread) to "
    "1 (quality). "
    "At the human gate the operator can either approve export or trigger a "
    "diversification rerun (diversifier -> cheminformatics -> critic) from the "
    "UI; you cannot click buttons yourself, so explain the tradeoff and tell "
    "them which action to take. "
    "While the pipeline is running you may only QUEUE guidance to the "
    "agents; you have no direct control. Never claim a steer took effect — "
    "report it as queued. "
    "You cannot start a run yourself — there is no tool for it. If asked how "
    "to begin, how to run this, or to start/launch a screen, tell the "
    "operator plainly: choose a target protein, upload a candidate library, "
    "optionally pick a ranking profile (balanced/quality/explore/strict — "
    "balanced is fine if unsure), then click Run triage, all in the form on "
    "this screen."
)


class ChatIn(BaseModel):
    message: str


class SteerIn(BaseModel):
    message: str


@app.post("/session")
async def create_session():
    """The frontend calls this on load so the chat has a run_id to talk to
    before any target/library/pipeline exists."""
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = Run(id=run_id, status="setup")
    return {"run_id": run_id}


@app.post("/upload/{run_id}")
async def upload_library(run_id: str, candidates: UploadFile = File(...)):
    """Attach a candidate library to a setup-phase Run without starting the
    graph — the chat's start_run tool is what actually begins it. POST /run
    (the existing form path) still creates+starts a run in one step."""
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    raw = await candidates.read()
    parsed = parse_candidates(raw)
    if not parsed:
        raise HTTPException(400, "No SMILES found in the uploaded file.")
    run.candidates = parsed
    return {"count": len(parsed)}


@app.post("/chat/{run_id}")
async def chat(run_id: str, body: ChatIn):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    transcript = "".join(
        f"{t['role']}: {t['content']}\n"
        for t in run.chat_history[-_CHAT_CFG.history_turns :]
    )
    user_msg = f"{transcript}user: {body.message}" if transcript else body.message
    tools = chat_tools.tools_for_status(run.status)

    async def executor(name, args):
        return await chat_tools.execute_chat_tool(run, name, args)

    # Hardcoded to the gateway, not get_active_config(): a single Ollama
    # instance serializes requests, so chatting on Ollama during a run would
    # slow the agents down with every turn. Agents keep whichever provider is
    # selected in the UI; chat is pinned to the gateway so it never contends
    # with them — this split is the reason the dual-provider setup exists.
    cfg = llm.load_config("gateway")

    start_idx = len(run.events)

    async def gen():
        # Chat uses a lower max_iters for responsiveness; force_tool_first is
        # configurable (default true) so operator Q&A stays tool-grounded.
        # Run the loop as a background task and poll run.events so tool_call
        # events reach the browser as they happen, not all at once after the
        # whole loop finishes — that dead-air wait was the actual "thinking
        # too long" complaint, not the LLM latency itself. Can't just drain
        # run.queue here — that's the pipeline /stream's own queue, shared
        # with a possibly-concurrent listener; polling run.events (append-only,
        # already how every event gets recorded) doesn't steal from it.
        task = asyncio.create_task(
            loop.run_tool_loop(
                run,
                "chat",
                CHAT_SYSTEM_PROMPT,
                user_msg,
                tools,
                executor,
                cfg=cfg,
                max_iters=_CHAT_CFG.max_iters,
                force_tool_first=_CHAT_CFG.force_tool_first,
            )
        )
        sent = start_idx
        while not task.done():
            if len(run.events) > sent:
                for event in run.events[sent:]:
                    if event.get("agent") == "chat":
                        yield json.dumps(event)
                sent = len(run.events)
            await asyncio.sleep(_CHAT_CFG.poll_interval_seconds)
        for event in run.events[sent:]:
            if event.get("agent") == "chat":
                yield json.dumps(event)

        answer = task.result()
        run.chat_history.append({"role": "user", "content": body.message})
        run.chat_history.append({"role": "assistant", "content": answer})
        for tok in answer.split(" "):
            yield json.dumps({"type": "chat_token", "payload": tok + " "})
        yield json.dumps({"type": "chat_done"})

    return EventSourceResponse(gen())


@app.post("/steer/{run_id}")
async def steer(run_id: str, body: SteerIn):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "running":
        raise HTTPException(
            400, f"can only steer a running pipeline (status={run.status!r})"
        )
    run.inbox.append(body.message)
    return {"queued": True, "position": len(run.inbox)}


class LLMConfigIn(BaseModel):
    provider: str
    model: str


def _llm_options() -> dict:
    return {"ollama": llm.list_ollama_models(), "gateway": llm.GATEWAY_MODELS}


@app.get("/config/llm")
async def get_llm_config():
    """Current active LLM provider/model + the selectable options for each.
    Never returns the api_key — not even partially."""
    cfg = llm.get_active_config()
    return {"provider": cfg.provider, "model": cfg.model, "options": _llm_options()}


@app.post("/config/llm")
async def post_llm_config(body: LLMConfigIn):
    provider = body.provider.strip().lower()
    if provider not in ("ollama", "gateway"):
        raise HTTPException(400, f"unknown provider: {provider!r}")
    options = _llm_options()
    valid_models = options[provider]
    if body.model not in valid_models:
        raise HTTPException(
            400,
            f"unknown model {body.model!r} for provider {provider!r} — valid: {valid_models}",
        )
    cfg = llm.set_active_config(provider, body.model)
    return {"provider": cfg.provider, "model": cfg.model, "options": options}


@app.get("/config/llm/health")
async def get_llm_health(provider: Optional[str] = None):
    """Health-check a provider without switching to it. Defaults to the active provider."""
    active = llm.get_active_config()
    provider = (provider or active.provider).strip().lower()
    if provider not in ("ollama", "gateway"):
        raise HTTPException(400, f"unknown provider: {provider!r}")
    cfg = active if provider == active.provider else llm.load_config(provider)
    return await llm.health(cfg)


@app.get("/")
async def health():
    return {"ok": True, "service": "target-triage-copilot", "orchestrator": "langgraph"}
