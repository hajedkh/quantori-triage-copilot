"""The four triage agents as async functions.

Each agent reads/writes the shared Run object and emits events matching the
frontend contract. graph.py wraps each of these as a LangGraph node — the
event shapes are the same whether run directly or through the graph.

Cheminformatics and Critic are real tool-calling agents (see tools.py/loop.py)
that decide their own strategy. Supervisor/Knowledge are plain async
functions with no scripted delays.
"""

from __future__ import annotations
import json
import re
from . import chem, sources, llm, tools, loop
from .store import emit


# ---------------- Supervisor ----------------
async def supervisor(run):
    emit(run, {"type": "agent_start", "agent": "supervisor"})
    emit(run, {"type": "log", "agent": "supervisor", "payload": "Parsing request…"})
    target_id, pref = sources.resolve_target(run.target_name)
    run.target_id = target_id
    emit(run, {"type": "target_resolved", "payload": {"id": target_id}})
    emit(run, {"type": "log", "agent": "supervisor",
               "payload": f"Resolved {run.target_name} → {target_id} · {len(run.candidates)} molecules loaded"})
    emit(run, {"type": "funnel", "payload": {"input": len(run.candidates), "filtered": None, "ranked": None}})
    emit(run, {"type": "agent_done", "agent": "supervisor"})


# ---------------- Knowledge ----------------
async def knowledge(run):
    emit(run, {"type": "agent_start", "agent": "knowledge"})
    emit(run, {"type": "log", "agent": "knowledge", "payload": "Querying ChEMBL for known actives (pChEMBL ≥ 6)…"})
    actives, active_ids = sources.get_known_actives(run.target_id)
    run.known_actives = actives
    run.active_ids = active_ids
    emit(run, {"type": "log", "agent": "knowledge",
               "payload": f"Retrieved {len(actives)} known actives · fetching PubMed abstracts…"})
    abstracts = sources.pubmed_abstracts(run.target_name)
    emit(run, {"type": "log", "agent": "knowledge",
               "payload": f"Retrieved {len(abstracts)} abstracts · writing cited dossier…"})
    dossier, citations = await llm.build_dossier(run.target_name, abstracts)
    run.dossier = dossier
    run.citations = citations
    # stream the dossier word by word — the text already exists in full,
    # this just replays it as a typing effect for the frontend
    for tok in dossier.split(" "):
        emit(run, {"type": "dossier_token", "payload": tok + " "})
    emit(run, {"type": "citations", "payload": citations})
    emit(run, {"type": "agent_done", "agent": "knowledge"})


# ---------------- Cheminformatics (real tool-calling agent) ----------------
async def cheminformatics(run):
    emit(run, {"type": "agent_start", "agent": "cheminformatics"})
    emit(run, {"type": "log", "agent": "cheminformatics",
               "payload": "Agentic filtering — deciding thresholds and strategy…"})

    system_prompt = (
        "You are the Cheminformatics agent in a drug-discovery triage pipeline. "
        "You have RDKit-backed tools to filter and inspect a candidate molecule "
        "library against known active binders for a target. Decide which filters "
        "to apply and at what thresholds yourself — there is no fixed recipe or "
        "required order. Justify every choice you make. Aim to end with roughly "
        "20-200 survivors: far fewer means you over-filtered and should loosen "
        "thresholds and re-screen; far more means you under-filtered and should "
        "tighten. "
        "Act through your tools — do not describe a plan in prose before doing "
        "anything. Your very first reply must be a tool call, not text. Only once "
        "you have actually screened the library and are satisfied with the "
        "survivor set should you stop calling tools and reply with a short "
        "plain-text summary of your final filtering strategy and why you chose it."
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

    cfg = llm.load_config("ollama")  # high call volume, cheap, self-hosted
    summary = await loop.run_tool_loop(
        run, "cheminformatics", system_prompt, user_msg, tools.CHEM_TOOLS, executor, cfg=cfg
    )

    emit(run, {"type": "log", "agent": "cheminformatics",
               "payload": summary.strip() if summary else "Filtering complete."})
    emit(run, {"type": "agent_done", "agent": "cheminformatics"})


# ---------------- Critic / Ranking (real tool-calling agent) ----------------
def _parse_critic_json(text: str):
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"```\s*$", "", t)
        t = t.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def critic(run):
    emit(run, {"type": "agent_start", "agent": "critic"})
    emit(run, {"type": "log", "agent": "critic",
               "payload": "Agentic scoring — gathering evidence before ranking…"})

    system_prompt = (
        "You are the Critic/Ranking agent in a drug-discovery triage pipeline. "
        "Survivors from the Cheminformatics agent's filtering are ready to be "
        "scored and ranked. You MUST fetch evidence with your tools before "
        "scoring — never score blind. Check the filtering results, choose score "
        "weights (similarity / QED / PAINS-clean) you can justify, then call "
        "rank_survivors to produce the final ranking; the numeric scores are "
        "computed by RDKit underneath, not by you — you choose weights and write "
        "the explanation, the science is never hallucinated. "
        "When done, reply with ONLY a JSON object, no markdown fences, no prose, "
        "matching exactly this shape: "
        '{"ranked":[{"smiles":str,"score":float,"confidence":str,"reason":str,'
        '"evidence_used":[str]}],"yield_ok":bool,"replan_reason":str_or_null}. '
        '"evidence_used" names which tool results backed your judgment for that '
        'row. "yield_ok" is false only if the ranked set is clearly too small or '
        'too large to be useful — explain why in "replan_reason", else null.'
    )
    user_msg = (
        f"{len(run.survivors)} survivors are ready to rank for target "
        f"{run.target_name}. Gather evidence, choose weights, score, and justify."
    )

    async def executor(name, args):
        return await tools.execute_tool(run, name, args)

    cfg = llm.load_config("gateway")  # one call, quality matters — cost-routing story

    raw = await loop.run_tool_loop(
        run, "critic", system_prompt, user_msg, tools.CRITIC_TOOLS, executor, cfg=cfg
    )
    parsed = _parse_critic_json(raw)

    if parsed is None:
        emit(run, {"type": "log", "agent": "critic",
                   "payload": "Output wasn't valid JSON — retrying with a stricter instruction…"})
        raw2 = await loop.run_tool_loop(
            run, "critic",
            system_prompt + "\n\nReturn ONLY valid JSON. No markdown fences. No commentary before or after.",
            "Your previous reply was not valid JSON. Return ONLY the JSON object now.",
            tools.CRITIC_TOOLS, executor, cfg=cfg, max_iters=2,
        )
        parsed = _parse_critic_json(raw2)

    # run.ranked (from the rank_survivors tool call) is the real, RDKit-scored
    # list — the model's JSON only adds evidence notes to it, never its own numbers.
    if run.ranked:
        ranked = run.ranked
        if parsed and isinstance(parsed.get("ranked"), list):
            evidence_by_smiles = {
                r.get("smiles"): r.get("evidence_used")
                for r in parsed["ranked"]
                if isinstance(r, dict) and r.get("smiles")
            }
            for r in ranked:
                ev = evidence_by_smiles.get(r["smiles"])
                if ev:
                    r["evidence_used"] = ev
        if parsed and parsed.get("replan_reason"):
            emit(run, {"type": "log", "agent": "critic",
                       "payload": f"Model flagged yield concern: {parsed['replan_reason']}"})
    else:
        emit(run, {"type": "log", "agent": "critic",
                   "payload": "Agent never produced a ranking — falling back to deterministic chem.rank()."})
        ranked = chem.rank(run.survivors, run.active_ids)
        run.ranked = ranked

    n_in = len(run.candidates)
    emit(run, {"type": "funnel", "payload": {"input": n_in, "filtered": len(run.survivors), "ranked": len(ranked)}})
    emit(run, {"type": "ranked", "payload": ranked})
    dropped = len(run.survivors) - len(ranked)
    emit(run, {"type": "log", "agent": "critic",
               "payload": f"Dropped {dropped} low-confidence · top {len(ranked)} ranked"})

    total_actives = sum(1 for c in run.candidates if c.get("label"))
    recovered = sum(1 for r in ranked if r["is_known_active"])
    if total_actives > 0:
        metric = {"recovered": recovered, "total_actives": total_actives,
                  "top_n": len(ranked), "screened": n_in}
        run.metric = metric
        emit(run, {"type": "metric", "payload": metric})

    emit(run, {"type": "agent_done", "agent": "critic"})
