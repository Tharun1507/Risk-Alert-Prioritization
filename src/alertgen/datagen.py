"""Master data, background behaviour, labelled scenarios and the ledger simulation.

Design notes
- Every stage uses its own seeded RNG stream, so changing the number of one scenario never perturbs others.
- Background behaviour is deliberately kept BELOW every rule threshold; alerts therefore come from planted,
  labelled scenarios (suspicious, benign false-positive, boundary no-alert) and are produced by the independent
  rule engine in rules.py, not assigned.
- Transactions are first planned as events, simulated chronologically (infeasible debits are dropped, transfer
  pairs atomically), trimmed to the exact target volume and only then given IDs and running balances.
"""
import bisect
import hashlib
import itertools
import math
from datetime import datetime, timedelta, date
from itertools import accumulate

from . import schema as S
from .common import (WS, WE, AS_OF, ts, pds, sub_rng, cus_id, kyc_id, acc_id, mrc_id, dev_id, txn_id, trf_id, usr_id)

FIRST = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Rohan", "Karan", "Arjun", "Rahul", "Neha", "Priya", "Ananya", "Diya",
         "Meera", "Kavya", "Sneha", "Pooja", "Riya", "Sanjay", "Vikram", "Manoj", "Deepa", "Lakshmi", "Suresh", "Anil",
         "Farhan", "Zoya", "Imran", "Sunita", "Harish", "Nikhil"]
LAST = ["Sharma", "Reddy", "Patel", "Iyer", "Nair", "Gupta", "Singh", "Khan", "Das", "Mehta", "Rao", "Joshi", "Verma",
        "Kulkarni", "Chopra", "Menon", "Bose", "Pillai", "Naidu", "Shetty", "Yadav", "Malhotra", "Banerjee", "Ghosh"]
CITIES = ["Hyderabad", "Mumbai", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Lucknow",
          "Kochi", "Indore"]
MCATS = [("GROCERY", 5411, "LOW", 18), ("RESTAURANT", 5812, "LOW", 14), ("FUEL", 5541, "LOW", 8),
         ("ECOMMERCE", 5311, "LOW", 16), ("PHARMACY", 5912, "LOW", 6), ("UTILITIES", 4900, "LOW", 8),
         ("CLOTHING", 5651, "LOW", 8), ("ELECTRONICS", 5732, "MEDIUM", 5), ("TRAVEL", 4511, "MEDIUM", 5),
         ("JEWELLERY", 5944, "MEDIUM", 2), ("GAMBLING", 7995, "HIGH", 1), ("CRYPTO_EXCHANGE", 6051, "HIGH", 1),
         ("MONEY_TRANSFER", 4829, "HIGH", 1)]
WORDS = ["Prime", "Royal", "Metro", "Urban", "Sunrise", "Elite", "Daily", "Swift", "Green", "Blue", "Grand", "Smart"]
OCC_W = [58, 14, 6, 10, 12]
OCC_INCOME_MULT = {"SALARIED": 1.0, "SELF_EMPLOYED": 1.2, "STUDENT": 0.35, "RETIRED": 0.6, "BUSINESS_OWNER": 1.8}
HOUR_W = [1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 8, 6, 4, 2, 1]
INT_RATE = 0.0125            # quarterly interest on savings, applied to the running balance
BG_SPEND_CAP = 19500.0       # background spends stay below every amount threshold (>= 20,000)
BG_TRANSFER_CAP = 15000.0
DEFAULT_CH = {"SALARY_CREDIT": "SYSTEM", "INTEREST_CREDIT": "SYSTEM", "CASH_DEPOSIT": "BRANCH",
              "TRANSFER_IN": "SYSTEM", "TRANSFER_OUT": "MOBILE_APP", "POS_PURCHASE": "POS",
              "ONLINE_PURCHASE": "WEB", "ATM_WITHDRAWAL": "ATM"}


def rdt(rng, lo, hi, h0=0, h1=23):
    d = lo + timedelta(days=rng.randint(0, (hi - lo).days))
    return datetime(d.year, d.month, d.day, rng.randint(h0, h1), rng.randint(0, 59), rng.randint(0, 59))


class Shortage(Exception):
    pass


class World:
    def __init__(self, cfg, fp_extra=0):
        self.cfg, self.fp_extra = cfg, fp_extra
        self.seq = itertools.count()
        self.tcount = itertools.count(1)
        self.events, self.truth, self.anoms = [], [], []
        self.used = set()
        self.scn_n = {}
        self.A = {}               # internal per-account attributes
        self.order = []           # deterministic account order
        self.tpurpose = {}

    # ---------------------------------------------------------------- helpers
    def new_scn(self, code):
        self.scn_n[code] = self.scn_n.get(code, 0) + 1
        return f"SCN-{code}-{self.scn_n[code]:04d}"

    def ev(self, when, acct, ttype, amount, kind, **kw):
        e = dict(ts=when, seq=next(self.seq), acct=acct, txn_type=ttype, dir=S.DIRS[ttype], amount=amount, kind=kind,
                 merchant=kw.get("merchant"), device=kw.get("device"), channel=kw.get("channel", DEFAULT_CH[ttype]),
                 tkey=kw.get("tkey"), scn=kw.get("scn"), must=kw.get("must", False), force=kw.get("force", False),
                 dyn=kw.get("dyn"), extra=kw.get("extra", 0.0), desc=kw.get("desc", ""))
        self.events.append(e)
        return e

    def transfer(self, when, src, dst, amount, kind, lag, scn=None, must=False, device=None, purpose="OTHER"):
        key = f"T{next(self.tcount)}"
        self.tpurpose[key] = purpose
        self.ev(when, src, "TRANSFER_OUT", amount, kind, tkey=key, scn=scn, must=must, device=device,
                desc=f"Transfer to {dst} ({purpose})")
        self.ev(when + timedelta(seconds=lag), dst, "TRANSFER_IN", amount, kind, tkey=key, scn=scn,
                desc=f"Transfer from {src} ({purpose})")
        return key

    def need(self, a, amount):
        self.A[a]["opening"] = max(self.A[a]["opening"], round(amount * 1.3 + 150000, 2))

    def link(self, dev, acct, when):
        k = (dev, acct)
        if k not in self.links or self.links[k]["first_seen_at"] > when:
            self.links[k] = dict(device_id=dev, account_id=acct, first_seen_at=when, last_seen_at=None)

    def new_device(self, rng, when, status="ACTIVE"):
        self.dev_n += 1
        did = dev_id(self.dev_n)
        typ = rng.choices(S.DEV_TYPE, [70, 10, 20])[0]
        os_ = rng.choice(["ANDROID", "IOS"]) if typ != "DESKTOP" else rng.choice(["WINDOWS", "MACOS"])
        self.devices.append(dict(device_id=did, device_type=typ, os_family=os_,
                                 fingerprint_hash=hashlib.sha1(f"{self.cfg.seed}-{did}".encode()).hexdigest()[:16],
                                 first_seen_at=None, status=status))
        return did

    def picker(self, name, cond):
        rng = sub_rng(self.cfg.seed, "pick:" + name)
        ids = [a for a in self.order if cond(self.A[a])]
        rng.shuffle(ids)
        it = iter(ids)

        def nxt():
            for a in it:
                if a not in self.used:
                    self.used.add(a)
                    return a
            raise RuntimeError(f"account pool exhausted for {name}")
        return nxt

    def bounds(self, A):
        start = max(WS, A["open_dt"] + timedelta(days=3))
        end = WE
        if A["close_date"]:
            end = min(end, datetime(A["close_date"].year, A["close_date"].month, A["close_date"].day) - timedelta(hours=1))
        if A["dormant_end"]:
            end = min(end, A["dormant_end"] - timedelta(hours=2))
        return start, end

    # conditions
    c_active = staticmethod(lambda a: a["primary"] and a["close_date"] is None and a["dormant_end"] is None
                            and a["open_date"] <= date(2026, 1, 5))
    c_benign = staticmethod(lambda a: a["primary"] and a["close_date"] is None and a["dormant_end"] is None
                            and a["open_date"] <= date(2026, 1, 5) and a["tenure"] >= 900
                            and a["kyc_status"] == "VERIFIED" and a["tier"] != "HIGH")
    c_dorm = staticmethod(lambda a: a["primary"] and a["close_date"] is None and a["dormant_end"] is None
                          and a["open_date"] <= date(2026, 1, 5))
    c_dorm_benign = staticmethod(lambda a: a["primary"] and a["close_date"] is None and a["dormant_end"] is None
                                 and a["open_date"] <= date(2026, 1, 5) and a["tenure"] >= 900
                                 and a["kyc_status"] == "VERIFIED" and a["tier"] != "HIGH")

    # ---------------------------------------------------------------- master data
    def gen_master(self):
        cfg, rng = self.cfg, sub_rng(self.cfg.seed, "master")
        as_of_d = AS_OF.date()
        self.customers, self.kyc, self.accounts, self.parties = [], [], [], []
        self.devices, self.links, self.dev_n = [], {}, 0
        self.cust_devices = {}
        n = cfg.n_customers
        extra = set(rng.sample(range(1, n + 1), cfg.n_accounts - n))
        for i in range(1, n + 1):
            cid = cus_id(i)
            tenure = min(4200, int(rng.expovariate(1 / 1100)) + 40)
            since = as_of_d - timedelta(days=tenure)
            dob = since - timedelta(days=int(365.25 * rng.randint(21, 80)) + rng.randint(0, 364))
            occ = rng.choices(S.OCC, OCC_W)[0]
            income = round(min(400000, max(12000, rng.lognormvariate(math.log(42000 * OCC_INCOME_MULT[occ]), 0.5))), -2)
            self.customers.append(dict(customer_id=cid, full_name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                                       date_of_birth=dob.isoformat(), city=rng.choice(CITIES), country_code="IN",
                                       occupation_band=occ, monthly_income=float(income),
                                       customer_since=since.isoformat(), status="ACTIVE"))
            # KYC profile
            r = rng.random()
            st = "VERIFIED" if r < 0.89 else ("PENDING" if r < 0.94 else "EXPIRED")
            if st == "EXPIRED" and tenure < 1100:
                st = "VERIFIED"
            tier = rng.choices(S.TIER, [70, 22, 8])[0]
            pep = 1 if rng.random() < 0.015 else 0
            if pep:
                tier = "HIGH"
            if st == "VERIFIED":
                ver = as_of_d - timedelta(days=rng.randint(0, min(tenure, 1500)))
                exp = ver + timedelta(days=1825)
            elif st == "EXPIRED":
                exp = as_of_d - timedelta(days=rng.randint(1, 300))
                ver = exp - timedelta(days=730)
            else:
                ver = exp = None
            self.kyc.append(dict(kyc_id=kyc_id(i), customer_id=cid, kyc_status=st, risk_tier=tier,
                                 verified_date=ver.isoformat() if ver else None,
                                 expiry_date=exp.isoformat() if exp else None,
                                 id_document_type=rng.choice(["PASSPORT", "NATIONAL_ID", "DRIVING_LICENCE"]),
                                 pep_flag=pep,
                                 source_of_funds=rng.choice(["SALARY", "BUSINESS_INCOME", "PENSION", "SAVINGS",
                                                             "FAMILY_SUPPORT"]),
                                 effective_from=(ver or since).isoformat()))
            # devices (1-2 per customer)
            devs = [self.new_device(rng, None) for _ in range(1 + (rng.random() < 0.3))]
            self.cust_devices[cid] = devs
            # accounts
            for seq, typ in [(1, "CHECKING")] + ([(2, "SAVINGS")] if i in extra else []):
                lo = 0
                hi = min(30, tenure - 35) if seq == 1 else tenure - 35
                od = since + timedelta(days=rng.randint(lo, hi))
                close = None
                if rng.random() < 0.04 and od <= date(2026, 6, 30):
                    cl_lo = max(od + timedelta(days=60), date(2026, 3, 1))
                    cl_hi = date(2026, 9, 20)
                    if cl_lo < cl_hi:
                        close = cl_lo + timedelta(days=rng.randint(0, (cl_hi - cl_lo).days))
                opening = round(min(2e6, rng.lognormvariate(math.log(15000 if typ == "CHECKING" else 40000), 1.0)), 2)
                aid = acc_id(cid, seq)
                self.A[aid] = dict(acct=aid, cust=cid, type=typ, primary=(typ == "CHECKING"), open_date=od,
                                   open_dt=datetime(od.year, od.month, od.day, 10, 0, 0), close_date=close,
                                   dormant_end=None, opening=opening,
                                   ovd=10000.0 if typ == "CHECKING" and rng.random() < 0.06 else 0.0,
                                   income=float(income), occ=occ, tenure=tenure, kyc_status=st, tier=tier,
                                   activity=rng.lognormvariate(0, 0.6), device=devs[0],
                                   payday=rng.choice([1, 2, 3, 25, 26, 27, 28, 28]))
                self.order.append(aid)
                for d in devs:
                    self.link(d, aid, self.A[aid]["open_dt"])
        # joint owners (8%)
        for aid in self.order:
            A = self.A[aid]
            self.parties.append(dict(account_id=aid, customer_id=A["cust"], party_role="OWNER",
                                     effective_from=A["open_date"].isoformat(), effective_to=None))
            if rng.random() < 0.08:
                other = cus_id(rng.randint(1, n))
                if other != A["cust"]:
                    self.parties.append(dict(account_id=aid, customer_id=other, party_role="JOINT_OWNER",
                                             effective_from=A["open_date"].isoformat(), effective_to=None))
        # merchants
        self.merchants = []
        mr = sub_rng(cfg.seed, "merchants")
        wts = [m[3] for m in MCATS]
        for i in range(1, cfg.n_merchants + 1):
            cat = MCATS[i - 1] if i <= len(MCATS) else mr.choices(MCATS, wts)[0]
            self.merchants.append(dict(merchant_id=mrc_id(i), merchant_name=f"{mr.choice(WORDS)} {cat[0].title().replace('_', ' ')} {i:04d}",
                                       mcc=cat[1], category=cat[0], risk_level=cat[2], country_code="IN", status="ACTIVE",
                                       onboarded_date=(date(2024, 1, 1) + timedelta(days=mr.randint(0, 900))).isoformat()))
        self.m_by_risk = {r_: [m["merchant_id"] for m in self.merchants if m["risk_level"] == r_] for r_ in S.RISK}
        self.m_by_cat = {}
        for m in self.merchants:
            self.m_by_cat.setdefault(m["category"], []).append(m["merchant_id"])
        self.m_w = list(accumulate(wts_ for wts_ in
                                   [next(c[3] for c in MCATS if c[0] == m["category"]) for m in self.merchants]))
        # users
        self.users = []
        un = 0
        ur = sub_rng(cfg.seed, "users")
        for role, cnt, team in [("OPS_ANALYST", cfg.n_ops, "Transaction Monitoring"),
                                ("RISK_REVIEWER", cfg.n_reviewers, "Risk & Compliance"),
                                ("SYS_ADMIN", cfg.n_admins, "Platform Administration")]:
            for _ in range(cnt):
                un += 1
                self.users.append(dict(user_id=usr_id(un), full_name=f"{ur.choice(FIRST)} {ur.choice(LAST)}", role=role,
                                       team=team, active_from=date(2025, 1, 1).isoformat(), status="ACTIVE"))

    # ---------------------------------------------------------------- planning
    def counts(self):
        R = self.cfg.n_alerts - self.cfg.n_edge
        c = dict(s1=max(1, round(.19 * R)), s2=max(1, round(.06 * R)), s3=max(1, round(.20 * R)),
                 ring=max(1, round(.14 * R / 3.4)), recip=max(1, round(.05 * R / 2)),
                 s7=max(1, round(.09 * R)), s8=max(1, round(.05 * R)),
                 b1=max(1, round(.03 * R)), b2=max(1, round(.03 * R)), b3=max(1, round(.03 * R)))
        c["s4"] = max(0, round(.15 * R) + self.fp_extra)
        return c

    def truth_add(self, scn, typ, label, rule, exp_alert, n_alerts, decision, accts, note):
        self.truth.append(dict(scenario_id=scn, scenario_type=typ, label=label, rule_id=rule, expected_alert=exp_alert,
                               expected_alert_count=n_alerts, expected_decision=decision, accounts=accts, note=note))

    def plan_dormant(self, c):
        rng = sub_rng(self.cfg.seed, "dormant")
        mer = self.m_by_risk

        def schedule(gap_lo, gap_hi, react_lo):
            react = datetime(2026, 8, 10) + timedelta(days=rng.randint(react_lo, 62), hours=rng.randint(9, 20),
                                                      minutes=rng.randint(0, 59))
            anchor = (react - timedelta(days=rng.randint(gap_lo, gap_hi))).replace(hour=11, minute=rng.randint(0, 59),
                                                                                  second=0)
            return react, anchor

        def common(a, anchor, scn):
            self.A[a]["dormant_end"] = anchor
            self.ev(anchor, a, "CASH_DEPOSIT", round(rng.uniform(2000, 10000), 2), "PLANT", scn=scn,
                    desc="Branch cash deposit (last activity before dormancy)", channel="BRANCH", must=True)

        pk = self.picker("s7", self.c_dorm)
        for _ in range(c["s7"]):
            a = pk(); scn = self.new_scn("S7")
            react, anchor = schedule(185, 205, 0)
            common(a, anchor, scn)
            if rng.random() < 0.5:
                X = round(rng.uniform(50000, 200000), 2)
                self.ev(react, a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True, merchant=rng.choice(mer["HIGH"]),
                        device=self.A[a]["device"], channel="WEB", desc="Reactivation purchase")
            else:
                X = float(rng.choice([50000, 75000, 100000]))
                self.ev(react, a, "ATM_WITHDRAWAL", X, "PLANT", scn=scn, must=True, desc="Reactivation cash withdrawal")
            self.need(a, X)
            self.truth_add(scn, "dormant_reactivation", "SUSPICIOUS", "RULE-004", True, 1, "CONFIRMED_SUSPICIOUS", [a],
                           "Dormant >=185d then large debit")
        pk = self.picker("s8", self.c_dorm_benign)
        for _ in range(c["s8"]):
            a = pk(); scn = self.new_scn("S8")
            react, anchor = schedule(185, 205, 0)
            common(a, anchor, scn)
            X = round(rng.uniform(16000, 35000), 2)
            Y = round(X * rng.uniform(0.85, 1.0), 2)
            back = rdt(rng, datetime(2026, 1, 8), anchor - timedelta(days=3), 9, 20)
            self.ev(back, a, "POS_PURCHASE", Y, "PLANT", scn=scn, must=True, merchant=rng.choice(self.m_by_cat["ELECTRONICS"]),
                    desc="Prior comparable purchase (history)")
            self.ev(react, a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True, merchant=rng.choice(self.m_by_cat["TRAVEL"]),
                    device=self.A[a]["device"], channel="WEB", desc="Returning customer travel booking")
            self.need(a, X + Y)
            self.truth_add(scn, "dormant_reactivation_benign", "BENIGN_FALSE_POSITIVE", "RULE-004", True, 1,
                           "FALSE_POSITIVE", [a], "Dormant customer with comparable prior purchase, verified KYC")
        pk = self.picker("b3", self.c_dorm)
        for _ in range(c["b3"]):
            a = pk(); scn = self.new_scn("B3")
            react, anchor = schedule(150, 175, 0)
            react = max(react, datetime(2026, 8, 10))
            anchor = (react - timedelta(days=rng.randint(150, 175))).replace(hour=11, minute=rng.randint(0, 59), second=0)
            common(a, anchor, scn)
            X = round(rng.uniform(30000, 120000), 2)
            self.ev(react, a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True, merchant=rng.choice(mer["HIGH"]),
                    device=self.A[a]["device"], channel="WEB", desc="Boundary: gap just under 180 days")
            self.need(a, X)
            self.truth_add(scn, "boundary_dormant_gap_lt_180", "BOUNDARY_NO_ALERT", "RULE-004", False, 0, "NO_ALERT",
                           [a], "Gap 150-175 days (threshold 180): must NOT alert")
        # baseline dormant accounts that never come back (status DORMANT at as-of date)
        pk = self.picker("dorm_base", self.c_dorm)
        for _ in range(max(1, round(0.015 * self.cfg.n_accounts))):
            a = pk()
            self.A[a]["dormant_end"] = datetime(2026, 1, 20) + timedelta(days=rng.randint(0, 70), hours=rng.randint(9, 17))

    def plan_background(self):
        cfg, rng = self.cfg, sub_rng(self.cfg.seed, "bg_credits")
        for a in self.order:
            A = self.A[a]
            s, e = self.bounds(A)
            A["s"], A["e"] = s, e
            if s >= e:
                continue
            if A["primary"]:
                ttype = "SALARY_CREDIT" if A["occ"] in ("SALARIED", "RETIRED") else "CASH_DEPOSIT"
                spread = 0.05 if ttype == "SALARY_CREDIT" else 0.25
                for m in range(1, 11):
                    d = datetime(2026, m, min(A["payday"], 28), 9, 0, 0) + timedelta(minutes=rng.randint(0, 180))
                    if s <= d <= e:
                        self.ev(d, a, ttype, round(A["income"] * rng.uniform(1 - spread, 1 + spread), 2), "BG_CREDIT",
                                desc="Salary/pension credit" if ttype == "SALARY_CREDIT" else "Business receipts deposit")
            else:
                for m, dd in [(3, 31), (6, 30), (9, 30)]:
                    d = datetime(2026, m, dd, 0, 30, 0)
                    if s <= d <= e:
                        self.ev(d, a, "INTEREST_CREDIT", None, "BG_CREDIT", dyn="interest", desc="Quarterly interest")

    def plan_scenarios(self, c):
        cfg, mer = self.cfg, self.m_by_risk
        # ---- S1 velocity spike (suspicious)
        rng = sub_rng(cfg.seed, "s1"); pk = self.picker("s1", self.c_active)
        for _ in range(c["s1"]):
            a = pk(); A = self.A[a]; scn = self.new_scn("S1")
            t0 = rdt(rng, datetime(2026, 3, 1), datetime(2026, 10, 10))
            k = rng.randint(7, 12); span = rng.randint(900, 3000)
            offs = sorted(rng.randint(0, span) for _ in range(k)); offs[0], offs[-1] = 0, span
            n_high = rng.randint(2, max(2, k // 2)) if rng.random() < 0.65 else 0
            kinds = ["H"] * n_high + [rng.choice("OOA") for _ in range(k - n_high)]
            rng.shuffle(kinds)
            tot = 0.0
            for off, kd in zip(offs, kinds):
                when = t0 + timedelta(seconds=off)
                if kd == "A":
                    amt = float(rng.choice([2000, 5000, 10000]))
                    self.ev(when, a, "ATM_WITHDRAWAL", amt, "PLANT", scn=scn, must=True, desc="ATM withdrawal")
                else:
                    amt = round(rng.uniform(800, 6000), 2)
                    m = rng.choice(mer["HIGH"]) if kd == "H" else rng.choice(mer["LOW"] + mer["MEDIUM"])
                    self.ev(when, a, "ONLINE_PURCHASE", amt, "PLANT", scn=scn, must=True, merchant=m, device=A["device"],
                            channel=rng.choice(["WEB", "MOBILE_APP"]), desc="Online purchase")
                tot += amt
            self.need(a, tot)
            self.truth_add(scn, "velocity_spike", "SUSPICIOUS", "RULE-001", True, 1, "CONFIRMED_SUSPICIOUS", [a],
                           f"{k} debits in {span // 60} min")
        # ---- S2 velocity (benign false positive: repeat purchases at a known merchant)
        rng = sub_rng(cfg.seed, "s2"); pk = self.picker("s2", self.c_benign)
        for _ in range(c["s2"]):
            a = pk(); scn = self.new_scn("S2")
            m = rng.choice(self.m_by_cat["GROCERY"] + self.m_by_cat["FUEL"])
            t0 = rdt(rng, datetime(2026, 3, 1), datetime(2026, 10, 10), 10, 18)
            for _b in range(3):
                self.ev(rdt(rng, t0 - timedelta(days=40), t0 - timedelta(days=12), 9, 20), a, "POS_PURCHASE",
                        round(rng.uniform(300, 1500), 2), "PLANT", scn=scn, must=True, merchant=m, desc="Regular purchase (history)")
            k = rng.randint(6, 7); tot = 0
            for j in range(k):
                amt = round(rng.uniform(300, 1500), 2); tot += amt
                self.ev(t0 + timedelta(minutes=j * rng.randint(5, 8)), a, "POS_PURCHASE", amt, "PLANT", scn=scn, must=True,
                        merchant=m, desc="Bulk purchase at regular merchant")
            self.need(a, tot + 5000)
            self.truth_add(scn, "velocity_benign", "BENIGN_FALSE_POSITIVE", "RULE-001", True, 1, "FALSE_POSITIVE", [a],
                           "Repeat purchases at a known merchant")
        # ---- S3 new-device high value (suspicious); attacker devices are reused across 1-4 victims
        rng = sub_rng(cfg.seed, "s3"); pk = self.picker("s3", self.c_active)
        pool = [self.new_device(rng, None) for _ in range(max(1, (c["s3"] + 2) // 3))]
        for _ in range(c["s3"]):
            a = pk(); scn = self.new_scn("S3")
            t0 = rdt(rng, datetime(2026, 3, 1), datetime(2026, 10, 10))
            dev = rng.choice(pool); self.link(dev, a, t0)
            X = round(rng.uniform(50000, 180000), 2)
            m = rng.choice(mer["HIGH"]) if rng.random() < 0.7 else rng.choice(mer["MEDIUM"])
            self.ev(t0 + timedelta(minutes=rng.randint(5, 180)), a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True,
                    merchant=m, device=dev, channel="WEB", desc="High-value purchase from newly linked device")
            self.need(a, X)
            self.truth_add(scn, "new_device_high_value", "SUSPICIOUS", "RULE-002", True, 1, "CONFIRMED_SUSPICIOUS", [a],
                           "New device linked <3h before large purchase")
        # ---- S5 circular transfers (suspicious rings of 3-4)
        rng = sub_rng(cfg.seed, "ring"); pk = self.picker("ring", self.c_active)
        for _ in range(c["ring"]):
            k = rng.choice([3, 3, 4]); accts = [pk() for _ in range(k)]; scn = self.new_scn("S5")
            t0 = rdt(rng, datetime(2026, 4, 15), datetime(2026, 10, 8), 8, 20)
            rd = self.new_device(rng, None)
            for a in accts:
                self.link(rd, a, t0 - timedelta(days=10))
            X = round(rng.uniform(30000, 200000), 2); amt = X; when = t0
            self.need(accts[0], X)
            for i in range(k):
                self.transfer(when, accts[i], accts[(i + 1) % k], amt, "PLANT", rng.randint(10, 300), scn=scn, must=True,
                              device=rd, purpose="OTHER")
                amt = round(amt * rng.uniform(0.97, 1.0), 2)
                when = when + timedelta(hours=rng.randint(1, 10), minutes=rng.randint(0, 59))
            self.truth_add(scn, "circular_transfer", "SUSPICIOUS", "RULE-003", True, k, "CONFIRMED_SUSPICIOUS", accts,
                           f"{k}-account loop within 72h")
        # ---- S6 reciprocal transfer (benign: deposit paid and refunded between known counterparties)
        rng = sub_rng(cfg.seed, "recip"); pk = self.picker("recip", self.c_benign)
        for _ in range(c["recip"]):
            a, b = pk(), pk(); scn = self.new_scn("S6")
            t0 = rdt(rng, datetime(2026, 4, 20), datetime(2026, 10, 8), 9, 18)
            bk = round(rng.uniform(3000, 9000), 2); X = round(rng.uniform(25000, 90000), 2)
            self.transfer(t0 - timedelta(days=rng.randint(25, 90)), a, b, bk, "PLANT", 60, scn=scn, must=True, purpose="RENT")
            self.transfer(t0, a, b, X, "PLANT", 90, scn=scn, must=True, purpose="RENT")
            self.transfer(t0 + timedelta(hours=rng.randint(4, 40)), b, a, X, "PLANT", 90, scn=scn, must=True, purpose="RENT")
            self.need(a, X + bk)
            self.truth_add(scn, "reciprocal_transfer_benign", "BENIGN_FALSE_POSITIVE", "RULE-003", True, 2, "FALSE_POSITIVE",
                           [a, b], "Deposit paid and refunded between known counterparties")
        # ---- B1 / B2 boundary no-alert
        rng = sub_rng(cfg.seed, "b1"); pk = self.picker("b1", self.c_active)
        for _ in range(c["b1"]):
            a = pk(); scn = self.new_scn("B1")
            t0 = rdt(rng, datetime(2026, 3, 1), datetime(2026, 6, 25))
            dev = self.new_device(rng, None); self.link(dev, a, t0)
            X = rng.choice([24999.00, 22000.00])
            self.ev(t0 + timedelta(minutes=rng.randint(5, 120)), a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True,
                    merchant=rng.choice(mer["MEDIUM"]), device=dev, channel="WEB", desc="Boundary: just below v1 threshold")
            self.need(a, X)
            self.truth_add(scn, "boundary_new_device_below_threshold", "BOUNDARY_NO_ALERT", "RULE-002", False, 0, "NO_ALERT",
                           [a], "Amount below RULE-002 v1 threshold (25,000) before 2026-07-01: must NOT alert")
        rng = sub_rng(cfg.seed, "b2"); pk = self.picker("b2", self.c_active)
        for _ in range(c["b2"]):
            a = pk(); scn = self.new_scn("B2")
            t0 = rdt(rng, datetime(2026, 3, 1), datetime(2026, 10, 10)); tot = 0
            for j in range(5):
                amt = round(rng.uniform(300, 2500), 2); tot += amt
                self.ev(t0 + timedelta(minutes=j * rng.randint(5, 9)), a, "ONLINE_PURCHASE", amt, "PLANT", scn=scn, must=True,
                        merchant=rng.choice(mer["LOW"]), device=self.A[a]["device"], channel="WEB", desc="Boundary: 5 debits")
            self.need(a, tot)
            self.truth_add(scn, "boundary_velocity_below_count", "BOUNDARY_NO_ALERT", "RULE-001", False, 0, "NO_ALERT", [a],
                           "5 debits in <60 min (threshold 6): must NOT alert")
        # ---- S4 new-device benign false positives (also the calibration lever for the alert total)
        rng = sub_rng(cfg.seed, "s4"); pk = self.picker("s4", self.c_benign)
        for _ in range(c["s4"]):
            a = pk(); scn = self.new_scn("S4")
            t0 = rdt(rng, datetime(2026, 4, 20), datetime(2026, 10, 12), 9, 18)
            dev = self.new_device(rng, None); self.link(dev, a, t0)
            X = round(rng.uniform(26000, 48000), 2); Y = round(X * rng.uniform(0.85, 1.0), 2)
            self.ev(rdt(rng, max(datetime(2026, 1, 8), t0 - timedelta(days=120)), t0 - timedelta(days=25), 10, 19), a, "POS_PURCHASE", Y, "PLANT", scn=scn,
                    must=True, merchant=rng.choice(self.m_by_cat["ELECTRONICS"] + self.m_by_cat["TRAVEL"]),
                    desc="Prior comparable purchase (history)")
            self.ev(t0 + timedelta(minutes=rng.randint(5, 360)), a, "ONLINE_PURCHASE", X, "PLANT", scn=scn, must=True,
                    merchant=rng.choice(self.m_by_cat["ELECTRONICS"]), device=dev, channel="MOBILE_APP",
                    desc="Purchase after customer enrolled a new phone")
            self.need(a, X + Y)
            self.truth_add(scn, "new_device_benign", "BENIGN_FALSE_POSITIVE", "RULE-002", True, 1, "FALSE_POSITIVE", [a],
                           "New phone + comparable prior purchase, verified long-tenure customer")

    def plan_anomaly_events(self):
        cfg, n = self.cfg, self.cfg.n_edge
        rng = sub_rng(cfg.seed, "anom_txn")
        # A1: transaction posted to an already closed account
        pk = self.picker("a1", lambda a: a["close_date"] is not None and a["open_date"] <= date(2026, 6, 1)
                         and a["dormant_end"] is None)
        for _ in range(n):
            try:
                a = pk()
            except RuntimeError:
                break
            cd = self.A[a]["close_date"]
            when = datetime(cd.year, cd.month, cd.day, 12) + timedelta(days=rng.randint(2, 20))
            e = self.ev(when, a, "POS_PURCHASE", round(rng.uniform(100, 1500), 2), "ANOM", must=True,
                        merchant=rng.choice(self.m_by_risk["LOW"]), desc="Card purchase posted after account closure")
            self.anoms.append(dict(issue_type="TXN_REFERENCES_CLOSED_ACCOUNT", table="transactions", seq=e["seq"], account=a,
                                   checks=["txn_not_after_account_close"]))
        # A2: negative balance with no overdraft facility (amount derived at simulation time from the running balance)
        pk = self.picker("a2", lambda a: a["primary"] and a["ovd"] == 0 and a["close_date"] is None
                         and a["dormant_end"] is None and a["open_date"] <= date(2026, 4, 1))
        for _ in range(n):
            a = pk()
            when = rdt(rng, datetime(2026, 6, 1), datetime(2026, 10, 5), 10, 20)
            e = self.ev(when, a, "POS_PURCHASE", None, "ANOM", force=True, dyn="overdraw", extra=round(rng.uniform(500, 5000), 2),
                        merchant=rng.choice(self.m_by_risk["LOW"]), desc="Purchase exceeding available balance")
            self.anoms.append(dict(issue_type="NEGATIVE_BALANCE_NO_OVERDRAFT", table="transactions", seq=e["seq"], account=a,
                                   checks=["negative_balance_without_overdraft"]))

    def plan_bg_transfers(self):
        rng = sub_rng(self.cfg.seed, "bg_tr")
        prim = [a for a in self.order if self.A[a]["primary"] and self.A[a]["s"] < self.A[a]["e"]]
        allv = [a for a in self.order if self.A[a]["s"] < self.A[a]["e"]]
        for _ in range(round(0.03 * self.cfg.n_transactions)):
            for _try in range(6):
                src, dst = rng.choice(prim), rng.choice(allv)
                if src == dst:
                    continue
                lo = max(self.A[src]["s"], self.A[dst]["s"]); hi = min(self.A[src]["e"], self.A[dst]["e"]) - timedelta(hours=1)
                if hi <= lo + timedelta(days=1):
                    continue
                when = rdt(rng, lo, hi, 8, 21)
                if not (lo <= when <= hi):
                    continue
                amt = round(min(BG_TRANSFER_CAP, max(100.0, rng.lognormvariate(math.log(2500), 0.9))), 2)
                self.transfer(when, src, dst, amt, "BG_TR", rng.randint(5, 600),
                              device=rng.choice(self.cust_devices[self.A[src]["cust"]]),
                              purpose=rng.choice(["FAMILY_SUPPORT", "RENT", "PURCHASE", "INVESTMENT", "OTHER"]))
                break

    def bg_spends(self, n):
        rng = sub_rng(self.cfg.seed, f"bgspend:{n}")
        ids = [a for a in self.order if self.A[a]["primary"] and self.A[a]["s"] < self.A[a]["e"]]
        cum = list(accumulate((self.A[a]["e"] - self.A[a]["s"]).days * self.A[a]["activity"] for a in ids))
        out = []
        for _ in range(n):
            a = ids[bisect.bisect(cum, rng.random() * cum[-1])] if cum[-1] > 0 else ids[0]
            A = self.A[a]
            span = (A["e"] - A["s"]).total_seconds()
            d = A["s"] + timedelta(seconds=rng.random() * span)
            h = rng.choices(range(24), HOUR_W)[0]
            when = datetime(d.year, d.month, d.day, h, rng.randint(0, 59), rng.randint(0, 59))
            if not (A["s"] <= when <= A["e"]):
                when = d.replace(microsecond=0)
            ttype = rng.choices(["ONLINE_PURCHASE", "POS_PURCHASE", "ATM_WITHDRAWAL"], [35, 40, 25])[0]
            amt = min(BG_SPEND_CAP, max(50.0, A["income"] * math.exp(rng.gauss(math.log(0.09), 0.9))))
            if ttype == "ATM_WITHDRAWAL":
                amt = float(max(500, int(round(amt / 500.0)) * 500))
                out.append(dict(ts=when, acct=a, txn_type=ttype, amount=amt, merchant=None, device=None, channel="ATM",
                                desc="ATM withdrawal"))
            else:
                m = self.merchants[bisect.bisect(self.m_w, rng.random() * self.m_w[-1])]["merchant_id"]
                dev = rng.choice(self.cust_devices[A["cust"]]) if ttype == "ONLINE_PURCHASE" else None
                out.append(dict(ts=when, acct=a, txn_type=ttype, amount=round(amt, 2), merchant=m, device=dev,
                                channel=(rng.choice(["WEB", "MOBILE_APP"]) if dev else "POS"),
                                desc="Online purchase" if dev else "Card purchase"))
        return out

    # ---------------------------------------------------------------- ledger simulation
    def _simulate(self, events):
        evs = sorted(events, key=lambda e: (e["ts"], e["seq"]))
        bal = {a: self.A[a]["opening"] for a in self.A}
        dropped, kept = set(), []
        for e in evs:
            if e["tkey"] in dropped:
                continue
            a, amt = e["acct"], e["amount"]
            if e["dyn"] == "interest":
                amt = max(0.01, round(bal[a] * INT_RATE, 2))
            elif e["dyn"] == "overdraw":
                amt = round(max(bal[a], 0) + self.A[a]["ovd"] + e["extra"], 2)
            if e["dir"] == "DEBIT":
                if not e["force"] and amt > bal[a] + self.A[a]["ovd"] + 1e-9:
                    if e["must"]:
                        raise RuntimeError(f"planted debit infeasible: {e['scn']} {a} {amt} bal={bal[a]}")
                    if e["tkey"]:
                        dropped.add(e["tkey"])
                    continue
                bal[a] = round(bal[a] - amt, 2)
            else:
                bal[a] = round(bal[a] + amt, 2)
            kept.append(e)
        return kept

    def simulate_and_finalize(self):
        cfg = self.cfg
        base = list(self.events)
        n_spend = max(10, int((cfg.n_transactions - len(base) * 0.97) * 1.12))
        for attempt in range(8):
            spends = self.bg_spends(n_spend)
            sp_events = []
            for s in spends:   # events get fresh deterministic seq numbers
                sp_events.append(dict(ts=s["ts"], seq=next(self.seq), acct=s["acct"], txn_type=s["txn_type"],
                                      dir="DEBIT", amount=s["amount"], kind="BG_SPEND", merchant=s["merchant"],
                                      device=s["device"], channel=s["channel"], tkey=None, scn=None, must=False,
                                      force=False, dyn=None, extra=0.0, desc=s["desc"]))
            kept = self._simulate(base + sp_events)
            if len(kept) >= cfg.n_transactions:
                break
            n_spend = int(n_spend * 1.15) + 20
        else:
            raise Shortage("could not reach target transaction volume")
        surplus = len(kept) - cfg.n_transactions
        if surplus:
            cands = [e for e in kept if e["kind"] == "BG_SPEND"]
            if len(cands) < surplus:
                raise Shortage("not enough background spends to trim")
            rm = {e["seq"] for e in sub_rng(cfg.seed, "trim").sample(cands, surplus)}
            kept = [e for e in kept if e["seq"] not in rm]
        # A3: transfer legs that do not reconcile (credit leg inflated by 8%; labelled)
        rng = sub_rng(cfg.seed, "anom_tr")
        pairs = {}
        for e in kept:
            if e["kind"] == "BG_TR":
                pairs.setdefault(e["tkey"], []).append(e)
        cand = sorted(k for k, v in pairs.items() if len(v) == 2)
        self.a3 = []
        for k in rng.sample(cand, min(cfg.n_edge, len(cand))):
            leg_in = next(e for e in pairs[k] if e["txn_type"] == "TRANSFER_IN")
            leg_in["amount"] = round(leg_in["amount"] * 1.08, 2)
            self.a3.append((k, leg_in["seq"]))
        # finalize: ids, running balances
        kept.sort(key=lambda e: (e["ts"], e["seq"]))
        bal = {a: self.A[a]["opening"] for a in self.A}
        self.txns, self.seq2txn = [], {}
        mname = {m["merchant_id"]: m["merchant_name"] for m in self.merchants}
        for i, e in enumerate(kept, 1):
            a, amt = e["acct"], e["amount"]
            if e["dyn"] == "interest":
                amt = max(0.01, round(bal[a] * INT_RATE, 2))
            elif e["dyn"] == "overdraw":
                amt = round(max(bal[a], 0) + self.A[a]["ovd"] + e["extra"], 2)
            bal[a] = round(bal[a] + (amt if e["dir"] == "CREDIT" else -amt), 2)
            tid = txn_id(i)
            self.seq2txn[e["seq"]] = tid
            e["_id"], e["_amt"] = tid, amt
            d = e["desc"] + (f" - {mname[e['merchant']]}" if e["merchant"] else "")
            self.txns.append(dict(transaction_id=tid, account_id=a, txn_ts=ts(e["ts"]), txn_type=e["txn_type"],
                                  direction=e["dir"], amount=amt, currency="INR", balance_after=bal[a], channel=e["channel"],
                                  merchant_id=e["merchant"], device_id=e["device"], transfer_id=None, description=d))
        self.final_balance = bal
        # transfer links
        by_key = {}
        for e in kept:
            if e["tkey"]:
                by_key.setdefault(e["tkey"], {})[e["txn_type"]] = e
        self.transfers, tn = [], 0
        tx_by_id = {t["transaction_id"]: t for t in self.txns}
        for key, legs in sorted(by_key.items(), key=lambda kv: kv[1]["TRANSFER_OUT"]["_id"]):
            o, i_ = legs["TRANSFER_OUT"], legs["TRANSFER_IN"]
            tn += 1
            tid = trf_id(tn)
            self.transfers.append(dict(transfer_id=tid, source_account_id=o["acct"], dest_account_id=i_["acct"],
                                       out_txn_id=o["_id"], in_txn_id=i_["_id"], amount=o["_amt"],
                                       initiated_at=ts(o["ts"]), completed_at=ts(i_["ts"]), status="COMPLETED",
                                       purpose=self.tpurpose[key]))
            tx_by_id[o["_id"]]["transfer_id"] = tid
            tx_by_id[i_["_id"]]["transfer_id"] = tid
            o["_trf"] = tid
        self.kept = kept
        # link last_seen / device first_seen (derived)
        for t in self.txns:
            if t["device_id"]:
                k = (t["device_id"], t["account_id"])
                lk = self.links[k]
                if lk["last_seen_at"] is None or t["txn_ts"] > lk["last_seen_at"]:
                    lk["last_seen_at"] = t["txn_ts"]
        # truth / anomaly resolution
        scn_txn, scn_acc = {}, {}
        for e in kept:
            if e["scn"]:
                scn_txn.setdefault(e["scn"], []).append(e["_id"])
        for tr in self.truth:
            tr["txn_ids"] = scn_txn.get(tr["scenario_id"], [])
        for a in self.anoms:
            a["entity_id"] = self.seq2txn[a["seq"]]
        trf_of = {e["seq"]: e.get("_trf") for e in kept}
        out_leg = {}
        for e in kept:
            if e["kind"] == "BG_TR" and e["txn_type"] == "TRANSFER_OUT":
                out_leg[e["tkey"]] = e["_trf"]
        for k, in_seq in self.a3:
            self.anoms.append(dict(issue_type="TRANSFER_LEGS_DO_NOT_RECONCILE", table="transfer_links",
                                   entity_id=out_leg[k], related=[self.seq2txn[in_seq]], checks=["transfer_legs_reconcile"]))

    # ---------------------------------------------------------------- tables
    def build(self):
        c = self.counts()
        self.gen_master()
        self.plan_dormant(c)
        self.plan_background()
        self.plan_scenarios(c)
        self.plan_anomaly_events()
        self.plan_bg_transfers()
        self.simulate_and_finalize()
        return self

    def master_tables(self):
        return dict(customers=self.customers, kyc_profiles=self.kyc, account_parties=self.parties,
                    merchants=self.merchants, analyst_users=self.users)
