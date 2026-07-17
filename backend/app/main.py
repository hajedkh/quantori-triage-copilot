"""FastAPI app — matches the frontend contract exactly.

Routes (frontend calls them via the Vite /api proxy):
  POST /run                       -> {run_id}
  GET  /stream/{run_id}           -> SSE event stream
  POST /approve/{run_id}          -> writes the export files
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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import graph, llm, loop, chat_tools
from .store import Run, RUNS

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

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


@app.post("/run")
async def start_run(target_name: str = Form(...), candidates: UploadFile = File(...)):
    raw = await candidates.read()
    parsed = parse_candidates(raw)
    if not parsed:
        raise HTTPException(400, "No SMILES found in the uploaded file.")
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = Run(id=run_id, target_name=target_name.strip(), candidates=parsed)
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
async def approve(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    await graph.resume(run_id)  # writes the export files
    return {"ok": True}


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
    "Before any change, preview it and ask. A mutate tool called without "
    "confirmed=true returns a preview, not a result — relay exactly what "
    "would change and wait for the operator's yes before calling again with "
    "confirmed=true. "
    "While the pipeline is running you may only QUEUE guidance to the "
    "agents; you have no direct control. Never claim a steer took effect — "
    "report it as queued. "
    "You cannot start a run yourself — there is no tool for it. If asked how "
    "to begin, how to run this, or to start/launch a screen, tell the "
    "operator plainly: choose a target protein and upload a candidate "
    "library in the form on this screen, then click Run triage."
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

    transcript = "".join(f"{t['role']}: {t['content']}\n" for t in run.chat_history[-6:])
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
        # force_tool_first=False + a lower max_iters: chat-only speed tuning,
        # doesn't touch cheminformatics/critic's calls (loop.py defaults unchanged).
        # Run the loop as a background task and poll run.events so tool_call
        # events reach the browser as they happen, not all at once after the
        # whole loop finishes — that dead-air wait was the actual "thinking
        # too long" complaint, not the LLM latency itself. Can't just drain
        # run.queue here — that's the pipeline /stream's own queue, shared
        # with a possibly-concurrent listener; polling run.events (append-only,
        # already how every event gets recorded) doesn't steal from it.
        task = asyncio.create_task(
            loop.run_tool_loop(
                run, "chat", CHAT_SYSTEM_PROMPT, user_msg, tools, executor,
                cfg=cfg, max_iters=2, force_tool_first=False,
            )
        )
        sent = start_idx
        while not task.done():
            if len(run.events) > sent:
                for event in run.events[sent:]:
                    if event.get("agent") == "chat":
                        yield json.dumps(event)
                sent = len(run.events)
            await asyncio.sleep(0.05)
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
        raise HTTPException(400, f"can only steer a running pipeline (status={run.status!r})")
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
