"""Regenerate the dataset twice with the same seed and compare row counts + SHA-256 checksums (Step 10)."""
import argparse, json, os, sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alertgen.build import main as build


def run(scale, seed, formats):
    d = tempfile.mkdtemp()
    build(["--seed", str(seed), "--scale", str(scale), "--out", d, "--formats", formats])
    m = json.load(open(f"{d}/manifest.json"))
    return {f: (v["sha256"], v["rows"]) for f, v in m["files"].items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--formats", default="csv,json,sql")
    ap.add_argument("--report", default="docs/reproducibility_report.json")
    a = ap.parse_args()
    r1, r2 = run(a.scale, a.seed, a.formats), run(a.scale, a.seed, a.formats)
    rep = dict(seed=a.seed, scale=a.scale, runs=2, files_compared=len(r1), identical=r1 == r2,
               mismatches=[f for f in r1 if r1[f] != r2.get(f)],
               files={f: dict(sha256=r1[f][0], rows=r1[f][1], run2_matches=r1[f] == r2.get(f)) for f in sorted(r1)})
    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    json.dump(rep, open(a.report, "w"), indent=1)
    print(f"reproducible={rep['identical']} files={rep['files_compared']} mismatches={rep['mismatches']}")
    sys.exit(0 if rep["identical"] else 1)
