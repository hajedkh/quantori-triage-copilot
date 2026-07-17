"""Export writers — CSV, SDF, and a Markdown report with the dossier + citations.

The CSV is the primary downstream artifact for cheminformatics triage.
It includes the full Lipinski Ro5 breakdown, extended ADME descriptors,
structural keys (InChIKey, Murcko scaffold), and scoring provenance,
all computed fresh from the ranked SMILES at export time.
"""

from __future__ import annotations
import csv
from pathlib import Path
from rdkit import Chem

# Import chem for extended_descriptors — resolve relative or absolute
try:
    from . import chem
except ImportError:
    import chem  # type: ignore


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
    # --- Provenance ---
    "is_known_active",
    "reason",
    "evidence_used",
]


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
        "score": r.get("score", ""),
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
        # provenance
        "is_known_active": r.get("is_known_active", ""),
        "reason": r.get("reason", ""),
        "evidence_used": (
            "; ".join(r.get("evidence_used", [])) if r.get("evidence_used") else ""
        ),
    }


def write_csv(path: Path, ranked: list[dict]) -> None:
    """Write the comprehensive triage CSV with full cheminformatics data."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in ranked:
            w.writerow(_enrich_row(r))


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


def write_sdf(path: Path, ranked: list[dict], gen_3d: bool = True) -> None:
    """Write SDF with 3D conformers (ETKDG + MMFF94) and all computed properties.

    When gen_3d is True (default), each molecule gets a force-field-optimized
    3D conformer. Molecules that fail embedding are written as 2D.
    """
    w = Chem.SDWriter(str(path))
    n_3d = n_2d = 0

    for r in ranked:
        mol = chem.parse(r["smiles"])
        if mol is None:
            continue

        ext = chem.extended_descriptors(mol)

        # Attempt 3D conformer generation
        mol_3d = None
        if gen_3d:
            mol_3d = _embed_3d(mol)

        if mol_3d is not None:
            out_mol = mol_3d
            out_mol.SetProp("_3d_status", "MMFF94_optimized")
            n_3d += 1
        else:
            out_mol = mol
            out_mol.SetProp("_3d_status", "2D_only")
            n_2d += 1

        # Ranking fields
        out_mol.SetProp("rank", str(r.get("rank", "")))
        out_mol.SetProp("score", f'{r["score"]:.3f}')
        out_mol.SetProp("confidence", r.get("confidence", ""))
        out_mol.SetProp("nearest_active", str(r.get("nearest_active", "")))

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
            f"- **Invalid SMILES:** {screen_stats.get('invalid', 0)} dropped",
            f"- **Lipinski failures:** {screen_stats.get('lipinski_dropped', 0)} dropped",
            f"- **PAINS flagged:** {screen_stats.get('pains_dropped', 0)} dropped",
            f"- **QED errors:** {screen_stats.get('qed_errors', 0)}",
            f"- **Survivors:** {screen_stats.get('survivors', '?')}",
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
            + ", ".join(u["pmid"] for u in grounding["ungrounded"])
            + ".",
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
        "| Rank | Score | Confidence | SMILES | Rationale |",
        "|---|---|---|---|---|",
    ]
    for r in ranked:
        lines.append(
            f"| {r['rank']} | {r['score']:.2f} | {r['confidence']} | `{r['smiles']}` | {r['reason']} |"
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
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_csv(run_dir / "shortlist.csv", ranked)
    write_sdf(run_dir / "shortlist.sdf", ranked)
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
    )
