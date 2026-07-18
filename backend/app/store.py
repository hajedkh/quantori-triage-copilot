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
    ranking_profile: str = "balanced"  # balanced | quality | explore | strict
    diversify_mode: str = "scaffold"  # off | scaffold | mmr | cluster (operator choice)
    diversify_lambda: float = 0.7  # MMR quality/spread trade-off (1=quality, 0=spread)
    diversify_cluster_cutoff: float = 0.35  # Butina distance cutoff when mode=cluster
    diversify_max_generated: int = 200  # cap on newly generated compounds per rerun
    diversity_stats: dict | None = None  # result of the Diversifier agent's pass
    diversified_candidates: list = field(
        default_factory=list
    )  # newly generated candidates
    diversified_seed_count: int = (
        0  # how many seed molecules were used to generate new compounds
    )
    provenance: dict | None = None  # {timestamp, model, provider} captured at run start
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
    still be read after the queue has drained it.

    Setup-phase runs have no pipeline /stream consumer, so their queue is never
    drained — queueing chat events there would grow it unboundedly over a long
    pre-run chat. Record on events (which the chat endpoint polls) but skip the
    queue until the run actually starts."""
    if event.get("type") != "dossier_token":
        run.events.append(event)
    if run.status != "setup":
        run.queue.put_nowait(event)
