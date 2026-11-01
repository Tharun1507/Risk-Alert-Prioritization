import json, re, hashlib, tempfile, os
from collections import Counter
from alertgen import schema as S
from alertgen.build import write_all, generate, sha
from alertgen.common import Config
from alertgen.validate import validate, expected_from_anoms, load_csv_tables
from alertgen.evaluate import evaluate
from alertgen import rules as R


def test_volumes_exact(ds):
    cfg, T, _ = ds
    assert len(T["customers"]) == cfg.n_customers
    assert len(T["accounts"]) == cfg.n_accounts
    assert len(T["transactions"]) == cfg.n_transactions
    assert len(T["alerts"]) == cfg.n_alerts


def test_id_conventions(ds):
    _, T, _ = ds
    assert T["customers"][0]["customer_id"] == "CUS-000001"
    assert re.match(r"^ACC-CUS-\d{6}-01$", T["accounts"][0]["account_id"])
    assert T["transactions"][0]["transaction_id"] == "TXN-2026-00000001"
    assert T["alerts"][0]["alert_id"] == "ALT-2026-000001"
    assert T["cases"][0]["case_id"] == "CASE-2026-000001"
    for t in T["accounts"]:
        assert t["account_id"].startswith(f"ACC-{t['primary_customer_id']}-")


def test_validation_matches_labelled_anomalies_exactly(ds, tmp_path):
    _, T, truth = ds
    write_all(T, truth, str(tmp_path), {"csv"})
    TT = load_csv_tables(str(tmp_path))          # validate what a consumer would load
    rep = validate(TT, expected_from_anoms(truth["injected_anomalies"]))
    assert rep["summary"]["all_pass"], rep["summary"]["failed"]
    # every required edge case type is present and detected
    kinds = {a["issue_type"] for a in truth["injected_anomalies"]}
    assert kinds == {"TXN_REFERENCES_CLOSED_ACCOUNT", "NEGATIVE_BALANCE_NO_OVERDRAFT", "TRANSFER_LEGS_DO_NOT_RECONCILE",
                     "ALERT_HAS_NO_TRIGGERING_TRANSACTION", "CASE_DISPOSITION_PRECEDES_ALERT"}


def test_validator_catches_unlabelled_corruption(ds, tmp_path):
    _, T, truth = ds
    write_all(T, truth, str(tmp_path), {"csv"})
    TT = load_csv_tables(str(tmp_path))
    TT["transactions"][10]["balance_after"] += 1.0
    rep = validate(TT, expected_from_anoms(truth["injected_anomalies"]))
    assert not rep["summary"]["all_pass"] and "txn_balance_reconciles" in rep["summary"]["failed"]


def test_balances_reconcile(ds):
    _, T, _ = ds
    bal = {a["account_id"]: a["opening_balance"] for a in T["accounts"]}
    for t in T["transactions"]:
        bal[t["account_id"]] = round(bal[t["account_id"]] + (t["amount"] if t["direction"] == "CREDIT" else -t["amount"]), 2)
        assert abs(bal[t["account_id"]] - t["balance_after"]) < 0.006


def test_planted_patterns_detected_and_boundaries_silent(ds):
    _, T, truth = ds
    ev = evaluate(T, truth)
    d = ev["detection"]
    assert d["suspicious_recall"] == 1.0
    assert d["unexpected_alerts"] == 0
    assert d["boundary_no_alert_violations"] == 0
    labels = {s["label"] for s in truth["scenarios"]}
    assert {"SUSPICIOUS", "BENIGN_FALSE_POSITIVE", "BOUNDARY_NO_ALERT"} <= labels   # false positives represented


def test_every_alert_explainable(ds):
    _, T, _ = ds
    for a in T["alerts"]:
        assert a["explanation"] and json.loads(a["evidence"]) is not None
        comps = json.loads(a["score_components"])
        assert abs(a["adjustment_total"] - sum(c["points"] for c in comps)) < 1e-6
    ok = [a for a in T["alerts"] if a["trigger_txn_id"]]
    assert len(ok) == len(T["alerts"]) - 2 or len(ok) >= len(T["alerts"]) - 6


def test_rule_versioning_effective_dates(ds):
    _, T, _ = ds
    v = {r["rule_version_id"]: r for r in T["risk_rules"]}
    assert v["RULE-002-v1"]["effective_to"] == v["RULE-002-v2"]["effective_from"]
    for a in T["alerts"]:
        r = v[a["rule_version_id"]]
        assert r["effective_from"] <= a["alert_ts"] and (r["effective_to"] is None or a["alert_ts"] < r["effective_to"] + "ZZ")


def test_ground_truth_not_leaked_into_inputs(ds):
    _, T, truth = ds
    secret = {"scenario_id", "scenario_type", "label", "expected_alert", "expected_decision", "issue_type", "affected_checks", "ground_truth"}
    for name, t in S.TABLES.items():
        cols = {f["name"] for f in t["fields"]}
        assert not (cols & secret), name
    blob = json.dumps(T["transactions"][:2000]) + json.dumps(T["alerts"])
    assert "SCN-" not in blob and "ANOM-" not in blob


def test_rule_engine_never_reads_truth(ds):
    _, T, _ = ds
    base = {k: T[k] for k in ["customers", "kyc_profiles", "accounts", "merchants", "device_account_links",
                              "transactions", "transfer_links", "risk_rules"]}
    alerts, _ = R.evaluate(base)       # works with inputs only
    assert len(alerts) == len([a for a in T["alerts"] if a["trigger_txn_id"]])


def test_case_lifecycle_and_grouping(ds):
    _, T, _ = ds
    assert {a["case_id"] for a in T["alerts"]} == {c["case_id"] for c in T["cases"]}
    assert any(c["alert_count"] > 1 for c in T["cases"])           # related alerts grouped
    assert Counter(c["status"] for c in T["cases"])["CLOSED"] > 0


def test_reproducible_same_seed(tmp_path):
    cfg = Config(seed=20261022, scale=0.02)
    sums = []
    for i in range(2):
        T, truth, _ = generate(cfg, verbose=False)
        d = tmp_path / str(i)
        files = write_all(T, truth, str(d), {"csv", "sql"})
        sums.append({f: sha(str(d / f)) for f in files})
    assert sums[0] == sums[1]


def test_different_seed_differs():
    a, _, _ = generate(Config(seed=1, scale=0.02), verbose=False)
    b, _, _ = generate(Config(seed=2, scale=0.02), verbose=False)
    assert a["customers"][0]["full_name"] != b["customers"][0]["full_name"] or a["transactions"][5] != b["transactions"][5]
