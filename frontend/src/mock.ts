// Mock run for EGFR — emits realistic events over ~11s so the whole UI
// is demoable with no backend. Live mode (api.ts) uses the same event shapes.

import type { Citation, LogEvent, RankedMol } from "./types";

export interface StreamEvent {
  type:
  | "agent_start"
  | "agent_done"
  | "log"
  | "target_resolved"
  | "funnel"
  | "dossier_token"
  | "citations"
  | "grounding"
  | "ranked"
  | "metric"
  | "diversity"
  | "tool_call"
  | "steer"
  | "awaiting_approval";
  agent?: LogEvent["agent"];
  payload?: any;
}

const DOSSIER =
  "EGFR (Epidermal Growth Factor Receptor) is a receptor tyrosine kinase that " +
  "signals cells to grow and divide. Activating mutations such as L858R and " +
  "T790M lock the receptor in the “on” state and drive non-small-cell lung " +
  "cancer (NSCLC) [[PMID:15737014]]. First-generation inhibitors gefitinib and " +
  "erlotinib compete at the ATP-binding pocket, but the secondary T790M mutation " +
  "confers acquired resistance [[PMID:15737014]]. Known potent binders share a " +
  "4-anilinoquinazoline scaffold [[PMID:16729045]], which the screen below uses " +
  "as the similarity anchor for triage.";

const CITATIONS: Citation[] = [
  {
    claim: "T790M confers acquired resistance to gefitinib",
    pmid: "15737014",
  },
  {
    claim: "4-anilinoquinazoline scaffold defines potent EGFR inhibitors",
    pmid: "16729045",
  },
];

// A mix of real EGFR-inhibitor SMILES (known actives) and decoy-like molecules.
const RANKED: RankedMol[] = [
  {
    rank: 1,
    smiles: "COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1",
    score: 0.94,
    confidence: "High",
    reason:
      "Tanimoto 0.98 to gefitinib (CHEMBL939), a proven EGFR binder on the 4-anilinoquinazoline scaffold. Passes Lipinski; no PAINS alerts.",
    nearest_active: "CHEMBL939",
    max_similarity: 0.98,
    is_known_active: true,
  },
  {
    rank: 2,
    smiles: "C#Cc1cccc(Nc2ncnc3cc(OCCOC)c(OCCOC)cc23)c1",
    score: 0.91,
    confidence: "High",
    reason:
      "Tanimoto 0.95 to erlotinib (CHEMBL553). Same quinazoline core; drug-like profile, clean PAINS.",
    nearest_active: "CHEMBL553",
    max_similarity: 0.95,
    is_known_active: true,
  },
  {
    rank: 3,
    smiles: "Cn1cnc2c1c(=O)n(C)c(=O)n2Cc1ccc(Nc2ncnc3ccccc23)cc1",
    score: 0.82,
    confidence: "High",
    reason:
      "Tanimoto 0.79 to a known quinazoline active; MW 419, logP 3.1 — within Lipinski. No interference alerts.",
    nearest_active: "CHEMBL203-A7",
    max_similarity: 0.79,
    is_known_active: true,
  },
  {
    rank: 4,
    smiles: "COc1cc2c(Nc3ccc(Br)cc3)ncnc2cc1OCCCN1CCCC1",
    score: 0.78,
    confidence: "High",
    reason:
      "Tanimoto 0.74 to nearest active; shares aniline-quinazoline hinge binder. Drug-like, clean.",
    nearest_active: "CHEMBL939",
    max_similarity: 0.74,
    is_known_active: false,
  },
  {
    rank: 5,
    smiles: "Nc1ncnc2c1c(-c1ccc(O)cc1)cn2C1CCCC1",
    score: 0.71,
    confidence: "High",
    reason:
      "Tanimoto 0.71 to a known active; passes all filters. Purine-like hinge binder.",
    nearest_active: "CHEMBL553",
    max_similarity: 0.71,
    is_known_active: true,
  },
  {
    rank: 6,
    smiles: "O=C(Nc1cccc(Nc2ncnc3ccccc23)c1)C=C",
    score: 0.64,
    confidence: "Medium",
    reason:
      "Tanimoto 0.58 to nearest active. Contains an acrylamide warhead (covalent motif) — flagged for review but drug-like.",
    nearest_active: "CHEMBL203-A7",
    max_similarity: 0.58,
    is_known_active: true,
  },
  {
    rank: 7,
    smiles: "COc1ccc(Nc2ncnc3cc(OC)c(OC)cc23)cc1Cl",
    score: 0.61,
    confidence: "Medium",
    reason:
      "Tanimoto 0.55 to erlotinib scaffold. Within Lipinski; moderate similarity, no PAINS.",
    nearest_active: "CHEMBL553",
    max_similarity: 0.55,
    is_known_active: false,
  },
  {
    rank: 8,
    smiles: "Cc1ccc(Nc2ncnc3[nH]ccc23)cc1S(=O)(=O)N",
    score: 0.57,
    confidence: "Medium",
    reason:
      "Tanimoto 0.49 to nearest active; pyrrolopyrimidine hinge binder. Passes filters.",
    nearest_active: "CHEMBL203-A7",
    max_similarity: 0.49,
    is_known_active: true,
  },
  {
    rank: 9,
    smiles: "OCCn1cnc2c1ncnc2Nc1ccc(F)cc1",
    score: 0.52,
    confidence: "Medium",
    reason:
      "Tanimoto 0.46 to a known active; drug-like purine core, clean PAINS.",
    nearest_active: "CHEMBL939",
    max_similarity: 0.46,
    is_known_active: false,
  },
  {
    rank: 10,
    smiles: "COc1cc2ncnc(Nc3ccccc3)c2cc1OC",
    score: 0.48,
    confidence: "Medium",
    reason:
      "Tanimoto 0.44 to gefitinib core; simplified quinazoline. Borderline but passes Lipinski.",
    nearest_active: "CHEMBL939",
    max_similarity: 0.44,
    is_known_active: false,
  },
];

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Drives the whole run. `emit` is called for each event; timing is baked in.
export async function runMockStream(
  emit: (e: StreamEvent) => void
): Promise<void> {
  // 1 — supervisor
  emit({ type: "agent_start", agent: "supervisor" });
  emit({ type: "log", agent: "supervisor", payload: "Parsing request…" });
  await wait(700);
  emit({ type: "target_resolved", payload: { id: "CHEMBL203" } });
  emit({
    type: "log",
    agent: "supervisor",
    payload: "Resolved EGFR → CHEMBL203 · 1,500 molecules loaded",
  });
  emit({ type: "funnel", payload: { input: 1500, filtered: null, ranked: null } });
  await wait(600);
  emit({ type: "agent_done", agent: "supervisor" });

  // 2 — knowledge
  emit({ type: "agent_start", agent: "knowledge" });
  emit({
    type: "log",
    agent: "knowledge",
    payload: "Querying ChEMBL for known actives (pChEMBL ≥ 6)…",
  });
  await wait(900);
  emit({
    type: "log",
    agent: "knowledge",
    payload: "Retrieved 87 known actives · fetching PubMed abstracts…",
  });
  await wait(800);
  emit({
    type: "log",
    agent: "knowledge",
    payload: "Embedded 52 abstracts · writing cited dossier…",
  });
  // stream the dossier token by token
  const words = DOSSIER.split(" ");
  for (let i = 0; i < words.length; i++) {
    emit({ type: "dossier_token", payload: words[i] + " " });
    await wait(38);
  }
  emit({ type: "citations", payload: CITATIONS });
  emit({
    type: "grounding",
    payload: {
      cited_pmids: ["15737014", "16729045"],
      provided_pmids: ["15737014", "16729045"],
      ungrounded: [],
      all_grounded: true,
    },
  });
  emit({ type: "agent_done", agent: "knowledge" });
  await wait(300);

  // 3 — cheminformatics: real tool-calling agent (mirrors the live backend's
  // agentic loop — see backend/app/loop.py). Includes a deliberate tool
  // error + retry so DEMO mode shows the self-correction path too.
  emit({ type: "agent_start", agent: "cheminformatics" });
  emit({
    type: "log",
    agent: "cheminformatics",
    payload: "Agentic filtering — deciding thresholds and strategy…",
  });
  await wait(500);
  emit({
    type: "tool_call",
    agent: "cheminformatics",
    payload: {
      iteration: 1,
      thought: "",
      tool: "screen_candidates",
      args: { mw_max: 500, logp_max: 5, hbd_max: 5, hba_max: 10, apply_pains: true },
      result_summary: '{"stats":{"input":1500,"invalid":3,"lipinski_dropped":888,"pains_dropped":398,"survivors":211}}',
      status: "ok",
    },
  });
  emit({ type: "funnel", payload: { input: 1500, filtered: 211, ranked: null } });
  await wait(600);
  emit({
    type: "tool_call",
    agent: "cheminformatics",
    payload: {
      iteration: 2,
      thought: "",
      tool: "compute_descriptors",
      args: { smiles_list: ["not-a-smiles(("] },
      result_summary: "could not parse 1 of 1 SMILES: ['not-a-smiles((']",
      status: "error",
    },
  });
  await wait(500);
  emit({
    type: "tool_call",
    agent: "cheminformatics",
    payload: {
      iteration: 3,
      thought: "",
      tool: "compute_descriptors",
      args: { smiles_list: ["COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1"] },
      result_summary: '{"count":1,"results":[{"smiles":"COc1cc2ncnc...","mw":446.9,"logp":4.1}]}',
      status: "retry",
    },
  });
  await wait(600);
  emit({
    type: "tool_call",
    agent: "cheminformatics",
    payload: {
      iteration: -1,
      thought: "",
      tool: null,
      args: {},
      result_summary: "loop done · 2140 tokens (prompt 1580 / completion 560)",
      status: "ok",
    },
  });
  emit({
    type: "log",
    agent: "cheminformatics",
    payload: "Applied Lipinski + PAINS at default thresholds; 211 survivors — within target range, no re-screen needed.",
  });
  await wait(400);
  emit({ type: "agent_done", agent: "cheminformatics" });

  // 4 — critic: real tool-calling agent, must fetch evidence before scoring.
  emit({ type: "agent_start", agent: "critic" });
  emit({
    type: "log",
    agent: "critic",
    payload: "Agentic scoring — gathering evidence before ranking…",
  });
  await wait(500);
  emit({
    type: "tool_call",
    agent: "critic",
    payload: {
      iteration: 1,
      thought: "",
      tool: "get_funnel_stats",
      args: {},
      result_summary: '{"input":1500,"invalid":3,"lipinski_dropped":888,"pains_dropped":398,"survivors":211}',
      status: "ok",
    },
  });
  await wait(600);
  emit({
    type: "tool_call",
    agent: "critic",
    payload: {
      iteration: 2,
      thought: "",
      tool: "rank_survivors",
      args: { top_n: 20, weights: { similarity: 0.6, qed: 0.3, pains: 0.1 } },
      result_summary: '{"ranked_count":20,"score_range":[0.48,0.94]}',
      status: "ok",
    },
  });
  emit({
    type: "tool_call",
    agent: "critic",
    payload: {
      iteration: -1,
      thought: "",
      tool: null,
      args: {},
      result_summary: "loop done · 980 tokens (prompt 860 / completion 120)",
      status: "ok",
    },
  });
  await wait(500);
  emit({ type: "funnel", payload: { input: 1500, filtered: 211, ranked: 20 } });
  emit({ type: "ranked", payload: RANKED });
  emit({
    type: "log",
    agent: "critic",
    payload: "Dropped 194 low-confidence · top 20 ranked",
  });
  await wait(500);
  emit({
    type: "metric",
    payload: { recovered: 8, total_actives: 10, top_n: 20, screened: 1500 },
  });
  emit({ type: "agent_done", agent: "critic" });
  await wait(300);

  // Human gate after critic: operator can approve export or run an
  // additional diversification -> filter -> critic loop.
  await wait(300);
  emit({ type: "awaiting_approval" });
}

export function buildCsv(ranked: RankedMol[]): string {
  const head = "rank,smiles,score,confidence,nearest_active,max_similarity,reason";
  const rows = ranked.map(
    (r) =>
      `${r.rank},"${r.smiles}",${r.score.toFixed(3)},${r.confidence},${r.nearest_active},${r.max_similarity},"${r.reason.replace(/"/g, "'")}"`
  );
  return [head, ...rows].join("\n");
}