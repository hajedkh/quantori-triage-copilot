"""LangGraph wiring.

Primary graph path with one interrupt at the human gate:

    supervisor → knowledge → cheminformatics → critic → human_gate → export

At the gate, the operator can either approve+export or request another
iteration pass:

    diversifier → cheminformatics → critic → human_gate

Each agent from agents.py is a node. The human_gate node calls interrupt() to
pause the graph and wait for approval (see resume() below for how approval
is actually handled). Heavy data lives on the Run object (looked up by
run_id); the graph state only carries the run_id, so the checkpointer stays
tiny.
"""

from __future__ import annotations
import logging
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.checkpoint.memory import MemorySaver

from . import agents, export
from .store import RUNS, emit

logger = logging.getLogger(__name__)


class GState(TypedDict):
    run_id: str


def _run(state: GState):
    return RUNS[state["run_id"]]


async def supervisor_node(state: GState):
    await agents.supervisor(_run(state))
    return {}


async def knowledge_node(state: GState):
    await agents.knowledge(_run(state))
    return {}


async def cheminformatics_node(state: GState):
    await agents.cheminformatics(_run(state))
    return {}


async def critic_node(state: GState):
    await agents.critic(_run(state))
    return {}


async def diversifier_node(state: GState):
    await agents.diversifier(_run(state))
    return {}


async def human_gate_node(state: GState):
    run = _run(state)
    run.status = "awaiting_approval"
    emit(run, {"type": "awaiting_approval"})
    run.queue.put_nowait(None)  # close the SSE stream; frontend waits for approval
    interrupt({"awaiting": "human approval"})
    return {}


async def export_node(state: GState):
    run = _run(state)
    from pathlib import Path

    runs_dir = Path(__file__).resolve().parent.parent / "runs"
    export.export_all(
        runs_dir / run.id,
        run.target_name,
        run.dossier,
        run.citations,
        run.ranked,
        run.metric,
        screen_stats=run.screen_stats,
        grounding=run.grounding,
        provenance=run.provenance,
    )
    run.status = "exported"
    return {}


def build_graph():
    g = StateGraph(GState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("cheminformatics", cheminformatics_node)
    g.add_node("critic", critic_node)
    g.add_node("diversifier", diversifier_node)
    g.add_node("human_gate", human_gate_node)
    g.add_node("export", export_node)

    g.add_edge(START, "supervisor")
    g.add_edge("supervisor", "knowledge")
    g.add_edge("knowledge", "cheminformatics")
    g.add_edge("cheminformatics", "critic")
    g.add_edge("critic", "human_gate")
    g.add_edge("human_gate", "export")
    g.add_edge("export", END)

    # checkpointer is required for interrupt/resume
    return g.compile(checkpointer=MemorySaver())


GRAPH = build_graph()


def _config(run_id: str):
    return {"configurable": {"thread_id": run_id}}


async def run_until_gate(run_id: str):
    """Run the graph until it pauses at the human gate."""
    run = RUNS[run_id]
    try:
        async for _ in GRAPH.astream({"run_id": run_id}, _config(run_id)):
            pass
    except Exception as e:  # never leave the stream hanging
        # Full traceback to the server log (uvicorn stderr); the browser only
        # gets a one-line summary. This is the swallow point that hid the
        # earlier NameError — logger.exception() records where it blew up.
        logger.exception("run %s aborted before the human gate", run_id)
        emit(
            run,
            {
                "type": "log",
                "agent": "supervisor",
                "payload": f"Error: {type(e).__name__}: {e}",
            },
        )
        emit(run, {"type": "awaiting_approval"})
        run.status = "awaiting_approval"
        run.queue.put_nowait(None)


async def resume(run_id: str):
    """Resume past the human gate -> runs export.
    Command(resume=True) doesn't work on this langgraph + Python 3.9 combo —
    async node execution needs 3.11+ to propagate its config contextvar, so
    interrupt()'s resume path raises. Everything export needs is already on
    the Run object by this point, so we just write the files directly instead
    of resuming the graph. export_node and the edges after human_gate are
    still defined but no longer reached.
    """
    run = RUNS[run_id]
    import asyncio
    from pathlib import Path

    runs_dir = Path(__file__).resolve().parent.parent / "runs"
    try:
        # Offload the CPU-bound export (SDF 3D embedding, CSV/report writing)
        # to a worker thread so it doesn't block the event loop while the
        # /approve request is in flight.
        await asyncio.to_thread(
            export.export_all,
            runs_dir / run.id,
            run.target_name,
            run.dossier,
            run.citations,
            run.ranked,
            run.metric,
            run.screen_stats,
            run.grounding,
            run.provenance,
        )
    except Exception:
        # Surfaces as a 500 to the /approve caller; log the traceback so the
        # cause is visible server-side rather than just the HTTP error.
        logger.exception("export failed for run %s", run_id)
        raise
    run.status = "exported"


async def diversify_and_retriage(run_id: str, opts: dict | None = None):
    """Run one operator-requested iteration:
    diversifier -> cheminformatics -> critic -> human gate.

    Mirrors the post-critic branch requested at the human gate without relying
    on langgraph resume (same Python 3.9 contextvar limitation as resume()).
    """
    run = RUNS[run_id]
    run.status = "running"
    if opts:
        run.diversify_mode = opts.get("mode", run.diversify_mode)
        run.diversify_lambda = opts.get("lam", run.diversify_lambda)
        run.diversify_cluster_cutoff = opts.get("cutoff", run.diversify_cluster_cutoff)
        run.diversify_max_generated = opts.get(
            "max_generated", run.diversify_max_generated
        )
    emit(
        run,
        {
            "type": "log",
            "agent": "supervisor",
            "payload": "Operator requested diversification pass — rerunning cheminformatics and critic.",
        },
    )

    try:
        await agents.diversifier(run)

        new_candidates = list(getattr(run, "diversified_candidates", []) or [])
        if not new_candidates:
            emit(
                run,
                {
                    "type": "log",
                    "agent": "supervisor",
                    "payload": "Diversifier produced no new compounds; keeping current shortlist.",
                },
            )
            return

        base_survivors = list(run.survivors)
        base_stats = dict(run.screen_stats or {})
        base_candidates = list(run.candidates)

        # Important: this rerun screens only newly generated compounds.
        run.candidates = new_candidates
        await agents.cheminformatics(run)

        new_survivors = list(run.survivors)
        old_by_smiles = {s.get("smiles"): s for s in base_survivors}
        added_survivors = [
            s for s in new_survivors if s.get("smiles") not in old_by_smiles
        ]
        merged_survivors = base_survivors + added_survivors
        run.survivors = merged_survivors
        run.candidates = base_candidates

        new_stats = dict(run.screen_stats or {})
        merged_stats = {
            "input": base_stats.get("input", 0) + len(new_candidates),
            "invalid": base_stats.get("invalid", 0) + new_stats.get("invalid", 0),
            "lipinski_dropped": base_stats.get("lipinski_dropped", 0)
            + new_stats.get("lipinski_dropped", 0),
            "pains_dropped": base_stats.get("pains_dropped", 0)
            + new_stats.get("pains_dropped", 0),
            "qed_errors": base_stats.get("qed_errors", 0)
            + new_stats.get("qed_errors", 0),
            "after_lipinski": base_stats.get("after_lipinski", 0)
            + new_stats.get("after_lipinski", 0),
            "survivors": len(merged_survivors),
            "diversified_added": len(new_candidates),
            "diversified_survivors_added": len(added_survivors),
        }
        run.screen_stats = merged_stats

        emit(
            run,
            {
                "type": "funnel",
                "payload": {
                    "input": merged_stats["input"],
                    "filtered": merged_stats["survivors"],
                    "ranked": None,
                    "diversified_added": merged_stats["diversified_added"],
                },
            },
        )

        await agents.critic(run)
    except Exception as e:
        logger.exception("run %s diversification pass failed", run_id)
        emit(
            run,
            {
                "type": "log",
                "agent": "supervisor",
                "payload": f"Error: {type(e).__name__}: {e}",
            },
        )
    finally:
        run.status = "awaiting_approval"
        emit(run, {"type": "awaiting_approval"})
        run.queue.put_nowait(None)
