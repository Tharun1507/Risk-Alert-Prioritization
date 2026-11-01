"""Shared helpers: configuration, deterministic ID formatters, time helpers."""
import random
from datetime import datetime, date

# Simulation calendar (fixed so every run is identical)
WS = datetime(2026, 1, 1, 0, 0, 0)          # first transaction instant
WE = datetime(2026, 10, 15, 23, 59, 59)     # last transaction instant
AS_OF = datetime(2026, 10, 22, 23, 59, 59)  # "today" for statuses / open cases


def ts(dt):
    return dt.replace(microsecond=0).isoformat()


def pts(s):
    return datetime.fromisoformat(s)


def pds(s):
    return date.fromisoformat(s)


def sub_rng(seed, name):
    """Independent, deterministic RNG stream per generator stage (string seeds are hash-stable)."""
    return random.Random(f"{seed}|{name}")


# ---- ID conventions (immutable, zero padded) ----
def cus_id(n): return f"CUS-{n:06d}"
def kyc_id(n): return f"KYC-{n:06d}"
def acc_id(cust, seq): return f"ACC-{cust}-{seq:02d}"       # ACC-CUS-000001-01
def mrc_id(n): return f"MRC-{n:06d}"
def dev_id(n): return f"DEV-{n:06d}"
def txn_id(n): return f"TXN-2026-{n:08d}"
def trf_id(n): return f"TRF-2026-{n:06d}"
def alt_id(n): return f"ALT-2026-{n:06d}"
def case_id(n): return f"CASE-2026-{n:06d}"
def act_id(n): return f"ACT-2026-{n:06d}"
def ase_id(n): return f"ASE-{n:07d}"
def usr_id(n): return f"USR-{n:03d}"


class Config:
    """All volumes derive from `scale` so the starter and full datasets share one generator."""

    BASE = dict(customers=5000, accounts=7000, transactions=100000, alerts=1000)

    def __init__(self, seed=20261022, scale=1.0, **over):
        self.seed = int(seed)
        self.scale = float(scale)

        def r(x, lo=1):
            return max(lo, int(round(x * self.scale)))

        self.n_customers = over.get("n_customers") or r(self.BASE["customers"], 20)
        self.n_accounts = over.get("n_accounts") or r(self.BASE["accounts"], 30)
        self.n_transactions = over.get("n_transactions") or r(self.BASE["transactions"], 500)
        self.n_alerts = over.get("n_alerts") or r(self.BASE["alerts"], 20)
        self.n_merchants = r(400, 40)
        self.n_edge = max(2, r(6))               # injected instances per edge-case type
        self.n_ops = r(20, 4)
        self.n_reviewers = r(5, 2)
        self.n_admins = 2
        if not (self.n_customers <= self.n_accounts <= 2 * self.n_customers):
            raise ValueError("need customers <= accounts <= 2*customers")

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}
