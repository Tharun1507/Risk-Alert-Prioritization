"""Transparent, versioned risk rules + explainable scoring.

The engine consumes ONLY the input tables (customers, kyc_profiles, accounts, merchants, device_account_links,
transactions, transfer_links, risk_rules). It never sees ground truth. Every alert carries:
  - the triggering transaction and supporting evidence transactions,
  - the exact thresholds/facts that fired (evidence JSON),
  - a score = base weight + signed, named adjustments (score_components) -> priority band.
"""
import bisect
import json
from collections import defaultdict
from datetime import timedelta

from .common import pts, ts, pds, AS_OF, alt_id

MIT = "MITIGATION_ESTABLISHED_VERIFIED_CONSISTENT_HISTORY"

RULE_DEFS = [
    dict(rule_id="RULE-001", name="VELOCITY_SPIKE", base=40.0,
         desc="Burst of debit transactions on one account inside a short window.",
         versions=[dict(v=1, frm="2026-01-01T00:00:00", to=None,
                        params=dict(window_minutes=60, min_debit_count=6, cooldown_hours=24))]),
    dict(rule_id="RULE-002", name="NEW_DEVICE_HIGH_VALUE", base=35.0,
         desc="High-value debit made from a device first linked to the account <= 24h earlier. "
              "v2 (2026-07-01) lowers the amount threshold after an admin review.",
         versions=[dict(v=1, frm="2026-01-01T00:00:00", to="2026-07-01T00:00:00",
                        params=dict(window_hours=24, min_amount=25000.0, cooldown_hours=24)),
                   dict(v=2, frm="2026-07-01T00:00:00", to=None,
                        params=dict(window_hours=24, min_amount=20000.0, cooldown_hours=24))]),
    dict(rule_id="RULE-003", name="CIRCULAR_TRANSFER", base=0.0,
         desc="Funds return to the originating account through a chain of 2-4 transfers within 72h "
              "(each leg >= min amount and within tolerance of the first leg). Base score depends on cycle length.",
         versions=[dict(v=1, frm="2026-01-01T00:00:00", to=None,
                        params=dict(window_hours=72, min_leg_amount=20000.0, amount_tolerance=0.15, min_len=2, max_len=4,
                                    base_by_len={"2": 35.0, "3": 55.0, "4": 65.0}))]),
    dict(rule_id="RULE-004", name="DORMANT_REACTIVATION", base=45.0,
         desc="First transaction after >= 180 days of inactivity is a high-value debit.",
         versions=[dict(v=1, frm="2026-01-01T00:00:00", to=None,
                        params=dict(dormancy_days=180, min_amount=15000.0))]),
]
LATENCY = {"RULE-001": 2, "RULE-002": 1, "RULE-003": 5, "RULE-004": 1}   # minutes from trigger to alert


def rule_rows(admin_id):
    rows = []
    for r in RULE_DEFS:
        for v in r["versions"]:
            rows.append(dict(rule_version_id=f"{r['rule_id']}-v{v['v']}", rule_id=r["rule_id"], rule_name=r["name"],
                             version=v["v"], description=r["desc"], base_weight=r["base"],
                             params=json.dumps(v["params"], sort_keys=True), effective_from=v["frm"],
                             effective_to=v["to"], created_by=admin_id, status="ACTIVE" if v["to"] is None else "RETIRED"))
    return rows


def band(score):
    return "P1" if score >= 70 else ("P2" if score >= 40 else "P3")


class Index:
    def __init__(self, t):
        self.t = t
        self.cust = {c["customer_id"]: c for c in t["customers"]}
        self.kyc = {k["customer_id"]: k for k in t["kyc_profiles"]}
        self.acct = {a["account_id"]: a for a in t["accounts"]}
        self.merch = {m["merchant_id"]: m for m in t["merchants"]}
        self.links = {(l["device_id"], l["account_id"]): pts(l["first_seen_at"]) for l in t["device_account_links"]}
        self.dev_accts = defaultdict(set)
        for (d, a) in self.links:
            self.dev_accts[d].add(a)
        # copies: the engine must not mutate the input tables
        self.txns = sorted((dict(r, _dt=pts(r["txn_ts"])) for r in t["transactions"]),
                           key=lambda r: (r["txn_ts"], r["transaction_id"]))
        self.by_acct = defaultdict(list)
        for r in self.txns:
            self.by_acct[r["account_id"]].append(r)
        self.txn_by_id = {r["transaction_id"]: r for r in self.txns}
        self.versions = defaultdict(list)
        for r in t["risk_rules"]:
            self.versions[r["rule_id"]].append(dict(r, _from=pts(r["effective_from"]),
                                                    _to=pts(r["effective_to"]) if r["effective_to"] else None,
                                                    _p=json.loads(r["params"])))

    def rule_at(self, rule_id, dt):
        for v in self.versions[rule_id]:
            if v["_from"] <= dt and (v["_to"] is None or dt < v["_to"]):
                return v
        return None

    def cust_of(self, acct):
        return self.acct[acct]["primary_customer_id"]

    def kyc_components(self, acct, at):
        c = self.cust_of(acct)
        k, cu = self.kyc[c], self.cust[c]
        comps = []
        if k["risk_tier"] == "HIGH":
            comps.append(("KYC_TIER_HIGH", 10.0))
        elif k["risk_tier"] == "MEDIUM":
            comps.append(("KYC_TIER_MEDIUM", 5.0))
        if k["kyc_status"] != "VERIFIED":
            comps.append(("KYC_NOT_VERIFIED", 10.0))
        if (at.date() - pds(cu["customer_since"])).days < 180:
            comps.append(("NEW_CUSTOMER_LT_180D", 5.0))
        return comps

    def established_verified(self, acct, at):
        c = self.cust_of(acct)
        return (self.kyc[c]["kyc_status"] == "VERIFIED"
                and (at.date() - pds(self.cust[c]["customer_since"])).days >= 730)


def amount_comps(x):
    if x >= 100000:
        return [("AMOUNT_GE_100K", 10.0)]
    if x >= 50000:
        return [("AMOUNT_GE_50K", 5.0)]
    return []


def merchant_comp(ix, mid):
    if not mid:
        return []
    r = ix.merch[mid]["risk_level"]
    return [("HIGH_RISK_MERCHANT", 10.0)] if r == "HIGH" else ([("MEDIUM_RISK_MERCHANT", 5.0)] if r == "MEDIUM" else [])


def make(ix, ver, acct, trig, trig_dt, base, comps, evidence, expl, txn_ids, group_key=None, mitigated=False):
    comps = list(comps)
    if mitigated:
        comps.append((MIT, -20.0))
    adj = sum(p for _, p in comps)
    score = round(min(100.0, max(0.0, base + adj)), 2)
    from .common import ts as _ts
    adt = trig_dt + timedelta(minutes=LATENCY[ver["rule_id"]])
    return dict(rule_version_id=ver["rule_version_id"], rule_id=ver["rule_id"], account_id=acct, trigger_txn_id=trig,
                alert_ts=_ts(adt), _dt=adt, base_score=float(base), adjustment_total=float(adj), risk_score=score,
                priority_band=band(score), group_key=group_key, explanation=expl,
                evidence=json.dumps(evidence, sort_keys=True),
                score_components=json.dumps([dict(code=c, points=p) for c, p in comps]), _txns=txn_ids)


# ------------------------------------------------------------------ RULE-001
def r_velocity(ix):
    out = []
    for acct in sorted(ix.by_acct):
        deb = [t for t in ix.by_acct[acct] if t["direction"] == "DEBIT"]
        if len(deb) < 5:
            continue
        j, last = 0, None
        for i, t in enumerate(deb):
            v = ix.rule_at("RULE-001", t["_dt"]); p = v["_p"]
            win = timedelta(minutes=p["window_minutes"])
            while t["_dt"] - deb[j]["_dt"] > win:
                j += 1
            n = i - j + 1
            if n >= p["min_debit_count"] and (last is None or t["_dt"] - last >= timedelta(hours=p["cooldown_hours"])):
                w = deb[j:i + 1]
                total = round(sum(x["amount"] for x in w), 2)
                high = sum(1 for x in w if x["merchant_id"] and ix.merch[x["merchant_id"]]["risk_level"] == "HIGH")
                comps = []
                if n >= 8:
                    comps.append(("BURST_GE_8_TXNS", 10.0))
                if high:
                    comps.append(("HIGH_RISK_MERCHANT", 10.0))
                if total >= 30000:
                    comps.append(("BURST_TOTAL_GE_30K", 5.0))
                comps += ix.kyc_components(acct, t["_dt"])
                mids = {x["merchant_id"] for x in w}
                mit = False
                if len(mids) == 1 and None not in mids and ix.established_verified(acct, t["_dt"]):
                    m0 = next(iter(mids))
                    prior = sum(1 for x in ix.by_acct[acct] if x["merchant_id"] == m0 and x["_dt"] < w[0]["_dt"])
                    mit = prior >= 2
                span = round((w[-1]["_dt"] - w[0]["_dt"]).total_seconds() / 60, 1)
                ev = dict(txn_count=n, span_minutes=span, total_amount=total, high_risk_merchant_txns=high,
                          single_merchant=len(mids) == 1, threshold=f">={p['min_debit_count']} debits in {p['window_minutes']} min")
                expl = (f"{v['rule_id']} {v['rule_name']}: {n} debits in {span} min (threshold >={p['min_debit_count']} in "
                        f"{p['window_minutes']} min), total INR {total:,.2f}, {high} at HIGH-risk merchants.")
                ids = [(x["transaction_id"], "TRIGGER" if x is t else "EVIDENCE") for x in w]
                out.append(make(ix, v, acct, t["transaction_id"], t["_dt"], v["base_weight"], comps, ev, expl, ids,
                                mitigated=mit))
                last = t["_dt"]
    return out


# ------------------------------------------------------------------ RULE-002
def r_new_device(ix):
    out, last = [], {}
    for t in ix.txns:
        if t["direction"] != "DEBIT" or not t["device_id"]:
            continue
        v = ix.rule_at("RULE-002", t["_dt"]); p = v["_p"]
        lk = ix.links.get((t["device_id"], t["account_id"]))
        if lk is None:
            continue
        age_h = (t["_dt"] - lk).total_seconds() / 3600
        if 0 <= age_h <= p["window_hours"] and t["amount"] >= p["min_amount"]:
            key = (t["account_id"], t["device_id"])
            if key in last and t["_dt"] - last[key] < timedelta(hours=p["cooldown_hours"]):
                continue
            last[key] = t["_dt"]
            comps = amount_comps(t["amount"]) + merchant_comp(ix, t["merchant_id"])
            shared = len(ix.dev_accts[t["device_id"]])
            if shared >= 2:
                comps.append(("DEVICE_SHARED_ACROSS_ACCOUNTS", 10.0))
            comps += ix.kyc_components(t["account_id"], t["_dt"])
            prior_max = max([x["amount"] for x in ix.by_acct[t["account_id"]]
                             if x["direction"] == "DEBIT" and x["_dt"] < t["_dt"]] or [0.0])
            mit = ix.established_verified(t["account_id"], t["_dt"]) and prior_max >= 0.8 * t["amount"]
            ev = dict(amount=t["amount"], device_age_hours=round(age_h, 2), device_id=t["device_id"],
                      accounts_sharing_device=shared, prior_max_debit=prior_max, rule_version=v["version"],
                      threshold=f"amount>={p['min_amount']:.0f} within {p['window_hours']}h of device link")
            expl = (f"{v['rule_id']} {v['rule_name']} (v{v['version']}): INR {t['amount']:,.2f} debit {age_h:.1f}h after device "
                    f"{t['device_id']} was linked (threshold >= {p['min_amount']:,.0f} within {p['window_hours']}h); "
                    f"device used on {shared} account(s).")
            out.append(make(ix, v, t["account_id"], t["transaction_id"], t["_dt"], v["base_weight"], comps, ev, expl,
                            [(t["transaction_id"], "TRIGGER")], mitigated=mit))
    return out


# ------------------------------------------------------------------ RULE-003
def r_cycles(ix):
    out = []
    v0 = ix.versions["RULE-003"][0]; p = v0["_p"]
    tl = ix.t["transfer_links"]
    edges = []
    for l in tl:
        if l["amount"] >= p["min_leg_amount"]:
            edges.append(dict(t=pts(l["initiated_at"]), done=pts(l["completed_at"]), src=l["source_account_id"],
                              dst=l["dest_account_id"], amt=l["amount"], id=l["transfer_id"], out=l["out_txn_id"],
                              inn=l["in_txn_id"]))
    edges.sort(key=lambda e: (e["t"], e["id"]))
    by_src = defaultdict(list)
    for e in edges:
        by_src[e["src"]].append(e)
    times = {k: [e["t"] for e in v] for k, v in by_src.items()}
    win = timedelta(hours=p["window_hours"])
    cycles, seen = [], set()

    def dfs(path, first):
        last = path[-1]
        if len(path) >= p["min_len"] and last["dst"] == first["src"]:
            key = tuple(sorted(e["id"] for e in path))
            if key not in seen:
                seen.add(key); cycles.append(list(path))
            return
        if len(path) >= p["max_len"]:
            return
        lst = by_src.get(last["dst"], [])
        i = bisect.bisect_right(times.get(last["dst"], []), last["t"])
        while i < len(lst) and lst[i]["t"] - first["t"] <= win:
            e = lst[i]
            srcs = {x["src"] for x in path}
            if abs(e["amt"] - first["amt"]) <= p["amount_tolerance"] * first["amt"] and (e["dst"] == first["src"] or e["dst"] not in srcs):
                path.append(e); dfs(path, first); path.pop()
            i += 1
    for e1 in edges:
        dfs([e1], e1)
    for gi, cyc in enumerate(cycles, 1):
        n = len(cyc)
        close_dt = max(e["done"] for e in cyc)
        v = ix.rule_at("RULE-003", close_dt)
        base = p["base_by_len"][str(n)]
        gk = f"CYC-{cyc[0]['id']}"
        all_txn = [x for e in cyc for x in (e["out"], e["inn"])]
        for e in cyc:
            acct = e["src"]
            comps = amount_comps(min(x["amt"] for x in cyc)) + ix.kyc_components(acct, close_dt)
            mit = False
            if n == 2 and ix.established_verified(acct, close_dt):
                pair = {cyc[0]["src"], cyc[0]["dst"]}
                first_t = min(x["t"] for x in cyc)
                mit = any({l["source_account_id"], l["dest_account_id"]} == pair and pts(l["initiated_at"]) <= first_t - timedelta(days=20)
                          for l in tl)
            ev = dict(cycle_len=n, transfer_ids=[x["id"] for x in cyc], accounts=[x["src"] for x in cyc],
                      min_leg_amount=min(x["amt"] for x in cyc), hours_first_to_close=round((close_dt - cyc[0]["t"]).total_seconds() / 3600, 2),
                      threshold=f"{p['min_len']}-{p['max_len']} legs >= {p['min_leg_amount']:.0f} within {p['window_hours']}h")
            expl = (f"{v['rule_id']} {v['rule_name']}: {n}-account loop {' -> '.join(x['src'] for x in cyc)} -> {cyc[0]['src']} "
                    f"completed in {ev['hours_first_to_close']}h; smallest leg INR {ev['min_leg_amount']:,.2f}.")
            ids = [(x, "EVIDENCE") for x in all_txn if x != e["out"]] + [(e["out"], "TRIGGER")]
            out.append(make(ix, v, acct, e["out"], close_dt, base, comps, ev, expl, ids,
                            group_key=gk, mitigated=mit))
    return out


# ------------------------------------------------------------------ RULE-004
def r_dormant(ix):
    out = []
    for acct in sorted(ix.by_acct):
        lst = ix.by_acct[acct]
        for i in range(1, len(lst)):
            t, prev = lst[i], lst[i - 1]
            v = ix.rule_at("RULE-004", t["_dt"]); p = v["_p"]
            gap = (t["_dt"] - prev["_dt"]).total_seconds() / 86400
            if gap >= p["dormancy_days"] and t["direction"] == "DEBIT" and t["amount"] >= p["min_amount"]:
                comps = amount_comps(t["amount"]) + merchant_comp(ix, t["merchant_id"]) + ix.kyc_components(acct, t["_dt"])
                prior_max = max(x["amount"] for x in lst[:i] if x["direction"] == "DEBIT") if any(
                    x["direction"] == "DEBIT" for x in lst[:i]) else 0.0
                mit = ix.established_verified(acct, t["_dt"]) and prior_max >= 0.8 * t["amount"]
                ev = dict(gap_days=round(gap, 1), amount=t["amount"], prior_max_debit=prior_max,
                          threshold=f"gap>={p['dormancy_days']}d and debit>={p['min_amount']:.0f}")
                expl = (f"{v['rule_id']} {v['rule_name']}: first activity after {gap:.0f} days of inactivity is a "
                        f"INR {t['amount']:,.2f} debit (threshold gap >= {p['dormancy_days']}d, amount >= {p['min_amount']:,.0f}).")
                out.append(make(ix, v, acct, t["transaction_id"], t["_dt"], v["base_weight"], comps, ev, expl,
                                [(t["transaction_id"], "TRIGGER"), (prev["transaction_id"], "EVIDENCE")], mitigated=mit))
    return out


def evaluate(tables):
    """Run all rules; returns alert dicts (without IDs / case)."""
    ix = Index(tables)
    alerts = r_velocity(ix) + r_new_device(ix) + r_cycles(ix) + r_dormant(ix)
    alerts.sort(key=lambda a: (a["_dt"], a["rule_id"], a["account_id"], a["trigger_txn_id"]))
    return alerts, ix
