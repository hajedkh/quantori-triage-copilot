"""Tool schemas + implementations for the chat copilot.

Not a new agent — the copilot is driven by the exact same run_tool_loop from
loop.py, just with this tool set instead of tools.CHEM_TOOLS/CRITIC_TOOLS.
No RAG, no vector store, no embeddings: run.ranked/run.survivors/run.events
are plain Python objects already sitting in memory; these tools just read
(or, at the gate, carefully mutate) them.

Two groups, gated by run.status:
  - read tools        (get_run_status, get_agent_trace, get_ranked, get_molecule,
                        get_dossier, explain, why_not, similar_to)
  - mutate tools       (rerank, focus_scaffold) — only at the approval gate,
                        and only ever write via the preview-then-confirm contract:
                        confirmed=false computes and returns a preview without
                        writing anything; confirmed=true commits and emits the
                        same "ranked"/"funnel" events the pipeline itself uses.

Starting a run is intentionally NOT something the chat can do — that only
happens via the classic form (target + file upload + "Run triage"). During
setup the chat only has get_run_status; the system prompt (main.py) tells it
to point the operator at the form if asked how to begin.

Every tool here calls chem.py's primitives (parse, descriptors, lipinski_pass,
pains_flag, fingerprint, max_tanimoto, build_active_fps, _confidence) or reads
chem-produced data already on Run — none of them reimplement chemistry, and
none of them touch tools.py or chem.py.

Descriptions are one short sentence each, same discipline as tools.py: verbose
ones make some local models stop calling tools and narrate instead.
"""

from __future__ import annotations

import asyncio

from . import chem
from .store import emit

_MAX_EXAMPLES = 3


# ---------------------------------------------------------------- schemas --


def get_run_status_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_run_status",
            "description": "Get the run's current status, target, funnel counts, and which agents finished.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_agent_trace_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_agent_trace",
            "description": "Get the real, recorded tool-call trace of what the agents have actually done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "How many recent tool calls. Default 15.",
                    }
                },
                "required": [],
            },
        },
    }


def get_ranked_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_ranked",
            "description": "Get the current ranked shortlist, compact fields only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "How many rows. Default 20.",
                    }
                },
                "required": [],
            },
        },
    }


def get_molecule_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_molecule",
            "description": "Get the full descriptor record for one ranked molecule by its rank.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {
                        "type": "integer",
                        "description": "1-indexed rank in the shortlist.",
                    }
                },
                "required": ["rank"],
            },
        },
    }


def get_dossier_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_dossier",
            "description": "Get the target dossier text and its citations.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def explain_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "explain",
            "description": "Get the exact stored reason a ranked molecule scored where it did — never re-reason this yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {
                        "type": "integer",
                        "description": "1-indexed rank in the shortlist.",
                    }
                },
                "required": ["rank"],
            },
        },
    }


def why_not_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "why_not",
            "description": "Find the exact filter gate that rejected (or didn't reject) a specific SMILES.",
            "parameters": {
                "type": "object",
                "properties": {
                    "smiles": {"type": "string", "description": "The SMILES to check."}
                },
                "required": ["smiles"],
            },
        },
    }


def similar_to_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "similar_to",
            "description": "Get the nearest-neighbour survivors to one ranked molecule by Tanimoto similarity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {
                        "type": "integer",
                        "description": "1-indexed rank of the reference molecule.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "How many neighbours. Default 5.",
                    },
                },
                "required": ["rank"],
            },
        },
    }


def rerank_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "rerank",
            "description": "Preview or apply a re-score of survivors with different scoring weights.",
            "parameters": {
                "type": "object",
                "properties": {
                    "weights": {
                        "type": "object",
                        "description": "Score weights. Defaults: similarity 0.40, breadth 0.15, qed 0.20, sa 0.15, penalty_lipinski 0.05, penalty_pains 0.03.",
                        "properties": {
                            "similarity": {"type": "number"},
                            "breadth": {"type": "number"},
                            "qed": {"type": "number"},
                            "sa": {"type": "number"},
                            "penalty_lipinski": {"type": "number"},
                            "penalty_pains": {"type": "number"},
                        },
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set true to apply. Default false previews.",
                    },
                },
                "required": [],
            },
        },
    }


def diversify_shortlist_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "diversify_shortlist",
            "description": "Preview or apply a chemotype-diversity rerank of the shortlist (scaffold, mmr, or cluster).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["off", "scaffold", "mmr", "cluster"],
                        "description": "off=pure score; scaffold=Bemis-Murcko round-robin; mmr=maximal marginal relevance; cluster=Butina.",
                    },
                    "lam": {
                        "type": "number",
                        "description": "MMR trade-off 0-1 (1=quality, 0=spread). Only used for mmr. Default 0.7.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set true to apply. Default false previews.",
                    },
                },
                "required": ["mode"],
            },
        },
    }


def get_funnel_breakdown_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_funnel_breakdown",
            "description": "Get the screening drop breakdown: how many were invalid, Lipinski-dropped, PAINS-dropped, and survived.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_scaffold_summary_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_scaffold_summary",
            "description": "Get how many distinct Bemis-Murcko scaffolds are in the shortlist and which dominate.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_metric_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_metric",
            "description": "Get the held-out validation metric: known actives recovered in the ranked top-N.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def focus_scaffold_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "focus_scaffold",
            "description": "Preview or apply a scoring bonus for survivors matching a SMARTS substructure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "smarts": {
                        "type": "string",
                        "description": "A SMARTS substructure pattern.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set true to apply. Default false previews.",
                    },
                },
                "required": ["smarts"],
            },
        },
    }


SETUP_TOOLS = [get_run_status_schema()]

READ_TOOLS = [
    get_run_status_schema(),
    get_agent_trace_schema(),
    get_ranked_schema(),
    get_molecule_schema(),
    get_dossier_schema(),
    explain_schema(),
    why_not_schema(),
    similar_to_schema(),
    get_funnel_breakdown_schema(),
    get_scaffold_summary_schema(),
    get_metric_schema(),
]

MUTATE_TOOLS = [
    rerank_schema(),
    focus_scaffold_schema(),
    diversify_shortlist_schema(),
]


def tools_for_status(status: str) -> list:
    """Only expose tools that make sense for where the run actually is."""
    if status == "setup":
        return SETUP_TOOLS
    if status == "awaiting_approval":
        return READ_TOOLS + MUTATE_TOOLS
    return READ_TOOLS  # running, exported


# ---------------------------------------------------------- implementations --
# (read)


def _get_run_status(run) -> dict:
    agents_done = [e.get("agent") for e in run.events if e.get("type") == "agent_done"]
    return {
        "status": run.status,
        "target": run.target_name,
        "funnel": {
            "input": len(run.candidates),
            "survivors": len(run.survivors),
            "ranked": len(run.ranked),
        },
        "agents_done": agents_done,
    }


def _get_agent_trace(run, n: int = 15) -> dict:
    calls = [e for e in run.events if e.get("type") == "tool_call"]
    tail = calls[-n:]
    return {
        "count": len(tail),
        "trace": [
            {
                "iteration": e["payload"]["iteration"],
                "agent": e.get("agent"),
                "tool": e["payload"]["tool"],
                "args": e["payload"]["args"],
                "result_summary": e["payload"]["result_summary"],
                "status": e["payload"]["status"],
            }
            for e in tail
        ],
    }


def _get_ranked(run, n: int = 250) -> dict:
    subset = run.ranked[:n]
    return {
        "count": len(subset),
        "total_ranked": len(run.ranked),
        "results": [
            {
                "rank": r["rank"],
                "smiles": r["smiles"],
                "score": r["score"],
                "confidence": r["confidence"],
                "is_known_active": r["is_known_active"],
            }
            for r in subset
        ],
    }


def _get_molecule(run, rank: int) -> dict:
    if rank < 1 or rank > len(run.ranked):
        raise ValueError(
            f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
        )
    smiles = run.ranked[rank - 1]["smiles"]
    for s in run.survivors:
        if s["smiles"] == smiles:
            return dict(s)
    raise ValueError(f"ranked molecule at rank {rank} not found in survivors")


def _get_dossier(run) -> dict:
    return {"dossier": run.dossier, "citations": run.citations}


def _get_funnel_breakdown(run) -> dict:
    st = run.screen_stats
    if not st:
        return {"available": False, "message": "no screen has run yet"}
    return {
        "available": True,
        "input": st.get("input"),
        "invalid_smiles": st.get("invalid", 0),
        "lipinski_dropped": st.get("lipinski_dropped", 0),
        "pains_dropped": st.get("pains_dropped", 0),
        "qed_errors": st.get("qed_errors", 0),
        "survivors": st.get("survivors"),
    }


def _get_scaffold_summary(run) -> dict:
    if not run.ranked:
        return {"available": False, "message": "no ranked shortlist yet"}
    from collections import Counter

    counts = Counter(r.get("scaffold", "") for r in run.ranked if r.get("scaffold"))
    top = counts.most_common(_MAX_EXAMPLES)
    out = {
        "ranked_count": len(run.ranked),
        "distinct_scaffolds": len(counts),
        "top_scaffolds": [{"scaffold": s, "count": n} for s, n in top],
    }
    if run.diversity_stats:
        out["diversity_pass"] = run.diversity_stats
    return out


def _get_metric(run) -> dict:
    if not run.metric:
        return {
            "available": False,
            "message": "validation metric not computed (no labelled actives in input)",
        }
    return dict(run.metric)


def _explain(run, rank: int) -> dict:
    if rank < 1 or rank > len(run.ranked):
        raise ValueError(
            f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
        )
    r = run.ranked[rank - 1]
    return {
        "rank": r["rank"],
        "smiles": r["smiles"],
        "reason": r["reason"],
        "evidence_used": r.get("evidence_used", []),
    }


def _why_not(run, smiles: str) -> dict:
    mol = chem.parse(smiles)
    if mol is None:
        return {"verdict": "unparseable", "smiles": smiles}

    stats = run.screen_stats or {}
    mw_max = stats.get("mw_max", 500)
    logp_max = stats.get("logp_max", 5)
    hbd_max = stats.get("hbd_max", 5)
    hba_max = stats.get("hba_max", 10)

    d = chem.descriptors(mol)
    lip = chem.lipinski_pass(
        d, mw_max=mw_max, logp_max=logp_max, hbd_max=hbd_max, hba_max=hba_max
    )
    if not lip:
        return {
            "verdict": "dropped at Lipinski",
            "mw": d["mw"],
            "logp": d["logp"],
            "hbd": d["hbd"],
            "hba": d["hba"],
            "thresholds": {
                "mw_max": mw_max,
                "logp_max": logp_max,
                "hbd_max": hbd_max,
                "hba_max": hba_max,
            },
        }

    if chem.pains_flag(mol):
        return {"verdict": "PAINS flagged"}

    active_fps, _ = chem.build_active_fps(run.known_actives)
    prof = chem.similarity_profile(chem.fingerprint(mol), active_fps)
    sim = prof["max_sim"]
    s_like = {
        "max_similarity": sim,
        "top_k_avg": prof["top_k_avg"],
        "qed": None,
        "sa_score": chem.sa_score(mol),
        "lipinski_violations": chem.lipinski_violations(d),
        "n_pains_alerts": 0,
    }
    score = chem.compute_score(s_like)
    conf = chem._confidence(score, sim, lip)
    if conf == "Low":
        return {"verdict": "confidence Low", "similarity": sim, "score": score}

    canonical = chem.Chem.MolToSmiles(mol)
    for r in run.ranked:
        if r["smiles"] == canonical:
            return {"verdict": "ranked", "rank": r["rank"], "score": r["score"]}

    return {"verdict": "survived, outside top_n", "similarity": sim, "confidence": conf}


def _similar_to(run, rank: int, k: int = 5) -> dict:
    if rank < 1 or rank > len(run.ranked):
        raise ValueError(
            f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
        )
    target_smiles = run.ranked[rank - 1]["smiles"]
    target_mol = chem.parse(target_smiles)
    if target_mol is None:
        raise ValueError(f"could not re-parse ranked molecule at rank {rank}")
    target_fp = chem.fingerprint(target_mol)

    scored = []
    for s in run.survivors:
        if s["smiles"] == target_smiles:
            continue
        mol = chem.parse(s["smiles"])
        if mol is None:
            continue
        sim, _ = chem.max_tanimoto(target_fp, [chem.fingerprint(mol)])
        scored.append({"smiles": s["smiles"], "similarity": sim})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return {"rank": rank, "smiles": target_smiles, "neighbours": scored[:k]}


# ---------------------------------------------------------- implementations --
# (mutate — gate-only, preview-then-confirm)


def _score_survivors(
    run, weights: dict, scaffold_pattern=None, scaffold_bonus: float = 0.15
):
    """Shared scoring pass for rerank/focus_scaffold. Uses chem.compute_score
    (the same six-component formula the Critic's rank_survivors uses) and the
    current three-arg chem._confidence(score, sim, lipinski_pass), so a chat
    re-rank produces scores directly comparable to the pipeline's. Side-effect
    free: builds and returns a candidate list without writing run.ranked."""
    scored = []
    matched_count = 0
    for s in run.survivors:
        score = chem.compute_score(s, weights)  # handles qed=None internally
        sim = s["max_similarity"]
        matched = False
        if scaffold_pattern is not None:
            mol = chem.parse(s["smiles"])
            matched = mol is not None and mol.HasSubstructMatch(scaffold_pattern)
            if matched:
                matched_count += 1
                score = round(min(1.0, score + scaffold_bonus), 3)
        conf = chem._confidence(score, sim, s["lipinski_pass"])
        if conf == "Low":
            continue
        idx = (
            int(s["nearest_active"].split("#")[-1])
            if "#" in s["nearest_active"]
            else -1
        )
        nearest = (
            run.active_ids[idx]
            if 0 <= idx < len(run.active_ids)
            else s["nearest_active"]
        )
        reason = chem._build_reason(s, score, nearest) + (
            "; matches scaffold" if matched else ""
        )
        scored.append(
            {
                "smiles": s["smiles"],
                "score": score,
                "confidence": conf,
                "reason": reason,
                "nearest_active": nearest,
                "max_similarity": sim,
                "scaffold": s.get("scaffold", ""),
                "sa_score": s.get("sa_score"),
                "is_known_active": (
                    bool(s.get("label")) if s.get("label") is not None else False
                ),
            }
        )
    scored.sort(key=lambda r: r["score"], reverse=True)
    top_n = len(run.ranked) or 20
    top = scored[:top_n]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    return top, matched_count


def _apply_ranked(run, new_ranked: list) -> None:
    run.ranked = new_ranked
    n_in = len(run.candidates)
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": n_in,
                "filtered": len(run.survivors),
                "ranked": len(new_ranked),
            },
        },
    )
    emit(run, {"type": "ranked", "payload": new_ranked})


def _top20_diff(old_ranked: list, new_ranked: list) -> tuple:
    old_top20 = {r["smiles"] for r in old_ranked[:20]}
    new_top20 = {r["smiles"] for r in new_ranked[:20]}
    entering = list(new_top20 - old_top20)[:_MAX_EXAMPLES]
    leaving = list(old_top20 - new_top20)[:_MAX_EXAMPLES]
    return entering, leaving


def _rerank(run, weights: dict = None, confirmed: bool = False) -> dict:
    if run.status != "awaiting_approval":
        raise ValueError(
            f"rerank is only available at the approval gate (status={run.status!r})"
        )
    weights = weights or {}
    new_ranked, _ = _score_survivors(run, weights)
    entering, leaving = _top20_diff(run.ranked, new_ranked)

    if not confirmed:
        return {
            "preview": True,
            "weights": {**chem.DEFAULT_WEIGHTS, **(weights or {})},
            "entering_top20": entering,
            "leaving_top20": leaving,
            "message": "Not applied. Call again with confirmed=true to commit.",
        }

    _apply_ranked(run, new_ranked)
    return {
        "applied": True,
        "ranked_count": len(new_ranked),
        "entering_top20": entering,
        "leaving_top20": leaving,
    }


def _focus_scaffold(run, smarts: str, confirmed: bool = False) -> dict:
    if run.status != "awaiting_approval":
        raise ValueError(
            f"focus_scaffold is only available at the approval gate (status={run.status!r})"
        )
    pattern = chem.Chem.MolFromSmarts(smarts)
    if pattern is None:
        raise ValueError(f"invalid SMARTS pattern: {smarts!r}")

    new_ranked, matched_count = _score_survivors(run, {}, scaffold_pattern=pattern)
    entering, leaving = _top20_diff(run.ranked, new_ranked)

    if not confirmed:
        return {
            "preview": True,
            "smarts": smarts,
            "matched_count": matched_count,
            "entering_top20": entering,
            "leaving_top20": leaving,
            "message": "Not applied. Call again with confirmed=true to commit.",
        }

    _apply_ranked(run, new_ranked)
    return {
        "applied": True,
        "smarts": smarts,
        "matched_count": matched_count,
        "ranked_count": len(new_ranked),
        "entering_top20": entering,
        "leaving_top20": leaving,
    }


def _diversify_shortlist(
    run, mode: str = "scaffold", lam: float = 0.7, confirmed: bool = False
) -> dict:
    if run.status != "awaiting_approval":
        raise ValueError(
            f"diversify_shortlist is only available at the approval gate (status={run.status!r})"
        )
    if not run.ranked:
        raise ValueError("no ranked shortlist to diversify yet")
    if mode not in chem.DIVERSITY_MODES:
        raise ValueError(
            f"unknown mode {mode!r} — expected one of {list(chem.DIVERSITY_MODES)}"
        )

    new_ranked, stats = chem.diversify(
        run.ranked, mode=mode, lam=lam, top_n=len(run.ranked)
    )
    entering, leaving = _top20_diff(run.ranked, new_ranked)

    if not confirmed:
        return {
            "preview": True,
            "mode": mode,
            "lambda": stats["lambda"],
            "distinct_scaffolds": stats["n_scaffolds"],
            "n_clusters": stats["n_clusters"],
            "entering_top20": entering,
            "leaving_top20": leaving,
            "message": "Not applied. Call again with confirmed=true to commit.",
        }

    _apply_ranked(run, new_ranked)
    run.diversity_stats = stats
    emit(run, {"type": "diversity", "payload": stats})
    return {
        "applied": True,
        "mode": mode,
        "distinct_scaffolds": stats["n_scaffolds"],
        "n_clusters": stats["n_clusters"],
        "ranked_count": len(new_ranked),
        "entering_top20": entering,
        "leaving_top20": leaving,
    }


# ------------------------------------------------------------------ dispatch --


async def execute_chat_tool(run, name: str, args: dict) -> dict:
    """Dispatch a chat-tool call by name. Network/CPU-bound read-only work runs
    in a thread so it can't stall the event loop (and, mid-run, the pipeline's
    own SSE stream); tools that call emit() or asyncio.create_task() run
    directly on the event loop, same as tools.execute_tool already does."""
    args = args or {}
    if name == "get_run_status":
        return _get_run_status(run)
    if name == "get_agent_trace":
        return _get_agent_trace(run, **args)
    if name == "get_ranked":
        return _get_ranked(run, **args)
    if name == "get_molecule":
        return _get_molecule(run, **args)
    if name == "get_dossier":
        return _get_dossier(run)
    if name == "get_funnel_breakdown":
        return _get_funnel_breakdown(run)
    if name == "get_scaffold_summary":
        return _get_scaffold_summary(run)
    if name == "get_metric":
        return _get_metric(run)
    if name == "explain":
        return _explain(run, **args)
    if name == "why_not":
        return await asyncio.to_thread(_why_not, run, **args)
    if name == "similar_to":
        return await asyncio.to_thread(_similar_to, run, **args)
    if name == "rerank":
        return _rerank(run, **args)
    if name == "focus_scaffold":
        return _focus_scaffold(run, **args)
    if name == "diversify_shortlist":
        return _diversify_shortlist(run, **args)
    raise ValueError(f"unknown chat tool: {name!r}")
