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

RDLogger.DisableLog("rdApp.*")  # quiet parse warnings

# Build the PAINS catalog once and reuse.
_pains_params = FilterCatalogParams()
_pains_params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
_PAINS = FilterCatalog(_pains_params)

# Minimum candidate count before spawning a pool is worthwhile.
_PARALLEL_THRESHOLD = 500
# Small batches give good load balancing (fast workers get the next batch
# immediately via as_completed) without excessive IPC overhead.
_BATCH_SIZE = 128

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


def lipinski_violations(
    d: dict,
    mw_max: float = 500,
    logp_max: float = 5,
    hbd_max: int = 5,
    hba_max: int = 10,
) -> list[str]:
    """Return list of violated Ro5 criteria (empty = fully compliant)."""
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
        sim, idx = max_tanimoto(fingerprint(mol), active_fps)
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
                "lipinski_pass": True,
                "lipinski_violations": violations,
                "pains_flag": False,
                "pains_alerts": [],
                "max_similarity": sim,
                "nearest_active": f"active#{idx}" if idx >= 0 else "-",
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

    sim, idx = max_tanimoto(fingerprint(mol), _w_active_fps)

    qed_val, qed_error = None, False
    try:
        qed_val = round(QED.qed(mol), 3)
    except Exception:
        qed_error = True

    return {
        "status": "survivor",
        "smiles": Chem.MolToSmiles(mol),
        "label": candidate.get("label"),
        **d,
        "qed": qed_val,
        "qed_error": qed_error,
        "lipinski_pass": True,
        "lipinski_violations": violations,
        "pains_flag": bool(alerts),
        "pains_alerts": alerts,
        "max_similarity": sim,
        "nearest_active": f"active#{idx}" if idx >= 0 else "-",
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


def _confidence(sim: float, lip: bool) -> str:
    if sim >= 0.60 and lip:
        return "High"
    if sim >= 0.30:
        return "Medium"
    return "Low"


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
    w_sim: float = 0.6,
    w_qed: float = 0.3,
    w_pains: float = 0.1,
) -> list[dict]:
    """Score, bucket confidence, drop Low, sort, take top_n, assign ranks."""
    scored = []
    for s in survivors:
        sim = s["max_similarity"]
        qed = s["qed"] if s.get("qed") is not None else 0.0
        score = w_sim * sim + w_qed * qed + w_pains * (0.0 if s["pains_flag"] else 1.0)
        conf = _confidence(sim, s["lipinski_pass"])
        if conf == "Low":
            continue
        # map nearest active index -> a chembl-ish id for display
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
        qed_note = " (QED unavailable)" if s.get("qed_error") else ""
        reason = (
            f"{sim:.2f} Tanimoto to {nearest} (known active); "
            f"{'passes' if s['lipinski_pass'] else 'fails'} Lipinski"
        )
        if s.get("lipinski_violations"):
            reason += f" ({len(s['lipinski_violations'])} violation(s))"
        reason += (
            f"; {'no PAINS' if not s['pains_flag'] else 'PAINS flagged'}{qed_note}."
        )
        scored.append(
            {
                "smiles": s["smiles"],
                "score": round(min(score, 0.99), 3),
                "confidence": conf,
                "reason": reason,
                "nearest_active": nearest,
                "max_similarity": sim,
                "is_known_active": (
                    bool(s.get("label")) if s.get("label") is not None else False
                ),
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:top_n]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    return top
