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
            "payload": {
                "input": len(run.candidates),
                "filtered": None,
                "ranked": None,
                "diversified_added": 0,
            },
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
    emit(run, {"type": "grounding", "payload": grounding})
    emit(run, {"type": "agent_done", "agent": "knowledge"})


# ---- Prompt variants — compact versions for small local models ----
# Mistral, Llama, and similar models fail with verbose prompts: they stop
# calling tools and narrate instead. These tight prompts have been tested
# with mistral:7b via Ollama.

_CHEM_PROMPT_COMPACT = (
    "You filter candidate molecules using your tools. "
    "Call screen_candidates first with default parameters. "
    "Then call get_funnel_stats to check the result. "
    "If survivors < 20, re-screen with looser thresholds. "
    "If survivors > 500, re-screen with tighter thresholds. "
    "Reply with a short summary when done."
)

_CRITIC_PROMPT_COMPACT = (
    "You rank the filtered molecules. "
    "Call get_funnel_stats first. "
    "Then call rank_survivors with default weights. "
    "Then call submit_ranking with yield_ok=true and empty evidence_notes. "
    "Reply with a short summary when done."
)


# One short, factual clause per profile — derived directly from the weight
# deltas in chem.py::RANKING_PROFILES vs. DEFAULT_WEIGHTS, not invented.
# Purely informational context for Cheminformatics' own threshold judgment;
# chem.py itself is untouched — this never forces a specific threshold.
_PROFILE_HINTS = {
    "balanced": "a standard trade-off across similarity, active-coverage breadth, drug-likeness, and synthesizability",
    "quality": "prioritizing strong similarity to known actives and multi-active coverage over synthesizability nuance",
    "explore": "favoring breadth and diversity of hits, with more lenient drug-likeness/PAINS penalties",
    "strict": "applying stricter drug-likeness and PAINS penalties for cleaner candidates",
}


def _is_small_model(cfg) -> bool:
    """Detect if the active LLM config points to a small local model
    that needs compact prompts."""
    if cfg is None:
        return False
    provider = getattr(cfg, "provider", "")
    model = getattr(cfg, "model", "").lower()
    if provider == "ollama":
        return True
    # Small models by name
    small_patterns = ["mistral", "llama", "phi", "gemma", "qwen2", "tinyllama"]
    return any(p in model for p in small_patterns)


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

    cfg = llm.get_active_config()
    small = _is_small_model(cfg)

    if small:
        system_prompt = _CHEM_PROMPT_COMPACT
    else:
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

    profile = getattr(run, "ranking_profile", "balanced")
    profile_hint = _PROFILE_HINTS.get(profile, _PROFILE_HINTS["balanced"])
    user_msg = (
        f"Triage {len(run.candidates)} candidate molecules for target "
        f"{run.target_name} ({run.target_id or 'unresolved'}). "
        f"{len(run.known_actives)} known active binders are available for "
        f"similarity scoring. The operator selected the '{profile}' ranking "
        f"profile ({profile_hint}) for this run — weigh that when choosing "
        f"filter thresholds. Call screen_candidates now."
    )

    async def executor(name, args):
        return await tools.execute_tool(run, name, args)

    summary = await loop.run_tool_loop(
        run,
        "cheminformatics",
        system_prompt,
        user_msg,
        tools.CHEM_TOOLS,
        executor,
        cfg=cfg,
    )

    # ---- Deterministic fallback ----
    # If the agent loop failed to produce survivors (model errored, narrated
    # instead of calling tools, or never called screen_candidates), run
    # screening with safe defaults so the pipeline always produces output.
    if not run.survivors:
        emit(
            run,
            {
                "type": "log",
                "agent": "cheminformatics",
                "payload": "Agent produced no survivors — running deterministic screen with defaults…",
            },
        )
        fallback_result = await tools.execute_tool(run, "screen_candidates", {})
        emit(
            run,
            {
                "type": "log",
                "agent": "cheminformatics",
                "payload": f"Fallback screen: {fallback_result.get('stats', {}).get('survivors', 0)} survivors",
            },
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
    # Force each critic pass to produce a fresh ranking from current survivors.
    # If the model skips rank_survivors, deterministic fallback will now run
    # instead of silently reusing a stale run.ranked from a prior pass.
    run.ranked = []
    emit(
        run,
        {
            "type": "log",
            "agent": "critic",
            "payload": "Agentic scoring — gathering evidence before ranking…",
        },
    )

    cfg = llm.get_active_config()
    small = _is_small_model(cfg)

    if small:
        system_prompt = _CRITIC_PROMPT_COMPACT
    else:
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
        f"{run.target_name}. Call get_funnel_stats now."
    )

    async def executor(name, args):
        return await tools.execute_tool(run, name, args)

    cfg = llm.get_active_config()  # whatever provider/model is selected in the UI

    await loop.run_tool_loop(
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
        ranked = chem.rank(
            run.survivors,
            run.active_ids,
            profile=getattr(run, "ranking_profile", "balanced"),
        )
        run.ranked = ranked

    n_in = (run.screen_stats or {}).get("input", len(run.candidates))
    diversified_added = (run.screen_stats or {}).get("diversified_added", 0)
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": n_in,
                "filtered": len(run.survivors),
                "ranked": len(ranked),
                "diversified_added": diversified_added,
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


# ---------------- Diversifier ----------------
# A separate agent under the supervisor that runs after the Critic. It takes
# the ranked shortlist and re-selects it for chemotype diversity using the
# mode/parameters selected at rerun time from the human-gate UI prompt
# (run.diversify_mode / diversify_lambda / diversify_cluster_cutoff /
# diversify_max_generated). The chat copilot's diversify_shortlist tool lets
# the operator re-run this interactively at the approval gate with a different
# mode or MMR lambda — same chem.diversify() underneath.
async def diversifier(run):
    emit(run, {"type": "agent_start", "agent": "diversifier"})

    mode = (getattr(run, "diversify_mode", "scaffold") or "scaffold").lower()
    lam = getattr(run, "diversify_lambda", 0.7)
    cutoff = getattr(run, "diversify_cluster_cutoff", 0.35)
    max_generated = getattr(run, "diversify_max_generated", 200)

    if mode not in chem.DIVERSITY_MODES:
        emit(
            run,
            {
                "type": "log",
                "agent": "diversifier",
                "payload": f"Unknown diversity mode {mode!r} — falling back to scaffold.",
            },
        )
        mode = "scaffold"

    if not run.ranked:
        emit(
            run,
            {
                "type": "log",
                "agent": "diversifier",
                "payload": "No ranked shortlist to diversify — skipping.",
            },
        )
        emit(run, {"type": "agent_done", "agent": "diversifier"})
        return

    if mode == "off":
        emit(
            run,
            {
                "type": "log",
                "agent": "diversifier",
                "payload": "Diversity pass disabled by operator — keeping pure score order.",
            },
        )
    else:
        detail = f", λ={lam}" if mode == "mmr" else ""
        emit(
            run,
            {
                "type": "log",
                "agent": "diversifier",
                "payload": f"Re-selecting shortlist for chemotype diversity — mode={mode}{detail}…",
            },
        )

    new_ranked, stats = chem.diversify(
        run.ranked, mode=mode, lam=lam, top_n=len(run.ranked), cutoff=cutoff
    )
    generated, gen_stats = chem.generate_diversified_candidates(
        run.ranked,
        mode=mode,
        lam=lam,
        cutoff=cutoff,
        max_generated=max_generated,
    )
    run.ranked = new_ranked
    run.diversified_candidates = generated
    run.diversified_seed_count = gen_stats.get("seed_count", 0)
    run.diversity_stats = stats
    run.diversity_stats["n_generated"] = len(generated)
    run.diversity_stats["seed_count"] = gen_stats.get("seed_count", 0)

    emit(run, {"type": "diversity", "payload": stats})
    emit(run, {"type": "ranked", "payload": new_ranked})

    n_in = (run.screen_stats or {}).get("input", len(run.candidates))
    diversified_added = (run.screen_stats or {}).get("diversified_added", 0)
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": n_in,
                "filtered": len(run.survivors),
                "ranked": len(new_ranked),
                "diversified_added": diversified_added,
            },
        },
    )

    # Recompute the validation metric — which known actives survive can shift
    # once the set is re-selected for diversity.
    total_actives = sum(1 for c in run.candidates if c.get("label"))
    if total_actives > 0:
        recovered = sum(1 for r in new_ranked if r.get("is_known_active"))
        metric = {
            "recovered": recovered,
            "total_actives": total_actives,
            "top_n": len(new_ranked),
            "screened": n_in,
        }
        run.metric = metric
        emit(run, {"type": "metric", "payload": metric})

    if mode == "off":
        summary = f"Kept top {stats['n_selected']} by score ({stats['n_scaffolds'] or '?'} scaffolds)."
    else:
        cl = f" · {stats['n_clusters']} clusters" if stats.get("n_clusters") else ""
        summary = (
            f"Diversified: {stats['n_scaffolds'] or '?'} distinct scaffolds "
            f"across top {stats['n_selected']}{cl}. Generated {len(generated)} new candidates."
        )
    emit(run, {"type": "log", "agent": "diversifier", "payload": summary})
    emit(run, {"type": "agent_done", "agent": "diversifier"})
