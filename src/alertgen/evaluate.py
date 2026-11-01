"""Measurable outputs: detection recall/precision vs ground truth, ranking quality, case/analyst KPIs.
Ground truth is read here only - never by the generator's rule engine or case logic."""
import json
from collections import Counter, defaultdict

from .common import pts, AS_OF


def auc(pos, neg):
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    vals = [v for v, _ in allv]
    while i < len(vals):
        j = i
        while j < len(vals) and vals[j] == vals[i]:
            j += 1
        ranks[vals[i]] = (i + 1 + j) / 2
        i = j
    rs = sum(ranks[v] for v in pos)
    return round((rs - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 4)


def p_at_k(rows, k):
    top = rows[:k]
    return round(sum(r["y"] for r in top) / max(1, len(top)), 4)


def evaluate(T, truth):
    scn_of_txn = {}
    for s in truth["scenarios"]:
        for t in s["txn_ids"]:
            scn_of_txn[t] = s
    scn_by_id = {s["scenario_id"]: s for s in truth["scenarios"]}
    txn = {t["transaction_id"]: t for t in T["transactions"]}
    alerts = T["alerts"]
    rules = {r["rule_version_id"]: r["rule_id"] for r in T["risk_rules"]}
    matched = defaultdict(list)
    unexpected, no_trigger = [], []
    for a in alerts:
        if not a["trigger_txn_id"]:
            no_trigger.append(a["alert_id"]); continue
        s = scn_of_txn.get(a["trigger_txn_id"])
        if s is None or not s["expected_alert"] or s["rule_id"] != rules[a["rule_version_id"]]:
            unexpected.append(a["alert_id"])
        else:
            matched[s["scenario_id"]].append(a)
    # ---- detection
    per_type = {}
    for s in truth["scenarios"]:
        d = per_type.setdefault(s["scenario_type"], dict(label=s["label"], scenarios=0, expected_alerts=0, alerts_found=0,
                                                         scenarios_detected=0, false_alerts_on_no_alert_scenarios=0))
        d["scenarios"] += 1
        if s["expected_alert"]:
            d["expected_alerts"] += s["expected_alert_count"]
            d["alerts_found"] += len(matched[s["scenario_id"]])
            d["scenarios_detected"] += 1 if len(matched[s["scenario_id"]]) >= s["expected_alert_count"] else 0
        else:
            d["false_alerts_on_no_alert_scenarios"] += sum(1 for a in alerts if a["trigger_txn_id"] in set(s["txn_ids"]))
    for d in per_type.values():
        d["recall"] = round(d["scenarios_detected"] / d["scenarios"], 4) if d["expected_alerts"] else None
    sus = [s for s in truth["scenarios"] if s["label"] == "SUSPICIOUS"]
    sus_det = sum(1 for s in sus if len(matched[s["scenario_id"]]) >= s["expected_alert_count"])
    # ---- ranking
    rows = []
    for sid, al in matched.items():
        s = scn_by_id[sid]
        for a in al:
            rows.append(dict(y=1 if s["label"] == "SUSPICIOUS" else 0, score=a["risk_score"],
                             amount=txn[a["trigger_txn_id"]]["amount"], ts=a["alert_ts"], band=a["priority_band"]))
    by_score = sorted(rows, key=lambda r: -r["score"])
    by_amt = sorted(rows, key=lambda r: -r["amount"])
    by_fifo = sorted(rows, key=lambda r: r["ts"])
    pos = [r for r in rows if r["y"]]; neg = [r for r in rows if not r["y"]]
    prev = len(pos) / max(1, len(rows))
    k20 = max(1, int(0.2 * len(rows)))
    ranking = dict(
        labelled_alerts=len(rows), suspicious_alerts=len(pos), benign_false_positive_alerts=len(neg),
        base_rate_suspicious=round(prev, 4),
        auc_risk_score=auc([r["score"] for r in pos], [r["score"] for r in neg]),
        auc_amount_only=auc([r["amount"] for r in pos], [r["amount"] for r in neg]),
        auc_fifo_time=auc([-pts(r["ts"]).timestamp() for r in pos], [-pts(r["ts"]).timestamp() for r in neg]),
        precision_at_50=dict(risk_score=p_at_k(by_score, 50), amount_only=p_at_k(by_amt, 50), fifo=p_at_k(by_fifo, 50)),
        precision_at_100=dict(risk_score=p_at_k(by_score, 100), amount_only=p_at_k(by_amt, 100), fifo=p_at_k(by_fifo, 100)),
        top20pct_review=dict(k=k20, precision_risk_score=p_at_k(by_score, k20), precision_amount_only=p_at_k(by_amt, k20),
                             precision_fifo=p_at_k(by_fifo, k20)),
        analyst_effort_saved=dict(note="share of benign false-positive alerts that land in band P3 (can be batch-reviewed)",
                                  value=round(sum(1 for r in neg if r["band"] == "P3") / max(1, len(neg)), 4)),
        band_vs_label={b: dict(suspicious=sum(1 for r in pos if r["band"] == b), benign=sum(1 for r in neg if r["band"] == b))
                       for b in ["P1", "P2", "P3"]},
        mean_score=dict(suspicious=round(sum(r["score"] for r in pos) / max(1, len(pos)), 2),
                        benign=round(sum(r["score"] for r in neg) / max(1, len(neg)), 2)))
    # ---- case / analyst KPIs
    alert_case = {a["alert_id"]: a["case_id"] for a in alerts}
    alert_label = {}
    for sid, al in matched.items():
        for a in al:
            alert_label[a["alert_id"]] = scn_by_id[sid]["expected_decision"]
    case_exp = defaultdict(list)
    for aid, d in alert_label.items():
        case_exp[alert_case[aid]].append(d)
    agree = tot = 0
    conf = Counter()
    for c in T["cases"]:
        if c["status"] == "CLOSED" and c["case_id"] in case_exp:
            exp = "CONFIRMED_SUSPICIOUS" if "CONFIRMED_SUSPICIOUS" in case_exp[c["case_id"]] else "FALSE_POSITIVE"
            conf[(exp, c["disposition"])] += 1
            tot += 1; agree += exp == c["disposition"]
    closed = [c for c in T["cases"] if c["status"] == "CLOSED"]
    breach = sum(1 for c in closed if c["closed_at"] > c["sla_due_at"]) + sum(
        1 for c in T["cases"] if c["status"] != "CLOSED" and c["sla_due_at"] < AS_OF.isoformat(timespec="seconds"))
    hrs = defaultdict(list)
    for c in closed:
        if c["closed_at"] >= c["opened_at"]:
            hrs[c["priority_band"]].append((pts(c["closed_at"]) - pts(c["opened_at"])).total_seconds() / 3600)
    cases = dict(
        total=len(T["cases"]), by_status=dict(Counter(c["status"] for c in T["cases"])),
        by_priority=dict(Counter(c["priority_band"] for c in T["cases"])),
        by_disposition=dict(Counter(c["disposition"] for c in closed)),
        false_positive_rate_closed=round(sum(1 for c in closed if c["disposition"] == "FALSE_POSITIVE") / max(1, len(closed)), 4),
        sla_breach_rate=round(breach / max(1, len(T["cases"])), 4),
        mean_hours_to_close_by_band={b: round(sum(v) / len(v), 1) for b, v in sorted(hrs.items())},
        analyst_vs_ground_truth_agreement=dict(closed_cases_with_label=tot, agree=agree,
                                               accuracy=round(agree / tot, 4) if tot else None,
                                               confusion={f"expected={e}|analyst={a}": n for (e, a), n in sorted(conf.items())}),
        multi_alert_cases=sum(1 for c in T["cases"] if c["alert_count"] > 1),
        max_alerts_in_case=max((c["alert_count"] for c in T["cases"]), default=0))
    return dict(
        volumes={k: len(v) for k, v in T.items()},
        detection=dict(suspicious_scenarios=len(sus), suspicious_detected=sus_det,
                       suspicious_recall=round(sus_det / max(1, len(sus)), 4),
                       unexpected_alerts=len(unexpected), unexpected_alert_ids=unexpected[:10],
                       alerts_without_trigger=len(no_trigger),
                       boundary_no_alert_violations=sum(d["false_alerts_on_no_alert_scenarios"] for d in per_type.values()),
                       precision_vs_expected=round(len(rows) / max(1, len(alerts) - len(no_trigger)), 4),
                       per_scenario_type=per_type),
        ranking=ranking, cases=cases,
        alerts_by_rule=dict(Counter(rules[a["rule_version_id"]] for a in alerts)),
        alerts_by_band=dict(Counter(a["priority_band"] for a in alerts)))


def to_markdown(m):
    d, r, c = m["detection"], m["ranking"], m["cases"]
    L = ["# Evaluation report", "", "## Volumes", "", "| Table | Rows |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in m["volumes"].items()]
    L += ["", "## Detection vs ground truth", "",
          f"- Suspicious planted scenarios detected: **{d['suspicious_detected']}/{d['suspicious_scenarios']}** (recall {d['suspicious_recall']})",
          f"- Unexpected alerts (no matching planted scenario): **{d['unexpected_alerts']}**",
          f"- Boundary no-alert scenarios wrongly alerted: **{d['boundary_no_alert_violations']}**",
          f"- Alerts without trigger (injected anomaly): {d['alerts_without_trigger']}",
          "", "| Scenario type | Label | Scenarios | Expected alerts | Found | Recall |", "|---|---|---|---|---|---|"]
    for k, v in d["per_scenario_type"].items():
        L.append(f"| {k} | {v['label']} | {v['scenarios']} | {v['expected_alerts']} | {v['alerts_found']} | {v['recall']} |")
    L += ["", "## Ranking quality (explainable risk score vs naive orderings)", "",
          f"Labelled alerts: {r['labelled_alerts']} ({r['suspicious_alerts']} suspicious / {r['benign_false_positive_alerts']} benign false positives; base rate {r['base_rate_suspicious']}).", "",
          "| Metric | Risk score | Amount only | FIFO (time) |", "|---|---|---|---|",
          f"| AUC | {r['auc_risk_score']} | {r['auc_amount_only']} | {r['auc_fifo_time']} |",
          f"| Precision@50 | {r['precision_at_50']['risk_score']} | {r['precision_at_50']['amount_only']} | {r['precision_at_50']['fifo']} |",
          f"| Precision@100 | {r['precision_at_100']['risk_score']} | {r['precision_at_100']['amount_only']} | {r['precision_at_100']['fifo']} |",
          f"| Precision in top 20% of alerts | {r['top20pct_review']['precision_risk_score']} | {r['top20pct_review']['precision_amount_only']} | {r['top20pct_review']['precision_fifo']} |",
          f"| False positives sent to low-priority band P3 | {r['analyst_effort_saved']['value']} | - | - |",
          "", f"Mean score - suspicious {r['mean_score']['suspicious']}, benign false positive {r['mean_score']['benign']}.", "",
          "| Band | Suspicious | Benign FP |", "|---|---|---|"] + [f"| {b} | {v['suspicious']} | {v['benign']} |" for b, v in r["band_vs_label"].items()]
    L += ["", "## Cases and analyst workflow", "", f"- Cases: {c['total']} (multi-alert cases: {c['multi_alert_cases']}, largest: {c['max_alerts_in_case']} alerts)",
          f"- Status: {c['by_status']}", f"- Priority: {c['by_priority']}", f"- Dispositions: {c['by_disposition']}",
          f"- False-positive rate among closed cases: {c['false_positive_rate_closed']}", f"- SLA breach rate: {c['sla_breach_rate']}",
          f"- Mean hours to close by band: {c['mean_hours_to_close_by_band']}",
          f"- Analyst disposition vs ground truth: {c['analyst_vs_ground_truth_agreement']}", ""]
    return "\n".join(L)
