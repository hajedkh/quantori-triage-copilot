"""LangGraph wiring.

Linear graph, one interrupt at the human gate:

    supervisor → knowledge → cheminformatics → critic → human_gate → export

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
    )
    run.status = "exported"
    return {}


def build_graph():
    g = StateGraph(GState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("cheminformatics", cheminformatics_node)
    g.add_node("critic", critic_node)
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
    from pathlib import Path

    runs_dir = Path(__file__).resolve().parent.parent / "runs"
    try:
        export.export_all(
            runs_dir / run.id,
            run.target_name,
            run.dossier,
            run.citations,
            run.ranked,
            run.metric,
            screen_stats=run.screen_stats,
            grounding=run.grounding,
        )
    except Exception:
        # Surfaces as a 500 to the /approve caller; log the traceback so the
        # cause is visible server-side rather than just the HTTP error.
        logger.exception("export failed for run %s", run_id)
        raise
    run.status = "exported"
