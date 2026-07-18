"""Shared run store — the Run object, the registry, and the emit helper.

Kept in its own module so main.py, agents.py, and graph.py can all import it
without circular imports.
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field


@dataclass
class Run:
    id: str
    target_name: str = ""  # empty until resolve_target (chat) or /run (form) sets it
    candidates: list = field(
        default_factory=list
    )  # [{"smiles": str, "label": bool|None}]
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    status: str = "running"  # setup | running | awaiting_approval | exported
    target_id: str = ""
    known_actives: list = field(default_factory=list)
    active_ids: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    ranked: list = field(default_factory=list)
    dossier: str = ""
    citations: list = field(default_factory=list)
    grounding: dict | None = None  # PMID-grounding report from build_dossier
    metric: dict | None = None
    screen_stats: dict | None = (
        None  # stats dict from the last screen_candidates tool call
    )
    events: list = field(
        default_factory=list
    )  # append-only history of everything emit() has sent
    inbox: list = field(
        default_factory=list
    )  # operator guidance queued for the next loop iteration
    chat_history: list = field(
        default_factory=list
    )  # the copilot's own conversation for this run


RUNS: dict[str, Run] = {}  # run_id -> Run


def emit(run: Run, event: dict) -> None:
    """Push an event onto the run's queue for the SSE stream to drain, and
    (except dossier_token, too noisy to keep) record it on the run so it can
    still be read after the queue has drained it."""
    if event.get("type") != "dossier_token":
        run.events.append(event)
    run.queue.put_nowait(event)
