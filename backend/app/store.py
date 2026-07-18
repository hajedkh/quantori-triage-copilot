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
    target_name: str
    candidates: list  # [{"smiles": str, "label": bool|None}]
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    status: str = "running"
    target_id: str = ""
    known_actives: list = field(default_factory=list)
    active_ids: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    ranked: list = field(default_factory=list)
    dossier: str = ""
    citations: list = field(default_factory=list)
    metric: dict | None = None
    screen_stats: dict | None = (
        None  # stats dict from the last screen_candidates tool call
    )


RUNS: dict[str, Run] = {}  # run_id -> Run


def emit(run: Run, event: dict) -> None:
    """Push an event onto the run's queue for the SSE stream to drain."""
    run.queue.put_nowait(event)
