"""Validation: generic schema checks (PK/required/enum/pattern/unique/FK) + semantic business checks.

Labelled anomalies are *expected* violations. A check passes only when observed violations == the labelled set
(no unlabelled corruption, no missed injections).
"""
import csv
import json
import re
from collections import defaultdict
from datetime import timedelta

from . import schema as S
from .common import pts


def load_csv_tables(d):
    out = {}
    for name, t in S.TABLES.items():
        rows = []
        with open(f"{d}/csv/{name}.csv", newline="") as f:
            for r in csv.DictReader(f):
                row = {}
                for fd in t["fields"]:
                    v = r[fd["name"]]
                    if v == "":
                        row[fd["name"]] = None
                    elif fd["type"] == "number":
                        row[fd["name"]] = float(v)
                    elif fd["type"] == "integer":
                        row[fd["name"]] = int(v)
                    else:
                        row[fd["name"]] = v
                rows.append(row)
        out[name] = rows
    return out


def expected_from_anoms(anoms):
    exp = defaultdict(set)
    for a in anoms:
        for c in a.get("affected_checks", a.get("checks", [])):
            exp[c].add(a["entity_id"])
    return exp


def validate(T, expected=None):
    expected = expected or {}
    res = {}

    def rec(name, bad, kind="semantic"):
        bad = sorted(set(bad))
        exp = sorted(expected.get(name, set()))
        res[name] = dict(kind=kind, observed=len(bad), expected_labelled=len(exp),
                         unlabelled=sorted(set(bad) - set(exp))[:10], missed=sorted(set(exp) - set(bad))[:10],
                         status="PASS" if bad == exp else "FAIL")

    # ---------------- generic
    for name, t in S.TABLES.items():
        rows = T[name]
        key = lambda r: tuple(r[c] for c in t["pk"])
        seen, dup = set(), []
        for r in rows:
            k = key(r)
            if k in seen:
                dup.append(str(k))
            seen.add(k)
        rec(f"pk_unique:{name}", dup, "structural")
        bad_req, bad_enum, bad_pat = [], [], []
        for r in rows:
            for f in t["fields"]:
                v = r[f["name"]]
                if v is None:
                    if f["required"] and not f["ddl_null"]:
                        bad_req.append(f"{key(r)}.{f['name']}")
                    continue
                if f["allowed"] is not None and v not in f["allowed"]:
                    bad_enum.append(f"{key(r)}.{f['name']}={v}")
                if f["pattern"] and not re.match(f["pattern"], str(v)):
                    bad_pat.append(f"{key(r)}.{f['name']}")
        rec(f"required:{name}", bad_req, "structural")
        rec(f"enum:{name}", bad_enum, "structural")
        rec(f"id_pattern:{name}", bad_pat, "structural")
        for f in t["fields"]:
            if f["unique"] and t["pk"] != [f["name"]]:
                vals = [r[f["name"]] for r in rows if r[f["name"]] is not None]
                rec(f"unique:{name}.{f['name']}", [str(v) for v in {x for x in vals if vals.count(x) > 1}] if len(vals) < 3000
                    else [], "structural")
    pkset = {n: {tuple(r[c] for c in t["pk"]) for r in T[n]} for n, t in S.TABLES.items()}
    for name, t in S.TABLES.items():
        for col, pt, pc in t["fks"]:
            ref = {r[pc] for r in T[pt]}
            rec(f"fk:{name}.{col}->{pt}", [str(r[t['pk'][0]]) for r in T[name] if r[col] is not None and r[col] not in ref], "structural")

    # ---------------- indexes
    cust = {r["customer_id"]: r for r in T["customers"]}
    kyc = {r["customer_id"]: r for r in T["kyc_profiles"]}
    acct = {r["account_id"]: r for r in T["accounts"]}
    txns = sorted(T["transactions"], key=lambda r: (r["txn_ts"], r["transaction_id"]))
    txn = {r["transaction_id"]: r for r in txns}
    by_acct = defaultdict(list)
    for r in txns:
        by_acct[r["account_id"]].append(r)
    links = {(l["device_id"], l["account_id"]): l for l in T["device_account_links"]}

    # ---------------- date ordering
    bad = [a["account_id"] for a in acct.values() if a["open_date"] < cust[a["primary_customer_id"]]["customer_since"]
           or (a["close_date"] and a["close_date"] < a["open_date"])]
    rec("date_order:account_open_close_vs_customer", bad)
    rec("date_order:kyc", [k["kyc_id"] for k in kyc.values() if k["verified_date"] and (
        k["verified_date"] < cust[k["customer_id"]]["customer_since"] or (k["expiry_date"] and k["expiry_date"] < k["verified_date"]))])
    rec("date_order:txn_not_before_account_open", [t["transaction_id"] for t in txns if t["txn_ts"][:10] < acct[t["account_id"]]["open_date"]])
    rec("date_order:transfer_completed_after_initiated", [l["transfer_id"] for l in T["transfer_links"] if l["completed_at"] < l["initiated_at"]])
    rec("date_order:alert_after_trigger_txn", [a["alert_id"] for a in T["alerts"]
                                               if a["trigger_txn_id"] and a["trigger_txn_id"] in txn and a["alert_ts"] < txn[a["trigger_txn_id"]]["txn_ts"]])
    rec("txn_device_link_precedes_use", [t["transaction_id"] for t in txns if t["device_id"] and (
        (t["device_id"], t["account_id"]) not in links or links[(t["device_id"], t["account_id"])]["first_seen_at"] > t["txn_ts"])])

    # ---------------- ledger
    bad_rec, bad_dir, bad_amt = [], [], []
    for a, lst in by_acct.items():
        bal = acct[a]["opening_balance"]
        for t in lst:
            bal = round(bal + (t["amount"] if t["direction"] == "CREDIT" else -t["amount"]), 2)
            if abs(bal - t["balance_after"]) > 0.005:
                bad_rec.append(t["transaction_id"])
                bal = t["balance_after"]
    for t in txns:
        if S.DIRS[t["txn_type"]] != t["direction"]:
            bad_dir.append(t["transaction_id"])
        if t["amount"] <= 0:
            bad_amt.append(t["transaction_id"])
    rec("txn_balance_reconciles", bad_rec)
    rec("txn_direction_matches_type", bad_dir)
    rec("txn_amount_positive", bad_amt)
    rec("txn_purchase_has_merchant", [t["transaction_id"] for t in txns if t["txn_type"] in ("POS_PURCHASE", "ONLINE_PURCHASE") and not t["merchant_id"]])
    rec("txn_not_after_account_close", [t["transaction_id"] for t in txns if acct[t["account_id"]]["close_date"]
                                        and t["txn_ts"][:10] > acct[t["account_id"]]["close_date"]])
    rec("negative_balance_without_overdraft", [t["transaction_id"] for t in txns if t["direction"] == "DEBIT"
                                               and t["balance_after"] < -acct[t["account_id"]]["overdraft_limit"] - 0.005])
    rec("account_current_balance_derived", [a["account_id"] for a in acct.values() if abs(
        a["current_balance"] - (by_acct[a["account_id"]][-1]["balance_after"] if by_acct.get(a["account_id"]) else a["opening_balance"])) > 0.005])
    # transfers
    bad_tr = []
    tl = {l["transfer_id"]: l for l in T["transfer_links"]}
    for l in T["transfer_links"]:
        o, i = txn.get(l["out_txn_id"]), txn.get(l["in_txn_id"])
        if not o or not i or o["direction"] != "DEBIT" or i["direction"] != "CREDIT" or abs(o["amount"] - i["amount"]) > 0.005 \
                or abs(l["amount"] - o["amount"]) > 0.005 or o["account_id"] != l["source_account_id"] \
                or i["account_id"] != l["dest_account_id"] or l["source_account_id"] == l["dest_account_id"] \
                or o["transfer_id"] != l["transfer_id"] or i["transfer_id"] != l["transfer_id"]:
            bad_tr.append(l["transfer_id"])
    rec("transfer_legs_reconcile", bad_tr)
    rec("transfer_txns_have_link", [t["transaction_id"] for t in txns if t["transfer_id"] and t["transfer_id"] not in tl])

    # ---------------- alerts
    br = defaultdict(list)
    for b in T["alert_transactions"]:
        br[b["alert_id"]].append(b)
    bad_trig, bad_score = [], []
    for a in T["alerts"]:
        tt = a["trigger_txn_id"]
        if not tt or tt not in txn or txn[tt]["account_id"] != a["account_id"] or \
                not any(b["transaction_id"] == tt and b["evidence_role"] == "TRIGGER" for b in br[a["alert_id"]]):
            bad_trig.append(a["alert_id"])
        comps = json.loads(a["score_components"])
        adj = sum(c["points"] for c in comps)
        sc = min(100.0, max(0.0, a["base_score"] + adj))
        from .rules import band
        if abs(adj - a["adjustment_total"]) > 0.005 or abs(sc - a["risk_score"]) > 0.005 or band(a["risk_score"]) != a["priority_band"]:
            bad_score.append(a["alert_id"])
    rec("alert_has_trigger_txn", bad_trig)
    rec("alert_score_arithmetic", bad_score)
    seen, dup = set(), []
    for a in T["alerts"]:
        k = (a["rule_version_id"], a["account_id"], a["trigger_txn_id"])
        if a["trigger_txn_id"] and k in seen:
            dup.append(a["alert_id"])
        seen.add(k)
    rec("duplicate_alerts", dup)
    seen, dup = set(), []
    for t in txns:
        k = (t["account_id"], t["txn_ts"], t["txn_type"], t["amount"], t["merchant_id"], t["transfer_id"])
        if k in seen:
            dup.append(t["transaction_id"])
        seen.add(k)
    rec("duplicate_transactions", dup)
    seen, dup = set(), []
    for c in T["customers"]:
        k = (c["full_name"], c["date_of_birth"], c["city"])
        if k in seen:
            dup.append(c["customer_id"])
        seen.add(k)
    rec("duplicate_customers", dup)

    # ---------------- cases / lifecycle
    alerts_by_case = defaultdict(list)
    for a in T["alerts"]:
        alerts_by_case[a["case_id"]].append(a)
    acts = defaultdict(list)
    for a in T["analyst_actions"]:
        acts[a["case_id"]].append(a)
    b1, b2, b3, b4, b5, b6, b7 = [], [], [], [], [], [], []
    for c in T["cases"]:
        cid = c["case_id"]
        al = alerts_by_case[cid]
        al_ts = [a["alert_ts"] for a in al]
        ac = sorted(acts[cid], key=lambda a: (a["action_ts"], a["action_id"]))
        if c["alert_count"] != len(al):
            b6.append(cid)
        if any(a["action_type"] == "CLOSE_CASE" and al_ts and a["action_ts"] < max(al_ts) for a in ac):
            b1.append(cid)
        if any(a["action_ts"] < c["opened_at"] for a in ac):
            b2.append(cid)
        if c["closed_at"] and c["closed_at"] < c["opened_at"]:
            b3.append(cid)
        cur = "OPEN"
        for a in ac:
            if a["from_status"] != cur or (a["to_status"], a["from_status"]) not in {(t, f) for f, t in S.ACTION_TRANSITIONS[a["action_type"]]} \
                    or (a["action_type"] == "CLOSE_CASE") != bool(a["disposition"]):
                b4.append(cid)
                break
            cur = a["to_status"]
        if cur != c["status"] or (c["status"] == "CLOSED") != bool(c["disposition"]):
            b5.append(cid)
        if ac and c["assigned_user_id"] is None:
            b7.append(cid)
    rec("case_disposition_not_before_alerts", b1)
    rec("case_actions_not_before_open", b2)
    rec("case_closed_not_before_opened", b3)
    rec("case_action_lifecycle", b4)
    rec("case_status_derived_from_actions", b5)
    rec("case_alert_count", b6)
    rec("case_assigned_after_action", b7)
    rec("case_has_alerts", [c["case_id"] for c in T["cases"] if not alerts_by_case[c["case_id"]]])
    # account lifecycle
    ev = defaultdict(list)
    for e in T["account_status_events"]:
        ev[e["account_id"]].append(e)
    b8, b9, b10 = [], [], []
    for a in acct.values():
        es = sorted(ev[a["account_id"]], key=lambda e: (e["event_ts"], e["event_id"]))
        prev = None
        for e in es:
            if e["from_status"] != prev or (e["from_status"], e["to_status"]) not in S.ACCOUNT_TRANSITIONS:
                b8.append(a["account_id"])
                break
            prev = e["to_status"]
        if not es or prev != a["status"] or (a["status"] == "CLOSED") != bool(a["close_date"]):
            b9.append(a["account_id"])
        fz = next((e["event_ts"] for e in es if e["to_status"] == "FROZEN"), None)
        if fz and any(t["txn_ts"] > fz for t in by_acct.get(a["account_id"], [])):
            b10.append(a["account_id"])
    rec("account_status_lifecycle", b8)
    rec("account_status_derived_from_events", b9)
    rec("no_txn_after_freeze", b10)
    # customer status derivation
    own = defaultdict(list)
    for a in acct.values():
        own[a["primary_customer_id"]].append(a["status"])
    rec("customer_status_derived", [c["customer_id"] for c in T["customers"]
                                    if (c["status"] == "CLOSED") != (bool(own[c["customer_id"]]) and all(s == "CLOSED" for s in own[c["customer_id"]]))])

    failed = [k for k, v in res.items() if v["status"] == "FAIL"]
    return dict(summary=dict(checks=len(res), passed=len(res) - len(failed), failed=failed,
                             labelled_violations_detected=sum(v["expected_labelled"] for v in res.values() if v["status"] == "PASS"),
                             all_pass=not failed), checks=res)
