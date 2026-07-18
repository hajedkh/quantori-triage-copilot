"""OpenAI-style tool schemas + implementations wrapping chem.py.

chem.py stays untouched RDKit (only lipinski_pass() got optional threshold
params). screen()/rank() aren't called from here — this module composes their
smaller building blocks directly (parse, descriptors, lipinski_pass,
pains_flag, fingerprint, max_tanimoto, build_active_fps, QED, _confidence),
which is what lets these tools expose tunable thresholds/weights without
touching screen()/rank() themselves.

Each tool does real RDKit work, writes the full result onto the Run object,
and returns a compact summary (counts + at most 3 examples) — the model acts
by reference against these summaries, never against the full candidate list.
"""

from __future__ import annotations

from . import chem
from .store import emit

_MAX_EXAMPLES = 3
_MAX_BATCH = 10


# ---------------------------------------------------------------- schemas --

# Keep descriptions to one short sentence — verbose ones make some local
# models (mistral, tested directly) silently stop calling tools and narrate
# instead. Reasoning detail belongs in the system prompt, not here.


def screen_candidates_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "screen_candidates",
            "description": "Filter the candidate library by drug-likeness/PAINS and score survivors by similarity to known actives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mw_max": {
                        "type": "number",
                        "description": "Max molecular weight. Default 500.",
                    },
                    "logp_max": {
                        "type": "number",
                        "description": "Max logP. Default 5.",
                    },
                    "hbd_max": {
                        "type": "integer",
                        "description": "Max H-bond donors. Default 5.",
                    },
                    "hba_max": {
                        "type": "integer",
                        "description": "Max H-bond acceptors. Default 10.",
                    },
                    "apply_pains": {
                        "type": "boolean",
                        "description": "Drop PAINS-flagged structures. Default true.",
                    },
                    "max_violations": {
                        "type": "integer",
                        "description": "Max Lipinski violations tolerated. Default 1 (true Ro5).",
                    },
                },
                "required": [],
            },
        },
    }


def compute_descriptors_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "compute_descriptors",
            "description": "Get MW/logP/HBD/HBA for specific SMILES you name, to spot-check them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "smiles_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"Up to {_MAX_BATCH} SMILES to inspect.",
                    }
                },
                "required": ["smiles_list"],
            },
        },
    }


def similarity_to_actives_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "similarity_to_actives",
            "description": "Get Tanimoto similarity of specific SMILES you name to the known active binders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "smiles_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"Up to {_MAX_BATCH} SMILES to score.",
                    }
                },
                "required": ["smiles_list"],
            },
        },
    }


def rank_survivors_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "rank_survivors",
            "description": "Score and rank survivors into the final shortlist. Uses similarity, breadth, QED, SA, and graduated penalties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "Max results to keep. Default 600.",
                    },
                    "diversity": {
                        "type": "boolean",
                        "description": "Scaffold-aware diversity reranking. Default true.",
                    },
                    "weights": {
                        "type": "object",
                        "description": "Score component weights. Defaults: similarity 0.40, breadth 0.15, qed 0.20, sa 0.15, penalty_lipinski 0.05/violation, penalty_pains 0.03/alert.",
                        "properties": {
                            "similarity": {"type": "number"},
                            "breadth": {"type": "number"},
                            "qed": {"type": "number"},
                            "sa": {"type": "number"},
                            "penalty_lipinski": {"type": "number"},
                            "penalty_pains": {"type": "number"},
                        },
                    },
                },
                "required": [],
            },
        },
    }


def get_funnel_stats_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_funnel_stats",
            "description": "Get the counts from the last screen_candidates call (input/invalid/dropped/survivors).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def submit_ranking_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "submit_ranking",
            "description": "Submit the final ranked shortlist with per-compound evidence notes. Call AFTER rank_survivors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_notes": {
                        "type": "array",
                        "description": "Per-compound evidence for top hits.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "smiles": {
                                    "type": "string",
                                    "description": "Canonical SMILES of the compound.",
                                },
                                "evidence_used": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Which tool results backed your judgment.",
                                },
                            },
                            "required": ["smiles", "evidence_used"],
                        },
                    },
                    "yield_ok": {
                        "type": "boolean",
                        "description": "True if ranked set size is reasonable.",
                    },
                    "replan_reason": {
                        "type": ["string", "null"],
                        "description": "Why yield is problematic, if yield_ok is false.",
                    },
                },
                "required": ["evidence_notes", "yield_ok"],
            },
        },
    }


CHEM_TOOLS = [
    screen_candidates_schema(),
    compute_descriptors_schema(),
    similarity_to_actives_schema(),
    get_funnel_stats_schema(),
]

CRITIC_TOOLS = [
    compute_descriptors_schema(),
    get_funnel_stats_schema(),
    rank_survivors_schema(),
    submit_ranking_schema(),
]


# ---------------------------------------------------------- implementations --


def _screen_candidates(
    run,
    mw_max: float = 500,
    logp_max: float = 5,
    hbd_max: int = 5,
    hba_max: int = 10,
    apply_pains: bool = True,
    max_violations: int = 1,
) -> dict:
    thresholds = {
        "mw_max": mw_max,
        "logp_max": logp_max,
        "hbd_max": hbd_max,
        "hba_max": hba_max,
        "apply_pains": apply_pains,
        "max_violations": max_violations,
    }

    # Reuse a persistent pool across re-screens — the agent often calls
    # screen_candidates 2-3 times with adjusted thresholds. Creating the
    # pool once avoids repeated fork + active-fps-rebuild overhead.
    if getattr(run, "_screen_pool", None) is None:
        run._screen_pool = chem.ScreenPool(run.known_actives)

    survivors, stats, invalid_examples = chem.screen_parallel(
        run.candidates,
        run.known_actives,
        thresholds,
        pool=run._screen_pool,
    )

    prior_added = (run.screen_stats or {}).get("diversified_added", 0)
    run.survivors = survivors
    run.screen_stats = {**stats, "diversified_added": prior_added}

    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": stats["input"],
                "filtered": stats["survivors"],
                "ranked": None,
                "diversified_added": prior_added,
            },
        },
    )

    summary = {
        "thresholds": thresholds,
        "stats": stats,
        "survivor_examples": [
            {
                "smiles": s["smiles"],
                "max_similarity": s["max_similarity"],
                "qed": s["qed"],
                "lipinski_violations": s["lipinski_violations"],
            }
            for s in survivors[:_MAX_EXAMPLES]
        ],
    }
    if invalid_examples:
        summary["invalid_examples"] = invalid_examples
        summary["hint"] = (
            f"{stats['invalid']} candidate(s) could not be parsed as SMILES and were "
            "already excluded from survivors. Call compute_descriptors on one of "
            "invalid_examples if you want to double-check before finalizing."
        )
    return summary


def _compute_descriptors(smiles_list: list) -> dict:
    if not smiles_list:
        raise ValueError("smiles_list must not be empty")
    smiles_list = smiles_list[:_MAX_BATCH]
    results, bad = [], []
    for smi in smiles_list:
        mol = chem.parse(smi)
        if mol is None:
            bad.append(smi)
            continue
        results.append({"smiles": smi, **chem.descriptors(mol)})
    if bad:
        raise ValueError(
            f"could not parse {len(bad)} of {len(smiles_list)} SMILES: {bad}"
        )
    return {"count": len(results), "results": results}


def _similarity_to_actives(run, smiles_list: list) -> dict:
    if not smiles_list:
        raise ValueError("smiles_list must not be empty")
    smiles_list = smiles_list[:_MAX_BATCH]
    # Cache active fps on run — build_active_fps re-parses all actives each
    # call, which is wasteful when the agent spot-checks multiple times.
    if getattr(run, "_cached_active_fps", None) is None:
        fps, ids = chem.build_active_fps(run.known_actives)
        run._cached_active_fps = fps
        run._cached_active_ids = ids
    active_fps = run._cached_active_fps
    active_ids = run._cached_active_ids
    results, bad = [], []
    for smi in smiles_list:
        mol = chem.parse(smi)
        if mol is None:
            bad.append(smi)
            continue
        sim, idx = chem.max_tanimoto(chem.fingerprint(mol), active_fps)
        nearest = active_ids[idx] if 0 <= idx < len(active_ids) else None
        results.append(
            {"smiles": smi, "max_similarity": sim, "nearest_active_index": nearest}
        )
    if bad:
        raise ValueError(
            f"could not parse {len(bad)} of {len(smiles_list)} SMILES: {bad}"
        )
    return {"count": len(results), "results": results}


def _rank_survivors(
    run, top_n: int = 600, weights: dict = None, diversity: bool = True
) -> dict:
    w = {**chem.DEFAULT_WEIGHTS, **(weights or {})}

    survivors = run.survivors
    active_ids = run.active_ids

    scored = []
    for s in survivors:
        score = chem.compute_score(s, w)
        sim = s["max_similarity"]
        conf = chem._confidence(score, sim, s["lipinski_pass"])
        if conf == "Low":
            continue
        idx = (
            int(s["nearest_active"].split("#")[-1])
            if "#" in s["nearest_active"]
            else -1
        )
        nearest = active_ids[idx] if 0 <= idx < len(active_ids) else s["nearest_active"]
        scored.append(
            {
                "smiles": s["smiles"],
                "score": score,
                "confidence": conf,
                "reason": chem._build_reason(s, score, nearest),
                "nearest_active": nearest,
                "max_similarity": sim,
                "scaffold": s.get("scaffold", ""),
                "sa_score": s.get("sa_score"),
                "is_diversified_generated": bool(
                    s.get("is_diversified_generated", False)
                ),
                "is_known_active": (
                    bool(s.get("label")) if s.get("label") is not None else False
                ),
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)

    if diversity:
        top = chem.diversity_rerank(scored, top_n)
    else:
        top = scored[:top_n]

    for i, r in enumerate(top, 1):
        r["rank"] = i

    run.ranked = top

    n_in = len(run.candidates)
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": (run.screen_stats or {}).get("input", n_in),
                "filtered": len(survivors),
                "ranked": len(top),
                "diversified_added": (run.screen_stats or {}).get(
                    "diversified_added", 0
                ),
            },
        },
    )

    scores = [r["score"] for r in top]
    n_scaffolds = len({r.get("scaffold", "") for r in top})
    return {
        "weights_used": w,
        "diversity_enabled": diversity,
        "ranked_count": len(top),
        "unique_scaffolds": n_scaffolds,
        "score_range": [min(scores), max(scores)] if scores else [None, None],
        "top_examples": top[:_MAX_EXAMPLES],
    }


def _get_funnel_stats(run) -> dict:
    if run.screen_stats is None:
        raise ValueError("no screen has been run yet — call screen_candidates first")
    return dict(run.screen_stats)


def _submit_ranking(
    run, evidence_notes: list = None, yield_ok: bool = True, replan_reason: str = None
) -> dict:
    """Accept the critic's structured evidence and attach it to run.ranked
    using canonical SMILES matching. Returns match stats so the model can
    see if any notes failed to attach."""
    if not run.ranked:
        raise ValueError("no ranking exists yet — call rank_survivors first")

    matched = 0
    unmatched_smiles: list[str] = []

    if evidence_notes:
        # Build lookup by canonical SMILES for robust matching
        ranked_by_smi = {}
        for r in run.ranked:
            canonical_smi = chem.canonical(r["smiles"]) or r["smiles"]
            ranked_by_smi[canonical_smi] = r

        for note in evidence_notes:
            raw_smi = note.get("smiles", "")
            canonical_smi = chem.canonical(raw_smi) or raw_smi
            target_row = ranked_by_smi.get(canonical_smi)
            if target_row:
                target_row["evidence_used"] = note.get("evidence_used", [])
                matched += 1
            else:
                unmatched_smiles.append(raw_smi)

    run.critic_yield_ok = yield_ok
    run.critic_replan_reason = replan_reason

    return {
        "accepted": True,
        "evidence_matched": matched,
        "evidence_unmatched": len(unmatched_smiles),
        "unmatched_examples": unmatched_smiles[:_MAX_EXAMPLES],
        "yield_ok": yield_ok,
        "replan_reason": replan_reason,
        "ranked_count": len(run.ranked),
    }


async def execute_tool(run, name: str, args: dict) -> dict:
    """Dispatch a model-requested tool call by name. Sync chem work under an
    async signature so loop.py can `await` every tool uniformly."""
    args = args or {}
    if name == "screen_candidates":
        return _screen_candidates(run, **args)
    if name == "compute_descriptors":
        return _compute_descriptors(**args)
    if name == "similarity_to_actives":
        return _similarity_to_actives(run, **args)
    if name == "rank_survivors":
        return _rank_survivors(run, **args)
    if name == "get_funnel_stats":
        return _get_funnel_stats(run)
    if name == "submit_ranking":
        return _submit_ranking(run, **args)
    raise ValueError(f"unknown tool: {name!r}")
