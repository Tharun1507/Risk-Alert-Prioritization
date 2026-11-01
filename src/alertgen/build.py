"""End-to-end build: generate -> rules -> cases -> ground truth -> export -> validate -> evaluate.

    python -m alertgen.build --seed 20261022 --scale 1.0 --out data/full
    python -m alertgen.build --preset starter --out data/starter
"""
import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

from . import schema as S
from . import rules as R
from . import casework
from .common import Config, ts
from .datagen import World
from .validate import validate, load_csv_tables, expected_from_anoms
from .evaluate import evaluate, to_markdown

SRC_REFS = [
    {"name": "FFIEC BSA/AML Examination Manual (typologies, schema inspiration only)", "url": "https://bsaaml.ffiec.gov/manual"},
    {"name": "FATF Recommendations (circular/layering concepts, inspiration only)",
     "url": "https://www.fatf-gafi.org/en/topics/fatf-recommendations.html"},
]


def _base_tables(w):
    link_rows = [dict(device_id=lk["device_id"], account_id=lk["account_id"], first_seen_at=ts(lk["first_seen_at"]),
                      last_seen_at=lk["last_seen_at"]) for _, lk in sorted(w.links.items())]
    admin = next(u["user_id"] for u in w.users if u["role"] == "SYS_ADMIN")
    return dict(customers=w.customers, kyc_profiles=w.kyc, merchants=w.merchants,
                accounts=[dict(account_id=a, primary_customer_id=w.A[a]["cust"]) for a in w.order],
                device_account_links=link_rows, transactions=w.txns, transfer_links=w.transfers,
                risk_rules=R.rule_rows(admin))


def generate(cfg, verbose=True):
    fp = 0
    for it in range(6):
        w = World(cfg, fp).build()
        base = _base_tables(w)
        alerts, ix = R.evaluate(base)
        delta = (cfg.n_alerts - cfg.n_edge) - len(alerts)
        if verbose:
            print(f"  calibration pass {it}: rule alerts={len(alerts)} target={cfg.n_alerts - cfg.n_edge} (fp_extra={fp})", flush=True)
        if delta == 0:
            break
        fp += delta
    out = casework.run(w, base, alerts, ix)
    T = {}
    own = dict(customers=w.customers, kyc_profiles=w.kyc, account_parties=w.parties, merchants=w.merchants,
               analyst_users=w.users, transactions=w.txns, transfer_links=w.transfers, risk_rules=base["risk_rules"])
    for name in S.TABLES:
        T[name] = own[name] if name in own else out[name]
    scen = [dict(scenario_id=s["scenario_id"], scenario_type=s["scenario_type"], label=s["label"], rule_id=s["rule_id"],
                 expected_alert=s["expected_alert"], expected_alert_count=s["expected_alert_count"],
                 expected_decision=s["expected_decision"], account_ids=s["accounts"], txn_ids=s["txn_ids"], note=s["note"])
            for s in w.truth]
    anoms = []
    for i, a in enumerate(sorted(w.anoms, key=lambda a: (a["issue_type"], a["entity_id"])), 1):
        anoms.append(dict(anomaly_id=f"ANOM-{i:04d}", issue_type=a["issue_type"], table=a["table"], entity_id=a["entity_id"],
                          account_id=a.get("account"), related_ids=a.get("related", []), affected_checks=a["checks"]))
    truth = dict(scenarios=scen, injected_anomalies=anoms)
    return T, truth, dict(fp_extra=fp)


# ------------------------------------------------------------------ export
def _fmt(v, typ):
    if v is None:
        return ""
    if typ == "number":
        return f"{float(v):.2f}"
    if typ == "integer":
        return str(int(v))
    return str(v)


def _sql_lit(v, typ):
    if v is None:
        return "NULL"
    if typ == "number":
        return f"{float(v):.2f}"
    if typ in ("integer", "boolean"):
        return str(int(v))
    return "'" + str(v).replace("'", "''") + "'"


def write_all(T, truth, out, formats):
    os.makedirs(out, exist_ok=True)
    files = []
    if "csv" in formats:
        os.makedirs(f"{out}/csv", exist_ok=True)
        for n, t in S.TABLES.items():
            with open(f"{out}/csv/{n}.csv", "w", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow([x["name"] for x in t["fields"]])
                for r in T[n]:
                    w.writerow([_fmt(r[x["name"]], x["type"]) for x in t["fields"]])
            files.append(f"csv/{n}.csv")
    if "json" in formats:
        os.makedirs(f"{out}/json", exist_ok=True)
        for n, t in S.TABLES.items():
            with open(f"{out}/json/{n}.json", "w") as f:
                json.dump([{x["name"]: r[x["name"]] for x in t["fields"]} for r in T[n]], f, sort_keys=True, separators=(",", ":"))
            files.append(f"json/{n}.json")
    if "sql" in formats:
        os.makedirs(f"{out}/sql", exist_ok=True)
        with open(f"{out}/sql/schema.sql", "w") as f:
            f.write("-- Generated from src/alertgen/schema.py\n" + S.ddl())
        with open(f"{out}/sql/seed.sql", "w") as f:
            f.write("-- Synthetic seed data. Load after schema.sql. PRAGMA foreign_keys=OFF while loading (circular FKs).\n"
                    "PRAGMA foreign_keys=OFF;\nBEGIN;\n")
            for n, t in S.TABLES.items():
                cols = [x["name"] for x in t["fields"]]
                rows = T[n]
                for i in range(0, len(rows), 500):
                    vals = ",\n".join("(" + ",".join(_sql_lit(r[x["name"]], x["type"]) for x in t["fields"]) + ")"
                                      for r in rows[i:i + 500])
                    f.write(f"INSERT INTO {n} ({', '.join(cols)}) VALUES\n{vals};\n")
            f.write("COMMIT;\n")
        files += ["sql/schema.sql", "sql/seed.sql"]
    # ground truth: separate directory, never referenced by input tables
    os.makedirs(f"{out}/ground_truth", exist_ok=True)
    with open(f"{out}/ground_truth/ground_truth.json", "w") as f:
        json.dump(truth, f, sort_keys=True, indent=1)
    with open(f"{out}/ground_truth/scenarios.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["scenario_id", "scenario_type", "label", "rule_id", "expected_alert", "expected_alert_count",
                    "expected_decision", "n_accounts", "n_transactions", "note"])
        for s in truth["scenarios"]:
            w.writerow([s["scenario_id"], s["scenario_type"], s["label"], s["rule_id"], int(s["expected_alert"]),
                        s["expected_alert_count"], s["expected_decision"], len(s["account_ids"]), len(s["txn_ids"]), s["note"]])
    with open(f"{out}/ground_truth/scenario_entities.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["scenario_id", "entity_type", "entity_id"])
        for s in truth["scenarios"]:
            for a in s["account_ids"]:
                w.writerow([s["scenario_id"], "account", a])
            for t in s["txn_ids"]:
                w.writerow([s["scenario_id"], "transaction", t])
    with open(f"{out}/ground_truth/injected_anomalies.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["anomaly_id", "issue_type", "table", "entity_id", "account_id", "related_ids", "affected_checks"])
        for a in truth["injected_anomalies"]:
            w.writerow([a["anomaly_id"], a["issue_type"], a["table"], a["entity_id"], a["account_id"] or "",
                        "|".join(a["related_ids"]), "|".join(a["affected_checks"])])
    files += [f"ground_truth/{x}" for x in ["ground_truth.json", "scenarios.csv", "scenario_entities.csv", "injected_anomalies.csv"]]
    with open(f"{out}/data_dictionary.md", "w") as f:
        f.write(S.data_dictionary_md())
    files.append("data_dictionary.md")
    return files


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sqlite_check(out):
    con = sqlite3.connect(":memory:")
    con.executescript(open(f"{out}/sql/schema.sql").read())
    con.executescript(open(f"{out}/sql/seed.sql").read())
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    counts = {n: con.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in S.TABLES}
    # join sanity: every alert joins to rule, account, case; every case has >=1 alert
    j = con.execute("""SELECT COUNT(*) FROM alerts a JOIN risk_rules r USING(rule_version_id)
                       JOIN accounts x ON x.account_id=a.account_id JOIN cases c ON c.case_id=a.case_id""").fetchone()[0]
    con.close()
    return dict(foreign_key_violations=len(fk), row_counts=counts, alerts_fully_joinable=j)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--preset", choices=["starter", "full"], default=None)
    ap.add_argument("--out", default="data/full")
    ap.add_argument("--formats", default="csv,json,sql")
    a = ap.parse_args(argv)
    scale = a.scale if a.scale is not None else (0.05 if a.preset == "starter" else 1.0)
    cfg = Config(seed=a.seed, scale=scale)
    t0 = time.time()
    print(f"Generating seed={cfg.seed} scale={cfg.scale} -> {a.out}", flush=True)
    T, truth, info = generate(cfg)
    fmts = set(a.formats.split(","))
    fmts.add("csv")   # csv is required for round-trip validation
    files = write_all(T, truth, a.out, fmts)
    # validate + evaluate from the files on disk (what a consumer would load)
    TT = load_csv_tables(a.out)
    expected = expected_from_anoms(truth["injected_anomalies"])
    vr = validate(TT, expected)
    if "sql" in fmts:
        vr["sqlite_load"] = sqlite_check(a.out)
    json.dump(vr, open(f"{a.out}/validation_report.json", "w"), indent=1, sort_keys=True)
    ev = evaluate(TT, truth)
    json.dump(ev, open(f"{a.out}/evaluation_metrics.json", "w"), indent=1, sort_keys=True)
    open(f"{a.out}/evaluation_report.md", "w").write(to_markdown(ev))
    files += ["validation_report.json", "evaluation_metrics.json", "evaluation_report.md"]
    manifest = dict(
        generator_command=f"python -m alertgen.build --seed {cfg.seed} --scale {cfg.scale} --out <dir> --formats {a.formats}",
        seed=cfg.seed, scale=cfg.scale, config=cfg.to_dict(), calibration=info,
        external_data_used=[], schema_inspiration_references=SRC_REFS,
        provenance="100% synthetic. No external datasets, no real personal data; names are random first/last-name combinations.",
        calendar=dict(transactions="2026-01-01..2026-10-15", as_of="2026-10-22"),
        files={f: dict(sha256=sha(f"{a.out}/{f}"), bytes=os.path.getsize(f"{a.out}/{f}"),
                       rows=(len(T[f.split('/')[-1].split('.')[0]]) if f.startswith(("csv/", "json/")) else None))
               for f in sorted(files) if f not in ("validation_report.json",)},
        validation_all_pass=vr["summary"]["all_pass"])
    json.dump(manifest, open(f"{a.out}/manifest.json", "w"), indent=1, sort_keys=True)
    s = vr["summary"]
    print(f"Done in {time.time() - t0:.1f}s | validation: {s['passed']}/{s['checks']} checks pass "
          f"({s['labelled_violations_detected']} labelled violations detected) | failed={s['failed']}")
    d = ev["detection"]
    print(f"Detection: suspicious recall {d['suspicious_recall']}, unexpected alerts {d['unexpected_alerts']}, "
          f"AUC risk-score {ev['ranking']['auc_risk_score']}  alerts={len(T['alerts'])} cases={len(T['cases'])} txns={len(T['transactions'])}")
    return 0 if s["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
