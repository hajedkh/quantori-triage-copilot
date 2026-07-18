"""3D conformer generation for the on-demand Mol* viewer.

SMILES carries no 3D coordinates — a 2D depiction is just a layout, not a
structure. Producing something Mol* can render requires actually generating
a conformer (embed + optimize) first. This is separate from chem.py on
purpose (see CLAUDE.md file-ownership notes): chem.py is the deterministic
reliability anchor for the triage pipeline itself, and this module has
nothing to do with that pipeline — it's called on-demand, purely for the
frontend's optional 3D viewer, never during a run.

Every failure mode (bad SMILES, embed failure, missing force-field params)
degrades to None. Callers must treat None as "fall back to the existing 2D
depiction" — never a crash, never a blank viewer.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem

from . import chem

# Fixed seed: ETKDG embedding is stochastic by default, and re-opening the
# same molecule's 3D view should look the same every time.
_EMBED_SEED = 0xC0FFEE


def to_molblock_3d(smiles: str) -> str | None:
    """SMILES -> a 3D MOL block (V2000, explicit Hs, one conformer), or None
    on any failure. Never raises."""
    try:
        mol = chem.parse(smiles)
        if mol is None:
            return None
        mol = Chem.AddHs(mol)

        params = AllChem.ETKDGv3()
        params.randomSeed = _EMBED_SEED
        embed_result = AllChem.EmbedMolecule(mol, params)
        if embed_result == -1:
            # Embedding failed outright (e.g. no reasonable geometry found) —
            # nothing to optimize, bail out now rather than touching a
            # molecule with no conformer.
            return None

        if AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol)
        else:
            AllChem.UFFOptimizeMolecule(mol)

        return Chem.MolToMolBlock(mol)
    except Exception:
        return None
