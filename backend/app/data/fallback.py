"""Bundled known actives + target aliases.

Used as a fallback when the ChEMBL API is unreachable so the demo never breaks.
These are real, well-known inhibitor SMILES for each target.
"""

# target name (upper) -> (chembl_id, pref_name)
TARGET_ALIASES = {
    "EGFR": ("CHEMBL203", "Epidermal growth factor receptor erbB1"),
    "BRAF": ("CHEMBL5145", "Serine/threonine-protein kinase B-raf"),
    "ABL1": ("CHEMBL1862", "Tyrosine-protein kinase ABL"),
}

# target chembl_id -> list of known-active SMILES (real inhibitors)
FALLBACK_ACTIVES = {
    "CHEMBL203": [  # EGFR
        "COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1",  # gefitinib
        "C#Cc1cccc(Nc2ncnc3cc(OCCOC)c(OCCOC)cc23)c1",  # erlotinib
        "CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1OC1CCOC1",  # afatinib
        "COc1cc2ncnc(Nc3ccc(Br)cc3F)c2cc1OCC1CCN(C)CC1",  # vandetanib
        "C=CC(=O)Nc1cccc(Nc2nc(Nc3ccc(N4CCN(C)CC4)cc3OC)ncc2Cl)c1",  # osimertinib-like
        "COc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1OCCCN1CCOCC1",  # quinazoline analog
        "Nc1ncnc2c1c(-c1ccc(O)cc1)cn2C1CCCC1",  # pyrrolopyrimidine
        "COc1cc2ncnc(Nc3ccccc3)c2cc1OC",  # simplified quinazoline
        "Cc1ccc(Nc2ncnc3[nH]ccc23)cc1S(N)(=O)=O",  # pyrrolopyrimidine sulfonamide
        "COc1ccc(Nc2ncnc3cc(OC)c(OC)cc23)cc1Cl",  # dimethoxy quinazoline
    ],
    "CHEMBL5145": [  # BRAF
        "CCCS(=O)(=O)Nc1ccc(F)c(C(=O)c2c[nH]c3ncc(-c4ccc(Cl)cc4)cc23)c1F",  # vemurafenib
        "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1",  # dabrafenib-like
        "COc1cc2c(Nc3ccc(Br)cc3)ncnc2cc1OC",
    ],
    "CHEMBL1862": [  # ABL1
        "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1",  # imatinib-like
        "Cc1cnc(Nc2ccc(OCCN3CCCC3)cc2)nc1-c1cccnc1",
    ],
}
