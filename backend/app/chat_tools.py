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
setup the chat exposes run status plus read-only ranking/profile guidance so
operators can choose a profile before launch.

Every tool here calls chem.py's primitives (parse, descriptors, lipinski_pass,
pains_flag, fingerprint, max_tanimoto, build_active_fps, _confidence) or reads
chem-produced data already on Run — none of them reimplement chemistry, and
none of them touch tools.py or chem.py.

Descriptions are one short sentence each, same discipline as tools.py: verbose
ones make some local models stop calling tools and narrate instead.
"""

from __future__ import annotations

import asyncio
import math

from . import chem
from .config import load_tool_config
from .store import emit

_TOOL_CFG = load_tool_config()
_MAX_EXAMPLES = _TOOL_CFG.max_examples


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
                    "profile": {
                        "type": "string",
                        "enum": ["balanced", "quality", "explore", "strict"],
                        "description": "Ranking profile preset to use.",
                    },
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


def get_ranking_options_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_ranking_options",
            "description": "Get available ranking profiles and what each optimizes for.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_methods_summary_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_methods_summary",
            "description": (
                "Get a concise methods summary: public databases, filtering, "
                "scoring, diversity, and export steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "overview",
                            "databases",
                            "filtering",
                            "scoring",
                            "diversity",
                            "export",
                        ],
                        "description": "Optional topic focus. Defaults to overview.",
                    }
                },
                "required": [],
            },
        },
    }


def get_export_status_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_export_status",
            "description": "Get current export stage, status, and artifact readiness.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_crossref_summary_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_crossref_summary",
            "description": "Get cross-reference coverage summary for ChEMBL and PubChem.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_compound_crossref_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_compound_crossref",
            "description": "Get ChEMBL and PubChem IDs for one ranked molecule or SMILES.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {
                        "type": "integer",
                        "description": "1-indexed rank in the shortlist.",
                    },
                    "smiles": {
                        "type": "string",
                        "description": "Canonical or input SMILES.",
                    },
                },
                "required": [],
            },
        },
    }


def compare_ranking_profiles_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "compare_ranking_profiles",
            "description": "Compare two ranking profiles and preview top-list changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_a": {
                        "type": "string",
                        "enum": ["balanced", "quality", "explore", "strict"],
                        "description": "First profile.",
                    },
                    "profile_b": {
                        "type": "string",
                        "enum": ["balanced", "quality", "explore", "strict"],
                        "description": "Second profile.",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many ranks to compare. Default 20.",
                    },
                },
                "required": ["profile_a", "profile_b"],
            },
        },
    }


def get_filter_thresholds_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_filter_thresholds",
            "description": "Get screening threshold settings and gate policy used in the run.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def get_report_metadata_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_report_metadata",
            "description": "Get report provenance, methods summary, and artifact metadata.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def explain_score_components_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "explain_score_components",
            "description": "Explain score component contributions for one ranked molecule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {
                        "type": "integer",
                        "description": "1-indexed rank in the shortlist.",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["balanced", "quality", "explore", "strict"],
                        "description": "Optional profile override.",
                    },
                },
                "required": ["rank"],
            },
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


SETUP_TOOLS = [
    get_run_status_schema(),
    get_ranking_options_schema(),
]

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
    get_ranking_options_schema(),
    get_methods_summary_schema(),
    get_export_status_schema(),
    get_crossref_summary_schema(),
    get_compound_crossref_schema(),
    compare_ranking_profiles_schema(),
    get_filter_thresholds_schema(),
    get_report_metadata_schema(),
    explain_score_components_schema(),
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
    st = run.screen_stats or {}
    return {
        "status": run.status,
        "target": run.target_name,
        "ranking_profile": getattr(run, "ranking_profile", "balanced"),
        "funnel": {
            "input": st.get("input", len(run.candidates)),
            "survivors": len(run.survivors),
            "ranked": len(run.ranked),
            "diversified_added": st.get("diversified_added", 0),
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
                "compound_id": r.get("compound_id", "") or "",
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
        "diversified_added": st.get("diversified_added", 0),
        "invalid_smiles": st.get("invalid", 0),
        "lipinski_dropped": st.get("lipinski_dropped", 0),
        "pains_dropped": st.get("pains_dropped", 0),
        "qed_errors": st.get("qed_errors", 0),
        "survivors": st.get("survivors"),
        "diversified_survivors_added": st.get("diversified_survivors_added", 0),
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


def _get_ranking_options(run) -> dict:
    baseline = chem.DEFAULT_WEIGHTS
    profiles = chem.RANKING_PROFILES

    def _top_changes(weights: dict) -> list[dict]:
        deltas = []
        for key, base_val in baseline.items():
            val = weights.get(key, base_val)
            delta = round(val - base_val, 3)
            if abs(delta) > 0:
                deltas.append({"component": key, "delta_vs_balanced": delta})
        deltas.sort(key=lambda d: abs(d["delta_vs_balanced"]), reverse=True)
        return deltas[:4]

    profile_details = {}
    for name, cfg in profiles.items():
        w = cfg["weights"]
        profile_details[name] = {
            "score_mode": cfg.get("score_mode", "enhanced"),
            "confidence_policy": cfg.get("confidence_policy", name),
            "weights": {
                "similarity": round(w["similarity"], 3),
                "breadth": round(w["breadth"], 3),
                "qed": round(w["qed"], 3),
                "sa": round(w["sa"], 3),
                "penalty_lipinski": round(w["penalty_lipinski"], 3),
                "penalty_pains": round(w["penalty_pains"], 3),
            },
            "largest_changes_vs_balanced": _top_changes(w),
        }

    return {
        "selected_profile": getattr(run, "ranking_profile", "balanced"),
        "baseline_profile": "balanced",
        "profile_summaries": {
            "balanced": {
                "summary": "Balanced quality and developability.",
                "best_for": "default triage and general-purpose runs",
            },
            "quality": {
                "summary": "Higher weight on known-active similarity and breadth.",
                "best_for": "lead-followup around known active chemotypes",
            },
            "explore": {
                "summary": "More novelty-friendly with softer liability penalties.",
                "best_for": "scaffold hopping and broader exploration",
            },
            "strict": {
                "summary": "Harsher liability penalties and tighter confidence gating.",
                "best_for": "conservative shortlists for downstream synthesis",
            },
        },
        "profile_details": profile_details,
    }


def _get_methods_summary(run, topic: str = "overview") -> dict:
    topic = (topic or "overview").strip().lower()
    if topic not in {
        "overview",
        "databases",
        "filtering",
        "scoring",
        "diversity",
        "export",
    }:
        topic = "overview"

    methods = {
        "databases": {
            "chembl": {
                "uses": [
                    "Resolve target identifier and preferred target name",
                    "Fetch known actives (activity records) for similarity anchoring",
                    "Cross-reference shortlisted compounds by InChIKey in export",
                ]
            },
            "pubmed": {
                "uses": [
                    "Fetch abstracts for target dossier generation",
                    "Attach citation-backed claims to the report",
                ]
            },
            "pubchem": {
                "uses": [
                    "Cross-reference shortlisted compounds by InChIKey during export",
                ]
            },
        },
        "filtering": {
            "gates": [
                "SMILES parse validity",
                "Lipinski Ro5 (default allows up to one violation)",
                "PAINS alerts",
                "QED/descriptor sanity checks",
            ],
            "source": "RDKit-backed screening in cheminformatics agent tools",
        },
        "scoring": {
            "selected_profile": getattr(run, "ranking_profile", "balanced"),
            "components": [
                "max similarity to known actives",
                "top-k similarity breadth",
                "QED",
                "synthetic accessibility",
                "Lipinski penalty",
                "PAINS penalty",
            ],
            "output": "Ranked shortlist with score, confidence, and rationale",
        },
        "diversity": {
            "shortlist_modes": ["off", "scaffold", "mmr", "cluster"],
            "human_gate": "Operator may approve export or run diversification + retriage",
        },
        "export": {
            "artifacts": ["shortlist.csv", "shortlist.sdf", "report.md"],
            "cross_reference": ["ChEMBL ID", "PubChem CID"],
            "sdf_note": "SDF generation includes 3D conformer embedding with RDKit",
        },
    }

    if topic == "overview":
        return {
            "topic": "overview",
            "status": run.status,
            "target": run.target_name,
            "summary": {
                "public_databases": ["ChEMBL", "PubMed", "PubChem"],
                "pipeline_steps": [
                    "target resolution + knowledge gathering",
                    "cheminformatics filtering",
                    "critic scoring/ranking",
                    "human approval or diversification rerun",
                    "export (CSV/SDF/report)",
                ],
            },
            "details": methods,
        }

    return {"topic": topic, "details": methods[topic]}


def _get_export_status(run) -> dict:
    progress = run.export_progress or {
        "status": "idle",
        "stage": "none",
        "message": "Export has not started.",
    }
    artifacts = run.export_artifacts or {}
    return {
        "run_status": run.status,
        "export": progress,
        "artifacts": artifacts,
        "artifacts_ready": bool(artifacts),
    }


def _get_crossref_summary(run) -> dict:
    summary = run.xref_summary
    if summary:
        return {
            "available": True,
            "source": "export",
            **summary,
        }

    if run.xref_by_smiles:
        rows = list(run.xref_by_smiles.values())
        return {
            "available": True,
            "source": "in_memory",
            "requested": len(rows),
            "queried": sum(1 for r in rows if r.get("crossref_queried")),
            "chembl_found": sum(1 for r in rows if r.get("chembl_id")),
            "pubchem_found": sum(1 for r in rows if r.get("pubchem_cid")),
        }

    return {
        "available": False,
        "message": "Cross-reference summary not available before export.",
    }


def _resolve_smiles_for_crossref(run, rank: int | None, smiles: str | None) -> str:
    if rank is not None:
        if rank < 1 or rank > len(run.ranked):
            raise ValueError(
                f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
            )
        return run.ranked[rank - 1]["smiles"]
    if smiles:
        can = chem.canonical(smiles)
        return can or smiles
    raise ValueError("Provide either rank or smiles.")


def _get_compound_crossref(
    run,
    rank: int | None = None,
    smiles: str | None = None,
) -> dict:
    target_smiles = _resolve_smiles_for_crossref(run, rank, smiles)
    ref = run.xref_by_smiles.get(target_smiles)
    if not ref:
        return {
            "available": False,
            "smiles": target_smiles,
            "message": "Cross-reference not available for this molecule yet.",
        }

    out = {
        "available": True,
        "smiles": target_smiles,
        "chembl_id": ref.get("chembl_id", "") or "",
        "pubchem_cid": ref.get("pubchem_cid", "") or "",
        "crossref_queried": bool(ref.get("crossref_queried", False)),
    }
    if rank is not None:
        out["rank"] = rank
    return out


def _compare_ranking_profiles(
    run,
    profile_a: str,
    profile_b: str,
    top_n: int = 20,
) -> dict:
    a_ranked, _, a_profile = _score_survivors(run, {}, profile=profile_a)
    b_ranked, _, b_profile = _score_survivors(run, {}, profile=profile_b)

    top_n = max(1, int(top_n or 20))
    a_top = a_ranked[:top_n]
    b_top = b_ranked[:top_n]
    a_smiles = {r["smiles"] for r in a_top}
    b_smiles = {r["smiles"] for r in b_top}

    return {
        "profile_a": a_profile,
        "profile_b": b_profile,
        "top_n": top_n,
        "entering_profile_b": list(b_smiles - a_smiles)[:_MAX_EXAMPLES],
        "leaving_profile_b": list(a_smiles - b_smiles)[:_MAX_EXAMPLES],
        "avg_score_profile_a": round(
            sum(r["score"] for r in a_top) / max(1, len(a_top)), 3
        ),
        "avg_score_profile_b": round(
            sum(r["score"] for r in b_top) / max(1, len(b_top)), 3
        ),
    }


def _get_filter_thresholds(run) -> dict:
    st = run.screen_stats or {}
    return {
        "thresholds": {
            "mw_max": st.get("mw_max", 500),
            "logp_max": st.get("logp_max", 5),
            "hbd_max": st.get("hbd_max", 5),
            "hba_max": st.get("hba_max", 10),
            "max_lipinski_violations": st.get("max_violations", 1),
            "pains_policy": "drop_if_any_alert",
        },
        "source": "last screening pass",
    }


def _get_report_metadata(run) -> dict:
    return {
        "target": run.target_name,
        "run_status": run.status,
        "provenance": run.provenance,
        "export": {
            "status": (run.export_progress or {}).get("status", "idle"),
            "stage": (run.export_progress or {}).get("stage", "none"),
            "artifacts": run.export_artifacts or {},
        },
        "methods": _get_methods_summary(run, "overview"),
    }


def _explain_score_components(
    run,
    rank: int,
    profile: str | None = None,
) -> dict:
    if rank < 1 or rank > len(run.ranked):
        raise ValueError(
            f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
        )

    ranked_row = run.ranked[rank - 1]
    smi = ranked_row["smiles"]
    survivor = next((s for s in run.survivors if s.get("smiles") == smi), None)
    if not survivor:
        raise ValueError("could not locate survivor record for ranked molecule")

    selected_profile = (profile or getattr(run, "ranking_profile", "balanced")).lower()
    cfg = chem.resolve_ranking_profile(selected_profile)
    weights = cfg["weights"]
    score_mode = cfg["score_mode"]
    confidence_policy = cfg["confidence_policy"]

    sim = survivor["max_similarity"]
    breadth = survivor.get("top_k_avg", sim)
    qed = 0.45 if survivor.get("qed") is None else survivor["qed"]
    sa = survivor.get("sa_score")
    sa_term = 0.5 if sa is None else 1.0 - sa
    n_viol = len(survivor.get("lipinski_violations", []))
    n_pains = survivor.get("n_pains_alerts", 1 if survivor.get("pains_flag") else 0)

    if (score_mode or "classic").lower() == "enhanced":
        sim_term = 1.0 - math.exp(-2.2 * max(0.0, sim))
        breadth_term = 1.0 - math.exp(-1.8 * max(0.0, breadth))
        lip_pen = weights["penalty_lipinski"] * (n_viol**1.2)
        pains_pen = weights["penalty_pains"] * (n_pains**1.35)
        positive = sum(
            [
                weights["similarity"] * sim_term,
                weights["breadth"] * breadth_term,
                weights["qed"] * qed,
                weights["sa"] * sa_term,
            ]
        )
        raw = positive - lip_pen - pains_pen
        score = round(
            max(0.0, min(1.0 / (1.0 + math.exp(-(raw - 0.32) / 0.14)), 1.0)), 3
        )
    else:
        sim_term = sim
        breadth_term = breadth
        lip_pen = weights["penalty_lipinski"] * n_viol
        pains_pen = weights["penalty_pains"] * n_pains
        score = chem.compute_score(survivor, weights, score_mode=score_mode)

    confidence = chem._confidence(
        score,
        sim,
        survivor.get("lipinski_pass", True),
        confidence_policy,
    )

    return {
        "rank": rank,
        "smiles": smi,
        "profile": selected_profile,
        "score_mode": score_mode,
        "score": score,
        "confidence": confidence,
        "components": {
            "similarity_term": round(sim_term, 3),
            "breadth_term": round(breadth_term, 3),
            "qed_term": round(qed, 3),
            "sa_term": round(sa_term, 3),
            "lipinski_penalty": round(lip_pen, 3),
            "pains_penalty": round(pains_pen, 3),
        },
        "weights": weights,
    }


def _explain(run, rank: int) -> dict:
    if rank < 1 or rank > len(run.ranked):
        raise ValueError(
            f"rank {rank} out of range — only {len(run.ranked)} ranked results exist"
        )
    r = run.ranked[rank - 1]
    return {
        "rank": r["rank"],
        "compound_id": r.get("compound_id", "") or "",
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
    run,
    weights: dict,
    profile: str | None = None,
    scaffold_pattern=None,
    scaffold_bonus: float = 0.15,
):
    """Shared scoring pass for rerank/focus_scaffold. Uses chem.compute_score
    (the same six-component formula the Critic's rank_survivors uses) and the
    current three-arg chem._confidence(score, sim, lipinski_pass), so a chat
    re-rank produces scores directly comparable to the pipeline's. Side-effect
    free: builds and returns a candidate list without writing run.ranked."""
    selected_profile = (profile or getattr(run, "ranking_profile", "balanced")).lower()
    profile_cfg = chem.resolve_ranking_profile(selected_profile)
    merged_weights = {**profile_cfg["weights"], **(weights or {})}
    score_mode = profile_cfg["score_mode"]
    confidence_policy = profile_cfg["confidence_policy"]

    scored = []
    matched_count = 0
    for s in run.survivors:
        score = chem.compute_score(s, merged_weights, score_mode=score_mode)
        sim = s["max_similarity"]
        matched = False
        if scaffold_pattern is not None:
            mol = chem.parse(s["smiles"])
            matched = mol is not None and mol.HasSubstructMatch(scaffold_pattern)
            if matched:
                matched_count += 1
                score = round(min(1.0, score + scaffold_bonus), 3)
        conf = chem._confidence(score, sim, s["lipinski_pass"], confidence_policy)
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
                "compound_id": s.get("compound_id", "") or "",
                "smiles": s["smiles"],
                "score": score,
                "confidence": conf,
                "reason": reason,
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
    top_n = len(run.ranked) or 20
    top = scored[:top_n]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    return top, matched_count, selected_profile


def _apply_ranked(run, new_ranked: list) -> None:
    run.ranked = new_ranked
    n_in = (run.screen_stats or {}).get("input", len(run.candidates))
    emit(
        run,
        {
            "type": "funnel",
            "payload": {
                "input": n_in,
                "filtered": len(run.survivors),
                "ranked": len(new_ranked),
                "diversified_added": (run.screen_stats or {}).get(
                    "diversified_added", 0
                ),
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


def _rerank(
    run,
    weights: dict = None,
    profile: str | None = None,
    confirmed: bool = False,
) -> dict:
    if run.status != "awaiting_approval":
        raise ValueError(
            f"rerank is only available at the approval gate (status={run.status!r})"
        )
    weights = weights or {}
    new_ranked, _, selected_profile = _score_survivors(run, weights, profile=profile)
    entering, leaving = _top20_diff(run.ranked, new_ranked)

    if not confirmed:
        return {
            "preview": True,
            "profile": selected_profile,
            "weights": {
                **chem.resolve_ranking_profile(selected_profile)["weights"],
                **(weights or {}),
            },
            "entering_top20": entering,
            "leaving_top20": leaving,
            "message": "Not applied. Call again with confirmed=true to commit.",
        }

    run.ranking_profile = selected_profile
    _apply_ranked(run, new_ranked)
    return {
        "applied": True,
        "profile": selected_profile,
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

    new_ranked, matched_count, selected_profile = _score_survivors(
        run,
        {},
        profile=getattr(run, "ranking_profile", "balanced"),
        scaffold_pattern=pattern,
    )
    entering, leaving = _top20_diff(run.ranked, new_ranked)

    if not confirmed:
        return {
            "preview": True,
            "smarts": smarts,
            "profile": selected_profile,
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
    if name == "get_ranking_options":
        return _get_ranking_options(run)
    if name == "get_methods_summary":
        return _get_methods_summary(run, **args)
    if name == "get_export_status":
        return _get_export_status(run)
    if name == "get_crossref_summary":
        return _get_crossref_summary(run)
    if name == "get_compound_crossref":
        return _get_compound_crossref(run, **args)
    if name == "compare_ranking_profiles":
        return _compare_ranking_profiles(run, **args)
    if name == "get_filter_thresholds":
        return _get_filter_thresholds(run)
    if name == "get_report_metadata":
        return _get_report_metadata(run)
    if name == "explain_score_components":
        return _explain_score_components(run, **args)
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
