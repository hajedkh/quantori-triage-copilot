#!/usr/bin/env python3
"""Download a DUD-E target -> id,smiles,label CSV. Stdlib only, no deps.

    ./get_data.py                    # EGFR, all ~35k
    ./get_data.py --limit 20000      # cap total, keeps every active
    ./get_data.py --target braf
"""

import argparse, gzip, random, sys, urllib.request

URL = "https://dude.docking.org/targets/{t}/{k}_final.ism"


def fetch(target, kind):
    url = URL.format(t=target, k=kind)
    print(f"  GET {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8", "ignore")
    except Exception as e:
        sys.exit(f"FAILED: {e}\n  check links at https://dude.docking.org/targets/{target}")
    smiles = [ln.split()[0] for ln in text.splitlines() if ln.strip()]
    print(f"  -> {len(smiles):,} {kind}")
    return smiles


ap = argparse.ArgumentParser()
ap.add_argument("--target", default="egfr")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("-o", "--out", default="data.csv")
a = ap.parse_args()

actives = fetch(a.target, "actives")
decoys = fetch(a.target, "decoys")

if not actives:
    sys.exit("FAIL: no actives -> recall metric would silently report 0/0")

random.seed(42)
if a.limit and len(actives) + len(decoys) > a.limit:
    decoys = random.sample(decoys, max(a.limit - len(actives), 0))

rows = [(s, "active") for s in actives] + [(s, "decoy") for s in decoys]
random.shuffle(rows)  # don't leave actives clustered at the top

with open(a.out, "w") as f:
    f.write("id,smiles,label\n")
    for i, (smi, lab) in enumerate(rows, 1):
        f.write(f"cand_{i:05d},{smi},{lab}\n")

print(f"\n{a.out}: {len(rows):,} molecules, {len(actives):,} actives")
print(f"est. screen time: {len(rows)*0.00277:.0f}s single-threaded")
print("".join(open(a.out).readlines()[:4]))