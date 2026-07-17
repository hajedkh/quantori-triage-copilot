"""The four triage agents as async functions.

Each agent reads/writes the shared Run object and emits events matching the
frontend contract. graph.py wraps each of these as a LangGraph node — the
event shapes are the same whether run directly or through the graph.

Cheminformatics and Critic are real tool-calling agents (see tools.py/loop.py)
that decide their own strategy. Supervisor/Knowledge are plain async
functions with no scripted delays.
"""

from __future__ import annotations
from . import chem, sources, llm, tools, loop
from .store import emit


# ---------------- Supervisor ----------------
async def supervisor(run):
    emit(run, {"type": "agent_start", "agent": "supervisor"})
    emit(run, {"type": "log", "agent": "supervisor", "payload": "Parsing request…"})
    target_id, pref = sources.resolve_target(run.target_name)
    run.target_id = target_id
    emit(run, {"type": "target_resolved", "payload": {"id": target_id}})
    emit(
        run,
        {
            "type": "log",
            "agent": "supervisor",
            "payload": f"Resolved {run.target_name} → {target_id} · {len(run.candidates)} molecules loaded",
        },
    )
    emit(
        run,
        {
            "type": "funnel",
            "payload": {"input": len(run.candidates), "filtered": None, "ranked": None},
        },
    )
    emit(run, {"type": "agent_done", "agent": "supervisor"})


# ---------------- Knowledge ----------------
async def knowledge(run):
    emit(run, {"type": "agent_start", "agent": "knowledge"})
    emit(
        run,
        {
            "type": "log",
            "agent": "knowledge",
            "payload": "Querying ChEMBL for known actives (pChEMBL ≥ 6)…",
        },
    )
    actives, active_ids = sources.get_known_actives(run.target_id)
    run.known_actives = actives
    run.active_ids = active_ids
    emit(
        run,
        {
            "type": "log",
            "agent": "knowledge",
            "payload": f"Retrieved {len(actives)} known actives · fetching PubMed abstracts…",
        },
    )
    abstracts = sources.pubmed_abstracts(run.target_name)
    emit(
        run,
        {
            "type": "log",
            "agent": "knowledge",
            "payload": f"Retrieved {len(abstracts)} abstracts · writing cited dossier…",
        },
    )
    dossier, citations, grounding = await llm.build_dossier(run.target_name, abstracts)
    run.dossier = dossier
    run.citations = citations
    run.grounding = grounding
    if grounding.get("ungrounded"):
        emit(
            run,
            {
                "type": "log",
                "agent": "knowledge",
                "payload": f"Warning: {len(grounding['ungrounded'])} citation(s) could not be grounded to "
                + "provided sources.",
            },
        )
    # stream the dossier word by word — the text already exists in full,
    # this just replays it as a typing effect for the frontend
    for tok in dossier.split(" "):
        emit(run, {"type": "dossier_token", "payload": tok + " "})
    emit(run, {"type": "citations", "payload": citations})
    emit(run, {"type": "agent_done", "agent": "knowledge"})


# ---------------- Cheminformatics (real tool-calling agent) ----------------
async def cheminformatics(run):
    emit(run, {"type": "agent_start", "agent": "cheminformatics"})
    emit(
        run,
        {
            "type": "log",
            "agent": "cheminformatics",
            "payload": "Agentic filtering — deciding thresholds and strategy…",
        },
    )

    system_prompt = (
        "You are the Cheminformatics agent in a drug-discovery triage pipeline. "
        "You have RDKit-backed tools to filter and inspect a candidate molecule "
        "library against known active binders for a target. Your tools handle "
        "Lipinski Ro5 (allowing up to 1 violation by default, as per the real "
        "rule — many marketed drugs have one violation), PAINS substructure "
        "alerts (with specific alert names for interpretability), QED, and "
        "Tanimoto similarity via Morgan fingerprints (radius 2, 2048-bit, "
        "chirality-aware).\n\n"
        "Decide which filters to apply and at what thresholds yourself — there "
        "is no fixed recipe or required order. Justify every choice you make "
        "with reference to the target class and candidate library properties. "
        "For example, if the target is a kinase, you might note that kinase "
        "inhibitors sometimes exceed MW 500 and relax that threshold; if the "
        "library is fragment-like, default Lipinski may be too permissive.\n\n"
        "Aim to end with roughly 20-200 survivors: far fewer means you likely "
        "over-filtered (loosen and re-screen); far more means under-filtered "
        "(tighten). However, if the data genuinely calls for a count outside "
        "that range, report and justify it rather than gaming thresholds to hit "
        "a headcount.\n\n"
        "After screening, spot-check a few survivors with compute_descriptors "
        "and similarity_to_actives to sanity-check the results before "
        "finalizing. Review the funnel stats to confirm drop reasons are "
        "reasonable.\n\n"
        "Act through your tools — do not describe a plan in prose before doing "
        "anything. Your very first reply must be a tool call, not text. Only "
        "once you have actually screened the library and are satisfied with the "
        "survivor set should you stop calling tools and reply with a short "
        "plain-text summary of your final filtering strategy, the rationale "
        "for each threshold choice, and any concerns about the survivor set."
    )
    user_msg = (
        f"Triage {len(run.candidates)} candidate molecules for target "
        f"{run.target_name} ({run.target_id or 'unresolved'}). "
        f"{len(run.known_actives)} known active binders are available for "
        f"similarity scoring. Begin now by calling a tool — don't just describe "
        f"what you would do."
    )

    async def executor(name, args):
        return await tools.execute_tool(run, name, args)

    cfg = llm.get_active_config()  # whatever provider/model is selected in the UI
    summary = await loop.run_tool_loop(
        run,
        "cheminformatics",
        system_prompt,
        user_msg,
        tools.CHEM_TOOLS,
        executor,
        cfg=cfg,
    )

    emit(
        run,
        {
            "type": "log",
            "agent": "cheminformatics",
            "payload": summary.strip() if summary else "Filtering complete.",
        },
    )
    emit(run, {"type": "agent_done", "agent": "cheminformatics"})


# ---------------- Critic / Ranking (real tool-calling agent) ----------------
async def critic(run):
    emit(run, {"type": "agent_start", "agent": "critic"})
    emit(
        run,
        {
            "type": "log",
            "agent": "critic",
            "payload": "Agentic scoring — gathering evidence before ranking…",
        },
    )

    system_prompt = (
        "You are the Critic/Ranking agent in a drug-discovery triage pipeline. "
        "Survivors from the Cheminformatics agent's filtering are ready to be "
        "scored and ranked. You MUST fetch evidence with your tools before "
        "scoring — never score blind.\n\n"
        "The scoring formula has six components you can weight:\n"
        "- similarity (default 0.40): max Tanimoto to nearest known active\n"
        "- breadth (default 0.15): top-3 average Tanimoto (rewards multi-active coverage)\n"
        "- qed (default 0.20): drug-likeness composite\n"
        "- sa (default 0.15): synthetic accessibility (inverted: easy to make = high score)\n"
        "- penalty_lipinski (default 0.05): deducted per Ro5 violation\n"
        "- penalty_pains (default 0.03): deducted per PAINS alert\n\n"
        "After scoring, diversity reranking clusters by Bemis-Murcko scaffold "
        "so the top-N covers distinct chemotypes. You can disable this.\n\n"
        "Workflow:\n"
        "1. Call get_funnel_stats to understand what the cheminformatics agent did.\n"
        "2. Spot-check a few survivors with compute_descriptors to verify quality.\n"
        "3. Choose weights you can justify for this target class, then call "
        "rank_survivors. The numeric scores are computed by RDKit.\n"
        "4. Call submit_ranking with per-compound evidence notes listing which "
        "tool results backed your judgment. Use canonical SMILES exactly as "
        "they appear in the rank_survivors output. Set yield_ok=false only if "
        "the ranked set is clearly too small or too large to be useful.\n\n"
        "Your very first reply must be a tool call, not text. Stop calling "
        "tools after submit_ranking and reply with a one-sentence summary."
    )
    user_msg = (
        f"{len(run.survivors)} survivors are ready to rank for target "
        f"{run.target_name}. Gather evidence, choose weights, score, and justify."
    )

    async def executor(name, args):
        return await tools.execute_tool(run, name, args)

    cfg = llm.get_active_config()  # whatever provider/model is selected in the UI

    raw = await loop.run_tool_loop(
        run, "critic", system_prompt, user_msg, tools.CRITIC_TOOLS, executor, cfg=cfg
    )

    # The submit_ranking tool (called within the loop) attaches evidence_used
    # to run.ranked using canonical SMILES matching, so we don't need to parse
    # the model's free-text output. If the model never called submit_ranking
    # (e.g. it ran out of iterations), we still have run.ranked from
    # rank_survivors and just log the gap.
    if run.ranked:
        ranked = run.ranked
        if not any(r.get("evidence_used") for r in ranked):
            emit(
                run,
                {
                    "type": "log",
                    "agent": "critic",
                    "payload": "Model did not call submit_ranking — evidence notes not attached.",
                },
            )
        if getattr(run, "critic_replan_reason", None):
            emit(
                run,
                {
                    "type": "log",
                    "agent": "critic",
                    "payload": f"Model flagged yield concern: {run.critic_replan_reason}",
                },
            )
    else:
        emit(
            run,
            {
                "type": "log",
                "agent": "critic",
                "payload": "Agent never produced a ranking — falling back to deterministic chem.rank().",
            },
        )
        ranked = chem.rank(run.survivors, run.active_ids)
        run.ranked = ranked

    n_in = len(run.candidates)
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": n_in,
                "filtered": len(run.survivors),
                "ranked": len(ranked),
            },
        },
    )
    emit(run, {"type": "ranked", "payload": ranked})
    dropped = len(run.survivors) - len(ranked)
    emit(
        run,
        {
            "type": "log",
            "agent": "critic",
            "payload": f"Dropped {dropped} low-confidence · top {len(ranked)} ranked",
        },
    )

    total_actives = sum(1 for c in run.candidates if c.get("label"))
    recovered = sum(1 for r in ranked if r["is_known_active"])
    if total_actives > 0:
        metric = {
            "recovered": recovered,
            "total_actives": total_actives,
            "top_n": len(ranked),
            "screened": n_in,
        }
        run.metric = metric
        emit(run, {"type": "metric", "payload": metric})

    emit(run, {"type": "agent_done", "agent": "critic"})
