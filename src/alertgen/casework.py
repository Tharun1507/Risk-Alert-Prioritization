"""Alerts -> cases -> analyst actions -> derived account/customer state, plus labelled alert/case anomalies.

Disposition policy (documented, deterministic - no random labels):
  1. A transfer ring (RULE-003 cycle length >= 3)            -> CONFIRMED_SUSPICIOUS
  2. case_score >= 60                                         -> CONFIRMED_SUSPICIOUS
  3. every alert carries the MITIGATION adjustment            -> FALSE_POSITIVE
  4. otherwise (mid band) analyst REQUEST_INFO, then          -> CONFIRMED if case_score >= 50 else FALSE_POSITIVE
Random numbers are used only for timing/assignment, never for the outcome.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, date

from .common import (AS_OF, WE, ts, pts, sub_rng, alt_id, case_id, act_id, ase_id)
from .rules import band, MIT

DELAYS = {"P1": (0.25, 2, 1, 12), "P2": (1, 8, 6, 48), "P3": (4, 48, 12, 120)}
BACKLOG_FROM = datetime(2026, 9, 25)   # month-end queue build-up: handling times x4 for cases opened later   # assign lo/hi, step lo/hi (hours)
SLA_H = {"P1": 24, "P2": 72, "P3": 168}


def decide(alerts, case_score):
    ring = any(a["rule_id"] == "RULE-003" and json.loads(a["evidence"]).get("cycle_len", 0) >= 3 for a in alerts)
    mitig = all(any(c["code"] == MIT for c in json.loads(a["score_components"])) for a in alerts)
    if ring:
        return "CONFIRMED_SUSPICIOUS", "transfer ring (cycle length >= 3)", False
    if case_score >= 60:
        return "CONFIRMED_SUSPICIOUS", f"case_score {case_score:g} >= 60", False
    if mitig:
        return "FALSE_POSITIVE", "all alerts mitigated (established verified customer with consistent history)", False
    if case_score >= 50:
        return "CONFIRMED_SUSPICIOUS", f"info request outcome: case_score {case_score:g} >= 50", True
    return "FALSE_POSITIVE", f"info request outcome: case_score {case_score:g} < 50", True


def account_events(world, txns):
    """Derive lifecycle events from the ledger (dormancy, reactivation) and closure dates."""
    ev = defaultdict(list)
    by = defaultdict(list)
    for t in txns:
        by[t["account_id"]].append(pts(t["txn_ts"]))
    for a, A in world.A.items():
        cd = A["close_date"]
        close_dt = datetime(cd.year, cd.month, cd.day, 17, 0, 0) if cd else None
        rows = [(A["open_dt"], None, "ACTIVE", "ACCOUNT_OPENED", "SYSTEM")]
        state = "ACTIVE"
        times = [t for t in by[a] if close_dt is None or t < close_dt]
        for prev, cur in zip(times, times[1:]):
            if (cur - prev).days >= 180:
                rows += [(prev + timedelta(days=180), "ACTIVE", "DORMANT", "INACTIVITY_180D", "SYSTEM"),
                         (cur, "DORMANT", "ACTIVE", "ACTIVITY_RESUMED", "SYSTEM")]
        if times:
            tail = times[-1] + timedelta(days=180)
            if tail <= AS_OF and (close_dt is None or tail < close_dt):
                rows.append((tail, "ACTIVE", "DORMANT", "INACTIVITY_180D", "SYSTEM"))
                state = "DORMANT"
        if close_dt:
            rows.append((close_dt, state, "CLOSED", "CUSTOMER_REQUEST", "SYSTEM"))
        ev[a] = [dict(ts=r[0], account_id=a, from_status=r[1], to_status=r[2], reason_code=r[3], source=r[4],
                      action_ref=None) for r in rows]
    return ev


def run(world, base_tables, alerts, ix):
    cfg = world.cfg
    rng = sub_rng(cfg.seed, "casework")
    txns = base_tables["transactions"]
    users = world.users
    ops = [u["user_id"] for u in users if u["role"] == "OPS_ANALYST"]
    revs = [u["user_id"] for u in users if u["role"] == "RISK_REVIEWER"]

    # ---- labelled anomaly: alerts without a triggering transaction (injected rule hits with no supporting fact)
    inj = []
    pool = sorted(a for a, A in world.A.items() if A["primary"] and A["close_date"] is None and ix.by_acct.get(a))
    for _ in range(cfg.n_edge):
        acct = rng.choice(pool)
        rid = rng.choice(["RULE-001", "RULE-002", "RULE-004"])
        when = datetime(2026, 4, 1) + timedelta(days=rng.randint(0, 190), hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
        v = ix.rule_at(rid, when)
        al = dict(rule_version_id=v["rule_version_id"], rule_id=rid, account_id=acct, trigger_txn_id=None, alert_ts=ts(when),
                  _dt=when, base_score=float(v["base_weight"]), adjustment_total=0.0, risk_score=float(v["base_weight"]),
                  priority_band=band(v["base_weight"]), group_key=None,
                  explanation=f"{rid} {v['rule_name']}: rule flagged by upstream feed; supporting transaction reference unavailable.",
                  evidence="{}", score_components="[]", _txns=[], _inj=True)
        alerts.append(al)
        inj.append(al)
    alerts.sort(key=lambda a: (a["_dt"], a["rule_id"], a["account_id"], a["trigger_txn_id"] or ""))
    for i, a in enumerate(alerts, 1):
        a["alert_id"] = alt_id(i)
    for a in inj:
        world.anoms.append(dict(issue_type="ALERT_HAS_NO_TRIGGERING_TRANSACTION", table="alerts", entity_id=a["alert_id"],
                                checks=["alert_has_trigger_txn"]))

    # ---- group alerts into cases (same customer <=14d, same device <=30d, same detected structure)
    n = len(alerts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    last_c, last_d, first_g = {}, {}, {}
    for i, a in enumerate(alerts):
        cust, dt = ix.cust_of(a["account_id"]), a["_dt"]
        if cust in last_c and dt - last_c[cust][1] <= timedelta(days=14):
            union(last_c[cust][0], i)
        last_c[cust] = (i, dt)
        if a["group_key"]:
            if a["group_key"] in first_g:
                union(first_g[a["group_key"]], i)
            else:
                first_g[a["group_key"]] = i
        if a["rule_id"] == "RULE-002" and a["trigger_txn_id"]:
            dev = ix.txn_by_id[a["trigger_txn_id"]]["device_id"]
            if dev in last_d and dt - last_d[dev][1] <= timedelta(days=30):
                union(last_d[dev][0], i)
            last_d[dev] = (i, dt)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    ordered = sorted(groups.values(), key=lambda g: min(g))

    last_txn = {}
    for t in txns:
        last_txn[t["account_id"]] = max(last_txn.get(t["account_id"], ""), t["txn_ts"])
    aev = account_events(world, txns)

    cases, actions, planned_freeze = [], [], []
    for ci, g in enumerate(ordered, 1):
        cid = case_id(ci)
        al = [alerts[i] for i in g]
        for a in al:
            a["case_id"] = cid
        top = max(al, key=lambda a: a["risk_score"])
        rules = {a["rule_id"] for a in al}
        score = round(min(100.0, top["risk_score"] + 5 * (len(rules) - 1)), 2)
        pb = band(score)
        opened = min(a["_dt"] for a in al) + timedelta(minutes=5)
        last_alert = max(a["_dt"] for a in al)
        disp, why, info = decide(al, score)
        ana, rev = ops[(ci - 1) % len(ops)], revs[(ci - 1) % len(revs)]
        d = tuple(x * (4 if opened >= BACKLOG_FROM and pb != 'P1' else 1) for x in DELAYS[pb])
        t = last_alert + timedelta(hours=rng.uniform(d[0], d[1]))
        acts = []

        def add(user, typ, frm, to, notes, dsp=None, acct=None):
            nonlocal t
            acts.append(dict(ts=t, case_ref=cid, user_id=user, action_type=typ, from_status=frm, to_status=to,
                             disposition=dsp, account_id=acct, notes=notes))
            t = t + timedelta(hours=rng.uniform(d[2], d[3]))

        ids = ", ".join(a["alert_id"] for a in al[:4]) + ("..." if len(al) > 4 else "")
        add(ana, "ASSIGN", "OPEN", "IN_REVIEW", f"Picked up case with {len(al)} alert(s) [{ids}]; priority {pb}, score {score:g}.")
        if info:
            add(ana, "REQUEST_INFO", "IN_REVIEW", "IN_REVIEW", "Requested customer explanation / supporting documents.")
        if disp == "CONFIRMED_SUSPICIOUS":
            add(ana, "ESCALATE", "IN_REVIEW", "ESCALATED", f"Escalated to risk/compliance: {why}.")
            if pb == "P1" or any(a["rule_id"] == "RULE-003" for a in al) or score >= 60:
                for acct in sorted({a["account_id"] for a in al})[:4]:
                    planned_freeze.append(dict(ts=t, case=cid, acct=acct, user=rev, notes=f"Account frozen pending investigation ({why}).",
                                               idx=len(acts), act=None))
                    acts.append(dict(ts=t, case_ref=cid, user_id=rev, action_type="FREEZE_ACCOUNT", from_status="ESCALATED",
                                     to_status="ESCALATED", disposition=None, account_id=acct,
                                     notes=f"Account frozen pending investigation ({why}).", _freeze=True))
                    planned_freeze[-1]["act"] = acts[-1]
                    t = t + timedelta(minutes=rng.randint(2, 20))
            add(rev, "CLOSE_CASE", "ESCALATED", "CLOSED", f"Confirmed suspicious: {why}.", dsp="CONFIRMED_SUSPICIOUS")
        else:
            add(ana, "CLOSE_CASE", "IN_REVIEW", "CLOSED", f"Closed as false positive: {why}.", dsp="FALSE_POSITIVE")
        cases.append(dict(case_id=cid, opened_at=opened, priority_band=pb, case_score=score, alert_count=len(al),
                          primary_account_id=top["account_id"], primary_customer_id=ix.cust_of(top["account_id"]),
                          assigned_user_id=ana, sla_due_at=opened + timedelta(hours=SLA_H[pb]),
                          _acts=acts, _alerts=al, _last_alert=last_alert))
    # freeze feasibility (global chronological order): needs no later txn, account not closed/frozen
    for f in sorted(planned_freeze, key=lambda f: (f["ts"], f["acct"])):
        A = world.A[f["acct"]]
        evl = sorted(aev[f["acct"]], key=lambda e: e["ts"])
        cur = [e for e in evl if e["ts"] <= f["ts"]]
        status = cur[-1]["to_status"] if cur else "ACTIVE"
        ok = (status in ("ACTIVE", "DORMANT") and A["close_date"] is None and f["ts"] <= AS_OF
              and last_txn.get(f["acct"], "") < ts(f["ts"]))
        if ok:
            aev[f["acct"]] = [e for e in evl if e["ts"] <= f["ts"]] + [
                dict(ts=f["ts"], account_id=f["acct"], from_status=status, to_status="FROZEN", reason_code="ANALYST_FREEZE",
                     source="ANALYST_ACTION", action_ref=f["act"])]
        else:
            f["act"]["_drop"] = True
    # truncate to as-of date; derive case state
    for c in cases:
        c["_acts"] = [a for a in c["_acts"] if not a.get("_drop") and a["ts"] <= AS_OF]

    # ---- labelled anomaly: case disposition (and its whole action chain) precedes the case's alerts
    cand = sorted([c for c in cases if c["_acts"] and c["_acts"][-1]["action_type"] == "CLOSE_CASE"
                   and not any(a["action_type"] == "FREEZE_ACCOUNT" for a in c["_acts"])], key=lambda c: c["case_id"])
    a5 = rng.sample(cand, min(cfg.n_edge, len(cand)))
    for c in a5:
        first_alert = min(a["_dt"] for a in c["_alerts"])
        target = first_alert - timedelta(hours=rng.randint(2, 48))
        delta = target - c["_acts"][-1]["ts"]
        for a in c["_acts"]:
            a["ts"] = a["ts"] + delta
        world.anoms.append(dict(issue_type="CASE_DISPOSITION_PRECEDES_ALERT", table="cases", entity_id=c["case_id"],
                                checks=["case_disposition_not_before_alerts", "case_actions_not_before_open",
                                        "case_closed_not_before_opened"]))

    # ---- action IDs (chronological), case rows derived from actions
    allacts = sorted((a for c in cases for a in c["_acts"]), key=lambda a: (a["ts"], a["case_ref"], a["action_type"]))
    for i, a in enumerate(allacts, 1):
        a["action_id"] = act_id(i)
    action_rows = [dict(action_id=a["action_id"], case_id=a["case_ref"], user_id=a["user_id"], action_ts=ts(a["ts"]),
                        action_type=a["action_type"], from_status=a["from_status"], to_status=a["to_status"],
                        disposition=a["disposition"], account_id=a["account_id"], notes=a["notes"]) for a in allacts]
    case_rows = []
    for c in cases:
        acts = sorted(c["_acts"], key=lambda a: (a["ts"], a["action_id"]))
        status = acts[-1]["to_status"] if acts else "OPEN"
        closed = next((a for a in acts if a["action_type"] == "CLOSE_CASE"), None)
        case_rows.append(dict(case_id=c["case_id"], opened_at=ts(c["opened_at"]), closed_at=ts(closed["ts"]) if closed else None,
                              status=status, priority_band=c["priority_band"], case_score=c["case_score"],
                              alert_count=c["alert_count"], primary_account_id=c["primary_account_id"],
                              primary_customer_id=c["primary_customer_id"],
                              assigned_user_id=c["assigned_user_id"] if acts else None,
                              sla_due_at=ts(c["sla_due_at"]), disposition=closed["disposition"] if closed else None))
    # ---- alerts + bridge
    alert_rows, bridge = [], []
    for a in alerts:
        alert_rows.append({k: a[k] for k in ["alert_id", "rule_version_id", "account_id", "trigger_txn_id", "alert_ts",
                                             "base_score", "adjustment_total", "risk_score", "priority_band", "group_key",
                                             "explanation", "evidence", "score_components", "case_id"]})
        seen = set()
        for tid, role in a["_txns"]:
            if tid not in seen:
                seen.add(tid)
                bridge.append(dict(alert_id=a["alert_id"], transaction_id=tid, evidence_role=role))
    # ---- account status events (append-only), accounts + customers derived from them
    flat = [e for a in sorted(aev) for e in aev[a]]
    flat.sort(key=lambda e: (e["ts"], e["account_id"]))
    ev_rows = []
    for i, e in enumerate(flat, 1):
        ev_rows.append(dict(event_id=ase_id(i), account_id=e["account_id"], event_ts=ts(e["ts"]), from_status=e["from_status"],
                            to_status=e["to_status"], reason_code=e["reason_code"], source=e["source"],
                            action_id=e["action_ref"]["action_id"] if e["action_ref"] else None))
    last_state = {}
    for e in ev_rows:
        last_state[e["account_id"]] = e["to_status"]
    last_ts = {}
    for t in txns:
        last_ts[t["account_id"]] = t["txn_ts"]
    acc_rows = []
    for aid in world.order:
        A = world.A[aid]
        acc_rows.append(dict(account_id=aid, primary_customer_id=A["cust"], account_type=A["type"], currency="INR",
                             open_date=A["open_date"].isoformat(), close_date=A["close_date"].isoformat() if A["close_date"] else None,
                             status=last_state[aid], opening_balance=A["opening"], overdraft_limit=A["ovd"],
                             current_balance=world.final_balance[aid], last_activity_at=last_ts.get(aid)))
    owned = defaultdict(list)
    for a in acc_rows:
        owned[a["primary_customer_id"]].append(a["status"])
    for c in world.customers:
        c["status"] = "CLOSED" if owned[c["customer_id"]] and all(s == "CLOSED" for s in owned[c["customer_id"]]) else "ACTIVE"
    dev_first = {}
    for (d, a), lk in world.links.items():
        dev_first[d] = min(dev_first.get(d, lk["first_seen_at"]), lk["first_seen_at"])
    dev_rows = [dict(d, first_seen_at=ts(dev_first[d["device_id"]])) for d in world.devices if d["device_id"] in dev_first]
    link_rows = [dict(device_id=lk["device_id"], account_id=lk["account_id"], first_seen_at=ts(lk["first_seen_at"]),
                      last_seen_at=lk["last_seen_at"]) for _, lk in sorted(world.links.items())]
    return dict(accounts=acc_rows, account_status_events=ev_rows, devices=dev_rows, device_account_links=link_rows,
                alerts=alert_rows, alert_transactions=bridge, cases=case_rows, analyst_actions=action_rows)
