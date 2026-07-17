"""Chemistry core — pure RDKit, deterministic, no network, no model.

This is the reliability anchor: given a list of candidate SMILES and a list of
known-active SMILES, it standardizes, filters (Lipinski + PAINS), scores by
similarity to the actives, and ranks. Same input -> same output.
"""

from __future__ import annotations
from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, AllChem, DataStructs, QED
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

RDLogger.DisableLog("rdApp.*")  # quiet parse warnings

# Build the PAINS catalog once and reuse.
_pains_params = FilterCatalogParams()
_pains_params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
_PAINS = FilterCatalog(_pains_params)


def parse(smiles: str):
    """Parse SMILES -> largest fragment (strips salts). None if invalid."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
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


def lipinski_pass(d: dict, mw_max: float = 500, logp_max: float = 5, hbd_max: int = 5, hba_max: int = 10) -> bool:
    return d["mw"] <= mw_max and d["logp"] <= logp_max and d["hbd"] <= hbd_max and d["hba"] <= hba_max


def pains_flag(mol) -> bool:
    return _PAINS.HasMatch(mol)


def fingerprint(mol):
    return AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)


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


def screen(candidates: list[dict], active_smiles: list[str]) -> tuple[list[dict], dict]:
    """Run the RDKit funnel over candidates.

    candidates: [{"smiles": str, "label": bool|None}, ...]
    Returns (survivors, stats). Survivors pass Lipinski and are PAINS-clean.
    """
    active_fps, _ = build_active_fps(active_smiles)

    n_input = len(candidates)
    n_invalid = n_lipinski = n_pains = 0
    survivors: list[dict] = []

    for c in candidates:
        mol = parse(c["smiles"])
        if mol is None:
            n_invalid += 1
            continue
        d = descriptors(mol)
        lip = lipinski_pass(d)
        if not lip:
            n_lipinski += 1
            continue
        if pains_flag(mol):
            n_pains += 1
            continue
        sim, idx = max_tanimoto(fingerprint(mol), active_fps)
        try:
            qed = round(QED.qed(mol), 3)
        except Exception:
            qed = 0.5
        survivors.append(
            {
                "smiles": Chem.MolToSmiles(mol),
                "label": c.get("label"),
                **d,
                "qed": qed,
                "lipinski_pass": lip,
                "pains_flag": False,
                "max_similarity": sim,
                "nearest_active": f"active#{idx}" if idx >= 0 else "-",
            }
        )

    stats = {
        "input": n_input,
        "invalid": n_invalid,
        "lipinski_dropped": n_lipinski,
        "pains_dropped": n_pains,
        "after_lipinski": n_input - n_invalid - n_lipinski,
        "survivors": len(survivors),
    }
    return survivors, stats


def _confidence(sim: float, lip: bool) -> str:
    if sim >= 0.60 and lip:
        return "High"
    if sim >= 0.30:
        return "Medium"
    return "Low"


def rank(survivors: list[dict], active_chembl_ids: list[str], top_n: int = 600) -> list[dict]:
    """Score, bucket confidence, drop Low, sort, take top_n, assign ranks."""
    scored = []
    for s in survivors:
        sim = s["max_similarity"]
        score = 0.6 * sim + 0.3 * s["qed"] + 0.1 * (0.0 if s["pains_flag"] else 1.0)
        conf = _confidence(sim, s["lipinski_pass"])
        if conf == "Low":
            continue
        # map nearest active index -> a chembl-ish id for display
        idx = int(s["nearest_active"].split("#")[-1]) if "#" in s["nearest_active"] else -1
        nearest = (
            active_chembl_ids[idx]
            if 0 <= idx < len(active_chembl_ids)
            else s["nearest_active"]
        )
        reason = (
            f"{sim:.2f} Tanimoto to {nearest} (known active); "
            f"{'passes' if s['lipinski_pass'] else 'fails'} Lipinski; "
            f"{'no PAINS' if not s['pains_flag'] else 'PAINS flagged'}."
        )
        scored.append(
            {
                "smiles": s["smiles"],
                "score": round(min(score, 0.99), 3),
                "confidence": conf,
                "reason": reason,
                "nearest_active": nearest,
                "max_similarity": sim,
                "is_known_active": bool(s.get("label")) if s.get("label") is not None else False,
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:top_n]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    return top
