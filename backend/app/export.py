"""Export writers — CSV, SDF, and a Markdown report with the dossier + citations."""

from __future__ import annotations
import csv
from pathlib import Path
from rdkit import Chem


def write_csv(path: Path, ranked: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rank",
                "smiles",
                "score",
                "confidence",
                "nearest_active",
                "max_similarity",
                "reason",
                "evidence_used",
            ]
        )
        for r in ranked:
            evidence = (
                "; ".join(r.get("evidence_used", [])) if r.get("evidence_used") else ""
            )
            w.writerow(
                [
                    r["rank"],
                    r["smiles"],
                    r["score"],
                    r["confidence"],
                    r["nearest_active"],
                    r["max_similarity"],
                    r["reason"],
                    evidence,
                ]
            )


def write_sdf(path: Path, ranked: list[dict]) -> None:
    w = Chem.SDWriter(str(path))
    for r in ranked:
        mol = Chem.MolFromSmiles(r["smiles"])
        if mol is None:
            continue
        mol.SetProp("rank", str(r["rank"]))
        mol.SetProp("score", f'{r["score"]:.3f}')
        mol.SetProp("confidence", r["confidence"])
        mol.SetProp("nearest_active", str(r["nearest_active"]))
        w.write(mol)
    w.close()


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
