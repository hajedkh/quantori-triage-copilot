"""Export writers — CSV, SDF, and a Markdown report with the dossier + citations.

The CSV is the primary downstream artifact for cheminformatics triage.
It includes the full Lipinski Ro5 breakdown, extended ADME descriptors,
structural keys (InChIKey, Murcko scaffold), and scoring provenance,
all computed fresh from the ranked SMILES at export time.

"""

from __future__ import annotations
import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from rdkit import Chem

# Import chem for extended_descriptors — resolve relative or absolute
try:
    from . import chem
    from .config import load_export_config
except ImportError:
    import chem  # type: ignore

    from config import load_export_config  # type: ignore


# ---- Column groups for the comprehensive CSV ----
# Ordered for a med-chem triager: ranking context first, then Lipinski,
# then extended properties, then structural/provenance.

_CSV_COLUMNS = [
    # --- Ranking ---
    "rank",
    "smiles",
    "score",
    "confidence",
    # --- Similarity ---
    "nearest_active",
    "max_similarity",
    # --- Lipinski Ro5 ---
    "mw",
    "logp",
    "hbd",
    "hba",
    "n_lipinski_violations",
    "lipinski_violations",
    # --- Extended ADME ---
    "tpsa",
    "rotatable_bonds",
    "molar_refractivity",
    # --- Drug-likeness ---
    "qed",
    "sa_score",
    # --- Complexity ---
    "heavy_atoms",
    "fraction_csp3",
    # --- Ring systems ---
    "n_rings",
    "n_aromatic_rings",
    "n_heteroatoms",
    # --- Charge ---
    "formal_charge",
    # --- PAINS ---
    "n_pains_alerts",
    "pains_alerts",
    # --- Structural ---
    "scaffold",
    "molecular_formula",
    "inchikey",
    # --- External cross-reference ---
    "chembl_id",
    "pubchem_cid",
    "crossref_queried",
    # --- Provenance ---
    "is_known_active",
    "is_diversified_generated",
    "reason",
    "evidence_used",
]

_CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
_PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_EXPORT_CFG = load_export_config()


def _notify_progress(callback, stage: str, message: str) -> None:
    if callback is None:
        return
    try:
        callback(stage, message)
    except Exception:
        pass


def _enrich_row(r: dict) -> dict:
    """Merge ranking data with freshly-computed extended descriptors.

    The ranked list carries SMILES + scoring fields; extended_descriptors
    recomputes the full property set from the SMILES. This runs on the
    final shortlist (<=600 compounds) so the cost is trivial.
    """
    mol = chem.parse(r["smiles"])
    if mol is None:
        return {col: "" for col in _CSV_COLUMNS}

    ext = chem.extended_descriptors(mol)

    # Lipinski violations against default thresholds
    lip_v = chem.lipinski_violations(
        {"mw": ext["mw"], "logp": ext["logp"], "hbd": ext["hbd"], "hba": ext["hba"]}
    )

    return {
        # ranking
        "rank": r.get("rank", ""),
        "smiles": r["smiles"],
        "score": (
            f"{float(r.get('score')):.3f}" if r.get("score") not in (None, "") else ""
        ),
        "confidence": r.get("confidence", ""),
        # similarity
        "nearest_active": r.get("nearest_active", ""),
        "max_similarity": r.get("max_similarity", ""),
        # Lipinski Ro5
        "mw": ext["mw"],
        "logp": ext["logp"],
        "hbd": ext["hbd"],
        "hba": ext["hba"],
        "n_lipinski_violations": len(lip_v),
        "lipinski_violations": "; ".join(lip_v) if lip_v else "",
        # extended ADME
        "tpsa": ext["tpsa"],
        "rotatable_bonds": ext["rotatable_bonds"],
        "molar_refractivity": ext["molar_refractivity"],
        # drug-likeness
        "qed": ext["qed"] if ext["qed"] is not None else "",
        "sa_score": ext["sa_score"] if ext["sa_score"] is not None else "",
        # complexity
        "heavy_atoms": ext["heavy_atoms"],
        "fraction_csp3": ext["fraction_csp3"],
        # ring systems
        "n_rings": ext["n_rings"],
        "n_aromatic_rings": ext["n_aromatic_rings"],
        "n_heteroatoms": ext["n_heteroatoms"],
        # charge
        "formal_charge": ext["formal_charge"],
        # PAINS
        "n_pains_alerts": ext["n_pains_alerts"],
        "pains_alerts": ext["pains_alerts"],
        # structural
        "scaffold": ext["scaffold"],
        "molecular_formula": ext["molecular_formula"],
        "inchikey": ext["inchikey"],
        # external cross-reference (filled later)
        "chembl_id": "",
        "pubchem_cid": "",
        "crossref_queried": "",
        # provenance
        "is_known_active": r.get("is_known_active", ""),
        "is_diversified_generated": r.get("is_diversified_generated", False),
        "reason": r.get("reason", ""),
        "evidence_used": (
            "; ".join(r.get("evidence_used", [])) if r.get("evidence_used") else ""
        ),
    }


def _lookup_chembl_id(inchikey: str) -> str:
    if not inchikey:
        return ""
    try:
        r = requests.get(
            f"{_CHEMBL}/molecule.json",
            params={"molecule_structures__standard_inchi_key": inchikey, "limit": 1},
            timeout=_EXPORT_CFG.xref_timeout_seconds,
        )
        r.raise_for_status()
        molecules = r.json().get("molecules", [])
        if molecules:
            return molecules[0].get("molecule_chembl_id", "") or ""
    except Exception:
        return ""
    return ""


def _lookup_pubchem_cid(inchikey: str) -> str:
    if not inchikey:
        return ""
    try:
        r = requests.get(
            f"{_PUBCHEM}/compound/inchikey/{quote(inchikey)}/cids/JSON",
            timeout=_EXPORT_CFG.xref_timeout_seconds,
        )
        r.raise_for_status()
        cids = r.json().get("IdentifierList", {}).get("CID", [])
        if cids:
            return str(cids[0])
    except Exception:
        return ""
    return ""


def _xref_one(row: dict) -> tuple[str, dict]:
    inchikey = row.get("inchikey", "") or ""
    smiles = row.get("smiles", "") or ""
    chembl_id = _lookup_chembl_id(inchikey)
    pubchem_cid = _lookup_pubchem_cid(inchikey)
    return (
        smiles,
        {
            "chembl_id": chembl_id,
            "pubchem_cid": pubchem_cid,
            "crossref_queried": True,
        },
    )


def _decide_xref_scope(rows: list[dict]) -> tuple[int, dict]:
    n = len(rows)
    if n <= _EXPORT_CFG.xref_top_limit:
        return n, {
            "mode": "all",
            "reason": "<= configured shortlist query limit",
            "projected_seconds": 0.0,
        }

    sample_n = min(_EXPORT_CFG.xref_probe_n, n)
    sample_rows = rows[:sample_n]
    start = time.perf_counter()
    workers = max(1, min(_EXPORT_CFG.xref_workers, sample_n))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_xref_one, r) for r in sample_rows]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass
    elapsed = max(time.perf_counter() - start, 0.01)
    avg_per_compound = elapsed / sample_n
    projected = avg_per_compound * n

    if projected > _EXPORT_CFG.xref_budget_seconds:
        return _EXPORT_CFG.xref_top_limit, {
            "mode": "top50",
            "reason": "projected API time too long",
            "projected_seconds": round(projected, 2),
        }
    return n, {
        "mode": "all",
        "reason": "projected API time acceptable",
        "projected_seconds": round(projected, 2),
    }


def _crossref_rows(rows: list[dict], progress_callback=None) -> tuple[list[dict], dict]:
    if not rows:
        return rows, {
            "requested": 0,
            "queried": 0,
            "mode": "all",
            "reason": "empty shortlist",
            "projected_seconds": 0.0,
            "chembl_found": 0,
            "pubchem_found": 0,
        }

    _notify_progress(
        progress_callback,
        "crossref_scope",
        "Estimating cross-reference scope and API latency.",
    )
    scope_n, meta = _decide_xref_scope(rows)
    scoped = rows[:scope_n]

    _notify_progress(
        progress_callback,
        "crossref_lookup",
        "Checking ChEMBL and PubChem for selected structures.",
    )
    by_smiles: dict[str, dict] = {}
    workers = max(1, min(_EXPORT_CFG.xref_workers, len(scoped)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_xref_one, r) for r in scoped]
        for f in as_completed(futures):
            try:
                smi, data = f.result()
                by_smiles[smi] = data
            except Exception:
                pass

    out = []
    for i, row in enumerate(rows):
        smi = row.get("smiles", "")
        ref = by_smiles.get(smi)
        if ref:
            out.append({**row, **ref})
        else:
            out.append(
                {
                    **row,
                    "chembl_id": "",
                    "pubchem_cid": "",
                    "crossref_queried": i < scope_n,
                }
            )

    summary = {
        "requested": len(rows),
        "queried": scope_n,
        **meta,
        "chembl_found": sum(1 for r in out if r.get("chembl_id")),
        "pubchem_found": sum(1 for r in out if r.get("pubchem_cid")),
    }
    return out, summary


def write_csv(
    path: Path, ranked: list[dict], progress_callback=None
) -> tuple[list[dict], dict]:
    """Write CSV and return enriched rows + cross-reference summary."""
    _notify_progress(
        progress_callback,
        "csv_prepare",
        "Preparing CSV summary for selected compounds.",
    )
    enriched_rows = [_enrich_row(r) for r in ranked]
    enriched_rows, xref_summary = _crossref_rows(enriched_rows, progress_callback)

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in enriched_rows:
            w.writerow(row)

    _notify_progress(
        progress_callback,
        "csv_done",
        "CSV export complete.",
    )

    return enriched_rows, xref_summary


def _embed_3d(mol):
    """Generate a 3D conformer with ETKDG + MMFF94 optimization.

    Returns the mol with 3D coords, or None if embedding fails.
    Pipeline: AddHs → ETKDGv3 embed → MMFF94 optimize (UFF fallback).
    """
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(mol)

    # ETKDGv3 is the best-quality conformer generator in RDKit
    params = AllChem.ETKDGv3()
    params.randomSeed = 42  # reproducible for the demo
    status = AllChem.EmbedMolecule(mol, params)
    if status == -1:
        # Retry with more permissive settings for strained molecules
        params.useRandomCoords = True
        params.maxAttempts = 50
        status = AllChem.EmbedMolecule(mol, params)
        if status == -1:
            return None

    # Force field optimization — MMFF94 preferred, UFF fallback
    try:
        result = AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
        # result: 0 = converged, 1 = not converged but still usable, -1 = setup failed
        if result == -1:
            AllChem.UFFOptimizeMolecule(mol, maxIters=500)
    except Exception:
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=500)
        except Exception:
            pass  # keep the ETKDG geometry unoptimized

    return mol


def _embed_worker(smiles: str) -> tuple[str | None, str]:
    """Process-pool worker: parse a SMILES and generate a 3D conformer.

    Runs in a separate process, so it takes and returns only picklable values
    (strings) — never RDKit Mol objects. Returns (molblock, status); the parent
    re-parses the molblock. randomSeed is fixed inside _embed_3d, so the
    conformer for a given SMILES is identical whether produced here or serially.
    """
    mol = chem.parse(smiles)
    if mol is None:
        return None, "parse_failed"
    mol_3d = _embed_3d(mol)
    if mol_3d is None:
        return None, "2D_only"
    # The molblock carries the 3D coordinates + explicit Hs from _embed_3d.
    return Chem.MolToMolBlock(mol_3d, includeStereo=True), "MMFF94_optimized"


def _embed_all(
    smiles_list: list[str], gen_3d: bool, max_workers: int | None
) -> list[tuple[str | None, str]]:
    """Generate 3D conformers for every SMILES, parallelized when worthwhile.

    Returns a list of (molblock_or_None, status) aligned 1:1 with smiles_list,
    order preserved. Falls back to a serial pass when 3D is disabled, the set
    is tiny, or the process pool can't be created (restricted sandbox, etc.) —
    export must never break just because parallelism was unavailable.
    """
    n = len(smiles_list)
    if not gen_3d:
        return [(None, "2D_only")] * n

    serial = n < _EXPORT_CFG.embed_parallel_min or (
        max_workers is not None and max_workers <= 1
    )
    if not serial:
        workers = max_workers or min(os.cpu_count() or 1, n)
        if workers > 1:
            # Chunk so IPC overhead stays small relative to per-molecule work.
            chunksize = max(1, n // (workers * 4))
            try:
                with ProcessPoolExecutor(max_workers=workers) as ex:
                    return list(ex.map(_embed_worker, smiles_list, chunksize=chunksize))
            except Exception:
                pass  # fall through to serial

    return [_embed_worker(s) for s in smiles_list]


def write_sdf(
    path: Path,
    ranked: list[dict],
    gen_3d: bool = True,
    max_workers: int | None = None,
) -> dict:
    """Write SDF with 3D conformers (ETKDG + MMFF94) and all computed properties.

    When gen_3d is True (default), each molecule gets a force-field-optimized
    3D conformer. The embedding step — the bottleneck — is parallelized across
    processes (defaults to one per CPU). Molecules that fail embedding are
    written as 2D. Pass max_workers=1 to force the old serial behavior.

    Descriptors are still computed in-process from a clean parse of each SMILES,
    exactly as before, so property values are unchanged from the serial version.
    """
    smiles_list = [r["smiles"] for r in ranked]
    embedded = _embed_all(smiles_list, gen_3d, max_workers)

    w = Chem.SDWriter(str(path))
    n_3d = n_2d = 0

    for r, (molblock, status) in zip(ranked, embedded):
        # base_mol: clean no-H parse, used for descriptors and as the 2D body.
        base_mol = chem.parse(r["smiles"])
        if base_mol is None:
            continue  # unparseable SMILES — skip, matches serial behavior

        ext = chem.extended_descriptors(base_mol)

        if status == "MMFF94_optimized" and molblock:
            out_mol = Chem.MolFromMolBlock(molblock, removeHs=False)
            if out_mol is None:  # defensive: molblock round-trip failed
                out_mol, status = base_mol, "2D_only"
        else:
            out_mol, status = base_mol, "2D_only"

        out_mol.SetProp("_3d_status", status)
        if status == "MMFF94_optimized":
            n_3d += 1
        else:
            n_2d += 1

        # Ranking fields
        out_mol.SetProp("rank", str(r.get("rank", "")))
        out_mol.SetProp("score", f'{r["score"]:.3f}')
        out_mol.SetProp("confidence", r.get("confidence", ""))
        out_mol.SetProp("nearest_active", str(r.get("nearest_active", "")))
        out_mol.SetProp(
            "is_diversified_generated",
            str(bool(r.get("is_diversified_generated", False))),
        )

        # Numeric descriptors
        for key in [
            "mw",
            "logp",
            "hbd",
            "hba",
            "tpsa",
            "rotatable_bonds",
            "molar_refractivity",
            "heavy_atoms",
            "fraction_csp3",
            "n_rings",
            "n_aromatic_rings",
            "n_heteroatoms",
            "formal_charge",
            "n_pains_alerts",
        ]:
            val = ext.get(key)
            if val is not None:
                out_mol.SetProp(key, str(val))

        # Float descriptors with precision
        for key in ["qed", "sa_score"]:
            val = ext.get(key)
            if val is not None:
                out_mol.SetProp(key, f"{val:.3f}")

        # String descriptors
        for key in ["scaffold", "molecular_formula", "inchikey", "pains_alerts"]:
            val = ext.get(key)
            if val:
                out_mol.SetProp(key, val)

        w.write(out_mol)
    w.close()

    return {"3d_conformers": n_3d, "2d_fallback": n_2d}


def write_report(
    path: Path,
    target: str,
    dossier: str,
    citations: list[dict],
    ranked: list[dict],
    metric: dict | None,
    screen_stats: dict | None = None,
    grounding: dict | None = None,
    provenance: dict | None = None,
    enriched_rows: list[dict] | None = None,
    xref_summary: dict | None = None,
) -> None:
    lines = [f"# Target Triage Report — {target}", ""]
    if provenance:
        lines += [
            "## Run provenance",
            "",
            f"- **Timestamp:** {provenance.get('timestamp', 'N/A')}",
            f"- **Model:** {provenance.get('model', 'N/A')}",
            f"- **Provider:** {provenance.get('provider', 'N/A')}",
            "",
        ]
    if metric:
        lines += [
            f"**Validation:** recovered {metric['recovered']} / {metric['total_actives']} "
            f"known actives in the top {metric['top_n']} (from {metric['screened']} screened).",
            "",
        ]
    if screen_stats:
        lines += [
            "## Screening funnel",
            "",
            f"- **Input:** {screen_stats.get('input', '?')} candidates",
            f"- **Diversification additions:** {screen_stats.get('diversified_added', 0)} generated compounds",
            f"- **Invalid SMILES:** {screen_stats.get('invalid', 0)} dropped",
            f"- **Lipinski failures:** {screen_stats.get('lipinski_dropped', 0)} dropped",
            f"- **PAINS flagged:** {screen_stats.get('pains_dropped', 0)} dropped",
            f"- **QED errors:** {screen_stats.get('qed_errors', 0)}",
            f"- **Survivors:** {screen_stats.get('survivors', '?')}",
            f"- **New survivors from diversification:** {screen_stats.get('diversified_survivors_added', 0)}",
            "",
        ]
    if xref_summary:
        lines += [
            "## External cross-reference",
            "",
            f"- **Requested compounds:** {xref_summary.get('requested', 0)}",
            f"- **Queried compounds:** {xref_summary.get('queried', 0)}",
            f"- **Scope mode:** {xref_summary.get('mode', 'all')} ({xref_summary.get('reason', 'N/A')})",
            f"- **Projected API time:** ~{xref_summary.get('projected_seconds', 0)}s",
            f"- **Found in ChEMBL:** {xref_summary.get('chembl_found', 0)}",
            f"- **Found in PubChem:** {xref_summary.get('pubchem_found', 0)}",
            "",
        ]
    lines += [
        "## Target dossier",
        "",
        dossier.replace("[[PMID:", "[PMID:").replace("]]", "]"),
        "",
    ]
    if grounding and grounding.get("ungrounded"):
        lines += [
            "**Grounding warning:** The following cited PMIDs were not in the "
            "provided source abstracts and may be hallucinated: "
            f"{', '.join(u['pmid'] for u in grounding['ungrounded'])}.",
            "",
        ]
    if citations:
        lines += ["### Citations", ""]
        for c in citations:
            lines.append(
                f"- [PMID:{c['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/) — {c['claim']}"
            )
        lines.append("")
    lines += [
        "## Ranked shortlist",
        "",
        "| Rank | Score | Confidence | ChEMBL ID | PubChem CID | SMILES | Rationale |",
        "|---|---|---|---|---|---|---|",
    ]
    xref_by_smiles = {}
    if enriched_rows:
        xref_by_smiles = {r.get("smiles", ""): r for r in enriched_rows}
    for r in ranked:
        xref = xref_by_smiles.get(r.get("smiles", ""), {})
        lines.append(
            f"| {r['rank']} | {r['score']:.3f} | {r['confidence']} | "
            f"{xref.get('chembl_id', '') or '-'} | {xref.get('pubchem_cid', '') or '-'} | "
            f"`{r['smiles']}` | {r['reason']} |"
        )
    lines += [
        "",
        "_Triage, not oracle — every candidate carries confidence and provenance; "
        "a human approved this shortlist before export._",
    ]
    path.write_text("\n".join(lines))


def export_all(
    run_dir: Path,
    target: str,
    dossier: str,
    citations: list[dict],
    ranked: list[dict],
    metric: dict | None,
    screen_stats: dict | None = None,
    grounding: dict | None = None,
    provenance: dict | None = None,
    progress_callback=None,
) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)

    _notify_progress(
        progress_callback,
        "start",
        "Starting export for CSV, SDF, and report outputs.",
    )

    enriched_rows, xref_summary = write_csv(
        run_dir / "shortlist.csv", ranked, progress_callback
    )

    _notify_progress(
        progress_callback,
        "sdf_prepare",
        "Preparing 3D structures for .sdf.",
    )
    write_sdf(run_dir / "shortlist.sdf", ranked)

    _notify_progress(
        progress_callback,
        "report_prepare",
        "Assembling report with rationale and citations.",
    )
    write_report(
        run_dir / "report.md",
        target,
        dossier,
        citations,
        ranked,
        metric,
        screen_stats=screen_stats,
        grounding=grounding,
        provenance=provenance,
        enriched_rows=enriched_rows,
        xref_summary=xref_summary,
    )

    _notify_progress(
        progress_callback,
        "finalize",
        "Finalizing downloads.",
    )

    xref_by_smiles = {
        row.get("smiles", ""): {
            "chembl_id": row.get("chembl_id", "") or "",
            "pubchem_cid": row.get("pubchem_cid", "") or "",
            "crossref_queried": bool(row.get("crossref_queried", False)),
        }
        for row in enriched_rows
        if row.get("smiles")
    }

    return {
        "xref_summary": xref_summary,
        "xref_by_smiles": xref_by_smiles,
        "artifacts": {
            "csv": str(run_dir / "shortlist.csv"),
            "sdf": str(run_dir / "shortlist.sdf"),
            "report": str(run_dir / "report.md"),
        },
        "ranked_count": len(ranked),
    }
