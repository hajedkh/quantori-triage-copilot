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

from . import graph, llm
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
    """Parse an uploaded CSV/SMI into [{smiles,label}]. Flexible about columns."""
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
            400, f"unknown model {body.model!r} for provider {provider!r} — valid: {valid_models}"
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
