"""Chemistry core — pure RDKit, deterministic, no network, no model.

This is the reliability anchor: given a list of candidate SMILES and a list of
known-active SMILES, it standardizes, filters (Lipinski + PAINS), scores by
similarity to the actives, and ranks. Same input -> same output.

screen_parallel() distributes per-molecule work across processes. RDKit mol
objects aren't picklable, so workers receive SMILES strings and rebuild
molecules + active fingerprints inside each process (once, via initializer).
"""

from __future__ import annotations
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, AllChem, DataStructs, QED
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.Scaffolds import MurckoScaffold

# SA Score lives in rdkit.Contrib — path varies by installation.
try:
    from rdkit.Chem import RDConfig
    import os as _os
    import sys as _sys

    _sys.path.append(_os.path.join(RDConfig.RDContribDir, "SA_Score"))
    from sascorer import calculateScore as _sa_raw  # type: ignore
except Exception:
    _sa_raw = None

RDLogger.DisableLog("rdApp.*")  # quiet parse warnings

# Build the PAINS catalog once and reuse.
_pains_params = FilterCatalogParams()
_pains_params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
_PAINS = FilterCatalog(_pains_params)

# Minimum candidate count before spawning a pool is worthwhile.
_PARALLEL_THRESHOLD = 500

# ---- Worker-process state (set by _init_worker, used by _process_one) ----
_w_active_fps: list = []


def parse(smiles: str):
    """Parse SMILES -> largest fragment (strips salts). None if invalid."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    # Only pay for fragment decomposition when salt dots are present.
    if "." in smiles:
        frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
        if len(frags) > 1:
            mol = max(frags, key=lambda m: m.GetNumAtoms())
    return mol


def descriptors(mol) -> dict:
    return {
        "mw": round(Descriptors.MolWt(mol), 1),
        "logp": round(Descriptors.MolLogP(mol), 2),
        "hbd": Descriptors.NumHDonors(mol),
        "hba": Descriptors.NumHAcceptors(mol),
    }


def extended_descriptors(mol) -> dict:
    """Comprehensive descriptor set for export. Computes everything a med-chem
    triager would want in a single pass over the mol object.

    Groups: identity, Lipinski Ro5, extended ADME-relevant, complexity,
    ring systems, charge, and structural keys.
    """
    from rdkit.Chem import rdMolDescriptors, Crippen, Lipinski
    from rdkit.Chem import inchi as inchi_mod

    smi = Chem.MolToSmiles(mol)

    # ---- Identity ----
    try:
        inchi_str = inchi_mod.MolToInchi(mol) or ""
        inchikey = inchi_mod.InchiToInchiKey(inchi_str) if inchi_str else ""
    except Exception:
        inchi_str, inchikey = "", ""

    formula = rdMolDescriptors.CalcMolFormula(mol)

    # ---- Lipinski Ro5 ----
    mw = round(Descriptors.MolWt(mol), 2)
    logp = round(Crippen.MolLogP(mol), 3)
    hbd = Descriptors.NumHDonors(mol)
    hba = Descriptors.NumHAcceptors(mol)

    # ---- Extended ADME-relevant ----
    tpsa = round(Descriptors.TPSA(mol), 2)
    rotatable_bonds = Lipinski.NumRotatableBonds(mol)
    molar_refractivity = round(Crippen.MolMR(mol), 2)

    # ---- Drug-likeness scores ----
    try:
        qed_val = round(QED.qed(mol), 3)
    except Exception:
        qed_val = None

    sa = sa_score(mol)

    # ---- Complexity / size ----
    heavy_atoms = mol.GetNumHeavyAtoms()
    fraction_csp3 = round(Descriptors.FractionCSP3(mol), 3)

    # ---- Ring systems ----
    ring_info = mol.GetRingInfo()
    n_rings = ring_info.NumRings()
    n_aromatic_rings = Descriptors.NumAromaticRings(mol)
    n_heteroatoms = Descriptors.NumHeteroatoms(mol)

    # ---- Charge ----
    formal_charge = Chem.GetFormalCharge(mol)

    # ---- PAINS ----
    alerts = pains_flag(mol)

    # ---- Scaffold ----
    skey = scaffold_key(mol)

    return {
        # identity
        "smiles": smi,
        "inchi": inchi_str,
        "inchikey": inchikey,
        "molecular_formula": formula,
        # Lipinski Ro5
        "mw": mw,
        "logp": logp,
        "hbd": hbd,
        "hba": hba,
        # extended ADME
        "tpsa": tpsa,
        "rotatable_bonds": rotatable_bonds,
        "molar_refractivity": molar_refractivity,
        # drug-likeness
        "qed": qed_val,
        "sa_score": sa,
        # complexity
        "heavy_atoms": heavy_atoms,
        "fraction_csp3": fraction_csp3,
        # ring systems
        "n_rings": n_rings,
        "n_aromatic_rings": n_aromatic_rings,
        "n_heteroatoms": n_heteroatoms,
        # charge
        "formal_charge": formal_charge,
        # PAINS
        "n_pains_alerts": len(alerts),
        "pains_alerts": "; ".join(alerts) if alerts else "",
        # scaffold
        "scaffold": skey,
    }


def lipinski_violations(
    d: dict,
    mw_max: float = 500,
    logp_max: float = 5,
    hbd_max: int = 5,
    hba_max: int = 10,
) -> list[str]:
    """
    Return list of violated Ro5 criteria (empty = fully compliant).
    """
    violations = []
    if d["mw"] > mw_max:
        violations.append(f"MW {d['mw']} > {mw_max}")
    if d["logp"] > logp_max:
        violations.append(f"LogP {d['logp']} > {logp_max}")
    if d["hbd"] > hbd_max:
        violations.append(f"HBD {d['hbd']} > {hbd_max}")
    if d["hba"] > hba_max:
        violations.append(f"HBA {d['hba']} > {hba_max}")
    return violations


def lipinski_pass(
    d: dict,
    mw_max: float = 500,
    logp_max: float = 5,
    hbd_max: int = 5,
    hba_max: int = 10,
    max_violations: int = 1,
) -> bool:
    """True Ro5: pass if at most `max_violations` criteria are violated."""
    return (
        len(lipinski_violations(d, mw_max, logp_max, hbd_max, hba_max))
        <= max_violations
    )


def pains_flag(mol) -> list[str]:
    """Return list of PAINS alert names that matched (empty = clean).
    Uses HasMatch for a fast boolean pre-check — GetMatches only runs
    when there's actually a hit, saving ~480 SMARTS evaluations on clean mols."""
    if not _PAINS.HasMatch(mol):
        return []
    return [entry.GetDescription() for entry in _PAINS.GetMatches(mol)]


def fingerprint(mol, use_chirality: bool = True):
    return AllChem.GetMorganFingerprintAsBitVect(
        mol, radius=2, nBits=2048, useChirality=use_chirality
    )


def max_tanimoto(fp, active_fps: list) -> tuple[float, int]:
    """Best Tanimoto against the actives; returns (score, index)."""
    if not active_fps:
        return 0.0, -1
    sims = DataStructs.BulkTanimotoSimilarity(fp, active_fps)
    best = max(range(len(sims)), key=lambda i: sims[i])
    return round(sims[best], 3), best


def similarity_profile(fp, active_fps: list, top_k: int = 3) -> dict:
    """Richer similarity signal than max-only.

    Returns {max_sim, max_idx, top_k_avg, n_above_03} where:
    - max_sim / max_idx: best Tanimoto and which active
    - top_k_avg: mean of the top-k similarities (breadth signal)
    - n_above_03: count of actives with Tanimoto >= 0.3 (coverage)
    """
    if not active_fps:
        return {"max_sim": 0.0, "max_idx": -1, "top_k_avg": 0.0, "n_above_03": 0}
    sims = DataStructs.BulkTanimotoSimilarity(fp, active_fps)
    sorted_sims = sorted(sims, reverse=True)
    best_idx = max(range(len(sims)), key=lambda i: sims[i])
    top_k_vals = sorted_sims[: min(top_k, len(sorted_sims))]
    return {
        "max_sim": round(sorted_sims[0], 3),
        "max_idx": best_idx,
        "top_k_avg": round(sum(top_k_vals) / len(top_k_vals), 3),
        "n_above_03": sum(1 for s in sims if s >= 0.3),
    }


def sa_score(mol) -> float | None:
    """Synthetic accessibility score (Ertl & Schuffenhauer).
    Returns 0.0 (easy) to 1.0 (hard), normalized from the raw 1-10 scale.
    None if SA scorer is unavailable."""
    if _sa_raw is None:
        return None
    try:
        raw = _sa_raw(mol)  # 1 (easy) – 10 (hard)
        return round((raw - 1.0) / 9.0, 3)  # normalize to 0–1
    except Exception:
        return None


def scaffold_key(mol) -> str:
    """Bemis-Murcko generic framework as SMILES. Used for diversity clustering."""
    try:
        core = MurckoScaffold.GetScaffoldForMol(mol)
        generic = MurckoScaffold.MakeScaffoldGeneric(core)
        return Chem.MolToSmiles(generic)
    except Exception:
        return Chem.MolToSmiles(mol)


def build_active_fps(active_smiles: list[str]):
    fps, ids = [], []
    for i, s in enumerate(active_smiles):
        m = parse(s)
        if m is not None:
            fps.append(fingerprint(m))
            ids.append(i)
    return fps, ids


def screen(
    candidates: list[dict], active_smiles: list[str], max_violations: int = 1
) -> tuple[list[dict], dict]:
    """Run the RDKit funnel over candidates.

    candidates: [{"smiles": str, "label": bool|None}, ...]
    Returns (survivors, stats). Survivors pass Lipinski (≤max_violations)
    and are PAINS-clean.
    """
    active_fps, _ = build_active_fps(active_smiles)

    n_input = len(candidates)
    n_invalid = n_lipinski = n_pains = n_qed_err = 0
    survivors: list[dict] = []

    for c in candidates:
        mol = parse(c["smiles"])
        if mol is None:
            n_invalid += 1
            continue
        d = descriptors(mol)
        violations = lipinski_violations(d)
        if len(violations) > max_violations:
            n_lipinski += 1
            continue
        alerts = pains_flag(mol)
        if alerts:
            n_pains += 1
            continue
        fp = fingerprint(mol)
        sim_prof = similarity_profile(fp, active_fps)
        qed_error = False
        try:
            qed = round(QED.qed(mol), 3)
        except Exception:
            qed = None
            qed_error = True
            n_qed_err += 1
        survivors.append(
            {
                "smiles": Chem.MolToSmiles(mol),
                "label": c.get("label"),
                **d,
                "qed": qed,
                "qed_error": qed_error,
                "sa_score": sa_score(mol),
                "scaffold": scaffold_key(mol),
                "lipinski_pass": True,
                "lipinski_violations": violations,
                "pains_flag": False,
                "pains_alerts": [],
                "n_pains_alerts": 0,
                "max_similarity": sim_prof["max_sim"],
                "top_k_avg": sim_prof["top_k_avg"],
                "n_actives_above_03": sim_prof["n_above_03"],
                "nearest_active": (
                    f"active#{sim_prof['max_idx']}" if sim_prof["max_idx"] >= 0 else "-"
                ),
            }
        )

    stats = {
        "input": n_input,
        "invalid": n_invalid,
        "lipinski_dropped": n_lipinski,
        "pains_dropped": n_pains,
        "qed_errors": n_qed_err,
        "after_lipinski": n_input - n_invalid - n_lipinski,
        "survivors": len(survivors),
    }
    return survivors, stats


# ---------------------------------------------------------------- parallel --


def _init_worker(active_smiles: list[str]) -> None:
    """Called once per subprocess. Rebuilds active fingerprints in worker
    memory. Thresholds are passed per work-item so the pool can be reused
    across screen_candidates calls with different parameters."""
    global _w_active_fps
    _w_active_fps, _ = build_active_fps(active_smiles)


def _process_one(work_item: tuple[dict, dict]) -> dict:
    """Process a single candidate molecule — runs inside a worker process.

    work_item: (candidate_dict, thresholds_dict)
    Returns a dict with a "status" routing key.
    """
    candidate, t = work_item
    mol = parse(candidate["smiles"])
    if mol is None:
        return {"status": "invalid", "smiles": candidate["smiles"]}

    d = descriptors(mol)
    violations = lipinski_violations(
        d,
        mw_max=t["mw_max"],
        logp_max=t["logp_max"],
        hbd_max=t["hbd_max"],
        hba_max=t["hba_max"],
    )
    if len(violations) > t["max_violations"]:
        return {"status": "lipinski_dropped"}

    alerts = pains_flag(mol)
    if t["apply_pains"] and alerts:
        return {"status": "pains_dropped"}

    fp = fingerprint(mol)
    sim_prof = similarity_profile(fp, _w_active_fps)

    qed_val, qed_error = None, False
    try:
        qed_val = round(QED.qed(mol), 3)
    except Exception:
        qed_error = True

    sa = sa_score(mol)
    skey = scaffold_key(mol)

    return {
        "status": "survivor",
        "smiles": Chem.MolToSmiles(mol),
        "label": candidate.get("label"),
        **d,
        "qed": qed_val,
        "qed_error": qed_error,
        "sa_score": sa,
        "scaffold": skey,
        "lipinski_pass": True,
        "lipinski_violations": violations,
        "pains_flag": bool(alerts),
        "pains_alerts": alerts,
        "n_pains_alerts": len(alerts),
        "max_similarity": sim_prof["max_sim"],
        "top_k_avg": sim_prof["top_k_avg"],
        "n_actives_above_03": sim_prof["n_above_03"],
        "nearest_active": (
            f"active#{sim_prof['max_idx']}" if sim_prof["max_idx"] >= 0 else "-"
        ),
    }


def _process_batch(work_items: list[tuple[dict, dict]]) -> list[dict]:
    """Process a batch of candidates in one worker call.
    Reduces per-task IPC overhead vs submitting one future per molecule."""
    return [_process_one(item) for item in work_items]


def _aggregate(results: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """Reduce per-molecule results into (survivors, stats, invalid_examples)."""
    survivors: list[dict] = []
    n_invalid = n_lipinski = n_pains = n_qed_err = 0
    invalid_examples: list[str] = []

    for r in results:
        st = r["status"]
        if st == "invalid":
            n_invalid += 1
            if len(invalid_examples) < 3:
                invalid_examples.append(r["smiles"])
        elif st == "lipinski_dropped":
            n_lipinski += 1
        elif st == "pains_dropped":
            n_pains += 1
        elif st == "survivor":
            if r.get("qed_error"):
                n_qed_err += 1
            r.pop("status")
            survivors.append(r)

    stats = {
        "input": len(results),
        "invalid": n_invalid,
        "lipinski_dropped": n_lipinski,
        "pains_dropped": n_pains,
        "qed_errors": n_qed_err,
        "after_lipinski": len(results) - n_invalid - n_lipinski,
        "survivors": len(survivors),
    }
    return survivors, stats, invalid_examples


# Small batches give good load balancing (fast workers get the next batch
# immediately via as_completed) without excessive IPC overhead.
_BATCH_SIZE = 64


class ScreenPool:
    """Persistent process pool for screening. Created once per run when
    actives are known, reused across agent re-screens with different
    thresholds. Eliminates repeated fork/init overhead between calls.

    Usage:
        pool = ScreenPool(active_smiles, n_workers=4)
        survivors, stats, bad = pool.screen(candidates, thresholds)
        # ... agent adjusts thresholds ...
        survivors, stats, bad = pool.screen(candidates, new_thresholds)
        pool.shutdown()
    """

    def __init__(self, active_smiles: list[str], n_workers: int | None = None):
        self._n_workers = n_workers or min(mp.cpu_count(), 8)
        self._active_smiles = active_smiles
        self._pool: ProcessPoolExecutor | None = None

    def _ensure_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self._n_workers,
                initializer=_init_worker,
                initargs=(self._active_smiles,),
            )
        return self._pool

    def screen(
        self,
        candidates: list[dict],
        thresholds: dict,
    ) -> tuple[list[dict], dict, list[str]]:
        n = len(candidates)
        if n < _PARALLEL_THRESHOLD:
            # Sequential — no fork overhead for small libraries
            _init_worker(self._active_smiles)
            results = [_process_one((c, thresholds)) for c in candidates]
            return _aggregate(results)

        pool = self._ensure_pool()

        # Chunk candidates into small batches and submit them all.
        # as_completed lets fast workers pick up the next batch immediately
        # instead of idling until the slowest worker in a map() chunk finishes.
        batches = []
        for i in range(0, n, _BATCH_SIZE):
            batch = [(c, thresholds) for c in candidates[i : i + _BATCH_SIZE]]
            batches.append(batch)

        futures = [pool.submit(_process_batch, b) for b in batches]

        results: list[dict] = []
        for future in as_completed(futures):
            results.extend(future.result())

        return _aggregate(results)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def __del__(self):
        self.shutdown()


def screen_parallel(
    candidates: list[dict],
    active_smiles: list[str],
    thresholds: dict,
    n_workers: int | None = None,
    pool: ScreenPool | None = None,
) -> tuple[list[dict], dict, list[str]]:
    """Parallel screening entry point.

    If a ScreenPool is provided, reuses it (no fork overhead). Otherwise
    creates a one-shot pool for backwards compatibility.
    """
    if pool is not None:
        return pool.screen(candidates, thresholds)

    # One-shot fallback — still better than sequential for large inputs
    one_shot = ScreenPool(active_smiles, n_workers)
    try:
        return one_shot.screen(candidates, thresholds)
    finally:
        one_shot.shutdown()


def _confidence(score: float, sim: float, lipinski_pass: bool) -> str:
    """Confidence bucket using BOTH the composite score and similarity.
    Score and confidence now agree — a High compound always outscores a Medium."""
    if score >= 0.55 and sim >= 0.50 and lipinski_pass:
        return "High"
    if score >= 0.30 and sim >= 0.25:
        return "Medium"
    return "Low"


# ---- Default scoring weights ----
DEFAULT_WEIGHTS = {
    "similarity": 0.40,  # max Tanimoto to nearest active
    "breadth": 0.15,  # top-k average (multi-active coverage)
    "qed": 0.20,  # drug-likeness
    "sa": 0.15,  # synthetic accessibility (inverted: easy = high)
    "penalty_lipinski": 0.05,  # per violation
    "penalty_pains": 0.03,  # per PAINS alert (graduated, not binary)
}


def compute_score(s: dict, weights: dict | None = None) -> float:
    """Compute the composite triage score for a survivor dict.

    score = w_sim × max_similarity
          + w_breadth × top_k_avg
          + w_qed × QED
          + w_sa × (1 - SA_norm)           # lower SA = easier = better
          - penalty_lipinski × n_violations
          - penalty_pains × n_alerts
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    sim = s["max_similarity"]
    breadth = s.get("top_k_avg", sim)  # fallback to max if profile missing
    qed = s["qed"] if s.get("qed") is not None else 0.0
    sa = s.get("sa_score")
    sa_term = (1.0 - sa) if sa is not None else 0.5  # neutral if unavailable
    n_viol = len(s.get("lipinski_violations", []))
    n_pains = s.get("n_pains_alerts", 1 if s.get("pains_flag") else 0)

    raw = (
        w["similarity"] * sim
        + w["breadth"] * breadth
        + w["qed"] * qed
        + w["sa"] * sa_term
        - w["penalty_lipinski"] * n_viol
        - w["penalty_pains"] * n_pains
    )
    return round(max(0.0, min(raw, 1.0)), 3)


def _build_reason(s: dict, score: float, nearest_id: str) -> str:
    """Human-readable rationale string for a ranked compound."""
    sim = s["max_similarity"]
    parts = [f"{sim:.2f} Tanimoto to {nearest_id}"]

    breadth = s.get("top_k_avg")
    n_above = s.get("n_actives_above_03", 0)
    if breadth is not None and breadth != sim:
        parts.append(f"top-3 avg {breadth:.2f}")
    if n_above > 1:
        parts.append(f"similar to {n_above} actives")

    lip_v = s.get("lipinski_violations", [])
    if lip_v:
        parts.append(f"Lipinski: {len(lip_v)} violation(s)")
    else:
        parts.append("passes Lipinski")

    sa = s.get("sa_score")
    if sa is not None:
        ease = "easy" if sa < 0.33 else ("moderate" if sa < 0.66 else "hard")
        parts.append(f"SA: {ease} ({1 - sa:.2f})")

    n_pains = s.get("n_pains_alerts", 0)
    if n_pains:
        parts.append(f"{n_pains} PAINS alert(s)")

    if s.get("qed_error"):
        parts.append("QED unavailable")

    return "; ".join(parts) + "."


def diversity_rerank(scored: list[dict], top_n: int) -> list[dict]:
    """Scaffold-aware greedy pick to ensure chemotype diversity.

    Walks the score-sorted list and picks the next highest-scoring compound
    whose Bemis-Murcko scaffold hasn't been seen more than `max_per_scaffold`
    times. This prevents the top-N from being dominated by analogs of one
    active, while still respecting the score ordering within each scaffold.
    """
    if not scored:
        return []

    # Allow more representatives from high-scoring scaffolds, but cap
    n_scaffolds_est = len({s.get("scaffold", s["smiles"]) for s in scored})
    if n_scaffolds_est > 0:
        max_per = max(3, top_n // max(1, n_scaffolds_est))
    else:
        max_per = max(3, top_n // 10)

    scaffold_counts: dict[str, int] = {}
    picked: list[dict] = []

    for s in scored:
        if len(picked) >= top_n:
            break
        skey = s.get("scaffold", s["smiles"])
        count = scaffold_counts.get(skey, 0)
        if count < max_per:
            scaffold_counts[skey] = count + 1
            picked.append(s)

    # If we couldn't fill top_n due to diversity caps, backfill from skipped
    if len(picked) < top_n:
        picked_smiles = {p["smiles"] for p in picked}
        for s in scored:
            if len(picked) >= top_n:
                break
            if s["smiles"] not in picked_smiles:
                picked.append(s)

    return picked


def canonical(smiles: str) -> str | None:
    """Canonicalize a SMILES string. Returns None if unparseable."""
    mol = parse(smiles)
    if mol is None:
        return None
    return Chem.MolToSmiles(mol)


def rank(
    survivors: list[dict],
    active_chembl_ids: list[str],
    top_n: int = 600,
    weights: dict | None = None,
    diversity: bool = True,
) -> list[dict]:
    """Score, bucket confidence, drop Low, diversity-rerank, assign ranks."""
    scored = []
    for s in survivors:
        score = compute_score(s, weights)
        sim = s["max_similarity"]
        conf = _confidence(score, sim, s["lipinski_pass"])
        if conf == "Low":
            continue
        idx = (
            int(s["nearest_active"].split("#")[-1])
            if "#" in s["nearest_active"]
            else -1
        )
        nearest = (
            active_chembl_ids[idx]
            if 0 <= idx < len(active_chembl_ids)
            else s["nearest_active"]
        )
        scored.append(
            {
                "smiles": s["smiles"],
                "score": score,
                "confidence": conf,
                "reason": _build_reason(s, score, nearest),
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

    if diversity:
        top = diversity_rerank(scored, top_n)
    else:
        top = scored[:top_n]

    for i, r in enumerate(top, 1):
        r["rank"] = i
    return top
