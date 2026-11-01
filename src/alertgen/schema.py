"""Single source of truth for the data model.

The same definitions drive: the data dictionary (docs), SQL DDL, CSV/JSON export column order,
CSV type-conversion on reload, and the generic checks in validate.py (PK, required, enum, pattern, FK, unique).
"""
from collections import OrderedDict

KYC_STATUS = ["VERIFIED", "PENDING", "EXPIRED"]
TIER = ["LOW", "MEDIUM", "HIGH"]
OCC = ["SALARIED", "SELF_EMPLOYED", "STUDENT", "RETIRED", "BUSINESS_OWNER"]
ACCT_TYPE = ["CHECKING", "SAVINGS"]
ACCT_STATUS = ["ACTIVE", "DORMANT", "FROZEN", "CLOSED"]
TXN_TYPES = ["SALARY_CREDIT", "INTEREST_CREDIT", "CASH_DEPOSIT", "TRANSFER_IN",
             "POS_PURCHASE", "ONLINE_PURCHASE", "ATM_WITHDRAWAL", "TRANSFER_OUT"]
DIRS = {"SALARY_CREDIT": "CREDIT", "INTEREST_CREDIT": "CREDIT", "CASH_DEPOSIT": "CREDIT", "TRANSFER_IN": "CREDIT",
        "POS_PURCHASE": "DEBIT", "ONLINE_PURCHASE": "DEBIT", "ATM_WITHDRAWAL": "DEBIT", "TRANSFER_OUT": "DEBIT"}
CHANNELS = ["BRANCH", "ATM", "POS", "MOBILE_APP", "WEB", "SYSTEM"]
RISK = ["LOW", "MEDIUM", "HIGH"]
DEV_TYPE = ["MOBILE", "TABLET", "DESKTOP"]
OS = ["ANDROID", "IOS", "WINDOWS", "MACOS"]
PRIORITY = ["P1", "P2", "P3"]
CASE_STATUS = ["OPEN", "IN_REVIEW", "ESCALATED", "CLOSED"]
DISPOSITION = ["CONFIRMED_SUSPICIOUS", "FALSE_POSITIVE"]
ACTION_TYPES = ["ASSIGN", "REQUEST_INFO", "ESCALATE", "FREEZE_ACCOUNT", "CLOSE_CASE"]
ROLES = ["OPS_ANALYST", "RISK_REVIEWER", "SYS_ADMIN"]

ACCOUNT_TRANSITIONS = {(None, "ACTIVE"), ("ACTIVE", "DORMANT"), ("DORMANT", "ACTIVE"), ("ACTIVE", "FROZEN"),
                       ("DORMANT", "FROZEN"), ("ACTIVE", "CLOSED"), ("DORMANT", "CLOSED"), ("FROZEN", "CLOSED")}
ACTION_TRANSITIONS = {"ASSIGN": {("OPEN", "IN_REVIEW")},
                      "REQUEST_INFO": {("IN_REVIEW", "IN_REVIEW")},
                      "ESCALATE": {("IN_REVIEW", "ESCALATED")},
                      "FREEZE_ACCOUNT": {("ESCALATED", "ESCALATED")},
                      "CLOSE_CASE": {("IN_REVIEW", "CLOSED"), ("ESCALATED", "CLOSED")}}


def F(name, typ, req=True, desc="", allowed=None, unit="", unique=False, pattern=None, rule="", ddl_null=False):
    return dict(name=name, type=typ, required=req, desc=desc, allowed=allowed, unit=unit,
                unique=unique, pattern=pattern, rule=rule, ddl_null=ddl_null)


TABLES = OrderedDict()


def table(name, desc, pk, fields, fks=(), cardinality="", lifecycle=""):
    TABLES[name] = dict(desc=desc, pk=list(pk), fields=fields, fks=list(fks),
                        cardinality=cardinality, lifecycle=lifecycle)


table("customers", "Account holder master record (synthetic person).", ["customer_id"], [
    F("customer_id", "string", desc="Immutable customer key", unique=True, pattern=r"^CUS-\d{6}$"),
    F("full_name", "string", desc="Synthetic name (random first+last, not a real person)"),
    F("date_of_birth", "date", desc="Birth date", rule="age >= 18 at customer_since"),
    F("city", "string", desc="Home city"),
    F("country_code", "string", desc="ISO-3166 alpha-2", allowed=["IN"]),
    F("occupation_band", "string", desc="Occupation band", allowed=OCC),
    F("monthly_income", "number", desc="Declared monthly income", unit="INR", rule="> 0"),
    F("customer_since", "date", desc="Onboarding date"),
    F("status", "string", desc="Derived: CLOSED iff every owned account is CLOSED", allowed=["ACTIVE", "CLOSED"]),
], cardinality="1:N accounts (as primary owner); 1:1 kyc_profiles; N:M accounts via account_parties",
    lifecycle="ACTIVE -> CLOSED (derived from account events).")

table("kyc_profiles", "KYC profile, one per customer.", ["kyc_id"], [
    F("kyc_id", "string", unique=True, pattern=r"^KYC-\d{6}$"),
    F("customer_id", "string", unique=True, desc="1:1 with customer"),
    F("kyc_status", "string", allowed=KYC_STATUS),
    F("risk_tier", "string", desc="Static customer risk tier", allowed=TIER),
    F("verified_date", "date", req=False, desc="Date of last verification; NULL when PENDING",
      rule="verified_date >= customer_since"),
    F("expiry_date", "date", req=False, desc="KYC expiry; NULL when PENDING",
      rule="VERIFIED => expiry >= as-of date; EXPIRED => expiry < as-of date"),
    F("id_document_type", "string", allowed=["PASSPORT", "NATIONAL_ID", "DRIVING_LICENCE"]),
    F("pep_flag", "integer", desc="Politically exposed person flag", allowed=[0, 1]),
    F("source_of_funds", "string", allowed=["SALARY", "BUSINESS_INCOME", "PENSION", "SAVINGS", "FAMILY_SUPPORT"]),
    F("effective_from", "date", desc="Start of validity of this profile row"),
], fks=[("customer_id", "customers", "customer_id")], cardinality="1:1 with customers",
    lifecycle="PENDING -> VERIFIED -> EXPIRED (snapshot at as-of date).")

table("accounts", "Deposit account. ID embeds the primary owner: ACC-<customer-id>-NN.", ["account_id"], [
    F("account_id", "string", unique=True, pattern=r"^ACC-CUS-\d{6}-\d{2}$"),
    F("primary_customer_id", "string", desc="Primary owner"),
    F("account_type", "string", allowed=ACCT_TYPE),
    F("currency", "string", allowed=["INR"]),
    F("open_date", "date", rule=">= customer_since"),
    F("close_date", "date", req=False, rule="NULL unless status = CLOSED; >= open_date"),
    F("status", "string", desc="Derived: latest account_status_events.to_status", allowed=ACCT_STATUS),
    F("opening_balance", "number", unit="INR", rule=">= 0"),
    F("overdraft_limit", "number", unit="INR", rule=">= 0; balance_after of a debit must be >= -overdraft_limit"),
    F("current_balance", "number", unit="INR", desc="Derived: balance_after of latest transaction (else opening_balance)"),
    F("last_activity_at", "timestamp", req=False, desc="Derived: timestamp of latest transaction"),
], fks=[("primary_customer_id", "customers", "customer_id")],
    cardinality="N:1 customers (primary); N:M customers via account_parties; 1:N transactions",
    lifecycle="ACTIVE -> DORMANT -> ACTIVE; ACTIVE|DORMANT -> FROZEN -> CLOSED; ACTIVE|DORMANT -> CLOSED (see account_status_events).")

table("account_parties", "Bridge table: N:M customers <-> accounts (owner / joint owner).",
      ["account_id", "customer_id"], [
    F("account_id", "string"), F("customer_id", "string"),
    F("party_role", "string", allowed=["OWNER", "JOINT_OWNER"]),
    F("effective_from", "date"), F("effective_to", "date", req=False),
], fks=[("account_id", "accounts", "account_id"), ("customer_id", "customers", "customer_id")],
    cardinality="N:M")

table("account_status_events", "Append-only history of account lifecycle state changes (never overwritten).",
      ["event_id"], [
    F("event_id", "string", unique=True, pattern=r"^ASE-\d{7}$"),
    F("account_id", "string"),
    F("event_ts", "timestamp"),
    F("from_status", "string", req=False, allowed=ACCT_STATUS, desc="NULL for the opening event"),
    F("to_status", "string", allowed=ACCT_STATUS, rule="(from,to) must be an allowed transition"),
    F("reason_code", "string", allowed=["ACCOUNT_OPENED", "INACTIVITY_180D", "ACTIVITY_RESUMED", "CUSTOMER_REQUEST",
                                       "ANALYST_FREEZE"]),
    F("source", "string", allowed=["SYSTEM", "ANALYST_ACTION"]),
    F("action_id", "string", req=False, desc="Set when source = ANALYST_ACTION"),
], fks=[("account_id", "accounts", "account_id"), ("action_id", "analyst_actions", "action_id")],
    cardinality="N:1 accounts; N:1 analyst_actions (optional)")

table("merchants", "Merchant master.", ["merchant_id"], [
    F("merchant_id", "string", unique=True, pattern=r"^MRC-\d{6}$"),
    F("merchant_name", "string"),
    F("mcc", "integer", desc="Merchant category code"),
    F("category", "string"),
    F("risk_level", "string", allowed=RISK),
    F("country_code", "string", allowed=["IN"]),
    F("status", "string", allowed=["ACTIVE", "INACTIVE"]),
    F("onboarded_date", "date"),
], cardinality="1:N transactions (optional on transaction)")

table("devices", "Device master (synthetic fingerprints).", ["device_id"], [
    F("device_id", "string", unique=True, pattern=r"^DEV-\d{6}$"),
    F("device_type", "string", allowed=DEV_TYPE),
    F("os_family", "string", allowed=OS),
    F("fingerprint_hash", "string", unique=True, desc="Synthetic SHA-1 prefix, no real device data"),
    F("first_seen_at", "timestamp", desc="Derived: earliest device_account_links.first_seen_at"),
    F("status", "string", allowed=["ACTIVE", "BLOCKED"]),
], cardinality="N:M accounts via device_account_links")

table("device_account_links", "Bridge table: N:M devices <-> accounts, with first/last use.",
      ["device_id", "account_id"], [
    F("device_id", "string"), F("account_id", "string"),
    F("first_seen_at", "timestamp", rule="<= every transaction using this device on this account"),
    F("last_seen_at", "timestamp", req=False, desc="Derived: latest transaction using the pair"),
], fks=[("device_id", "devices", "device_id"), ("account_id", "accounts", "account_id")], cardinality="N:M")

table("transactions", "Timestamped debit/credit ledger entries; balance_after is a running balance per account.",
      ["transaction_id"], [
    F("transaction_id", "string", unique=True, pattern=r"^TXN-2026-\d{8}$"),
    F("account_id", "string"),
    F("txn_ts", "timestamp"),
    F("txn_type", "string", allowed=TXN_TYPES),
    F("direction", "string", allowed=["DEBIT", "CREDIT"], rule="must match txn_type"),
    F("amount", "number", unit="INR", rule="> 0"),
    F("currency", "string", allowed=["INR"]),
    F("balance_after", "number", unit="INR", rule="= previous balance_after (or opening_balance) +/- amount"),
    F("channel", "string", allowed=CHANNELS),
    F("merchant_id", "string", req=False, desc="Set for purchases"),
    F("device_id", "string", req=False, desc="Must be linked to the account via device_account_links"),
    F("transfer_id", "string", req=False, desc="Set for TRANSFER_OUT/TRANSFER_IN legs"),
    F("description", "string"),
], fks=[("account_id", "accounts", "account_id"), ("merchant_id", "merchants", "merchant_id"),
        ("device_id", "devices", "device_id"), ("transfer_id", "transfer_links", "transfer_id")],
    cardinality="N:1 accounts; N:1 merchants (opt); N:1 devices (opt); N:1 transfer_links (opt, 2 legs)",
    lifecycle="Append-only; no transaction after account close or freeze.")

table("transfer_links", "Links the debit leg and credit leg of an internal transfer.", ["transfer_id"], [
    F("transfer_id", "string", unique=True, pattern=r"^TRF-2026-\d{6}$"),
    F("source_account_id", "string"), F("dest_account_id", "string", rule="<> source_account_id"),
    F("out_txn_id", "string", unique=True), F("in_txn_id", "string", unique=True),
    F("amount", "number", unit="INR", rule="= out leg amount = in leg amount"),
    F("initiated_at", "timestamp"), F("completed_at", "timestamp", rule=">= initiated_at"),
    F("status", "string", allowed=["COMPLETED"]),
    F("purpose", "string", allowed=["FAMILY_SUPPORT", "RENT", "PURCHASE", "INVESTMENT", "OTHER"]),
], fks=[("source_account_id", "accounts", "account_id"), ("dest_account_id", "accounts", "account_id"),
        ("out_txn_id", "transactions", "transaction_id"), ("in_txn_id", "transactions", "transaction_id")],
    cardinality="1:1 with each of its two transactions")

table("analyst_users", "Internal users: ops analysts, risk/compliance reviewers, system administrators.",
      ["user_id"], [
    F("user_id", "string", unique=True, pattern=r"^USR-\d{3}$"),
    F("full_name", "string"), F("role", "string", allowed=ROLES), F("team", "string"),
    F("active_from", "date"), F("status", "string", allowed=["ACTIVE", "INACTIVE"]),
], cardinality="1:N analyst_actions; 1:N risk_rules (created_by)")

table("risk_rules", "Versioned, transparent rule catalogue. One row per rule version (effective-dated).",
      ["rule_version_id"], [
    F("rule_version_id", "string", unique=True, pattern=r"^RULE-\d{3}-v\d+$"),
    F("rule_id", "string", pattern=r"^RULE-\d{3}$"),
    F("rule_name", "string"), F("version", "integer", rule=">= 1"),
    F("description", "string"),
    F("base_weight", "number", unit="points", desc="Starting score (RULE-003 base depends on cycle length, see params)"),
    F("params", "json", desc="Thresholds used by the engine"),
    F("effective_from", "timestamp"), F("effective_to", "timestamp", req=False, desc="Exclusive; NULL = current"),
    F("created_by", "string", desc="SYS_ADMIN user"),
    F("status", "string", allowed=["ACTIVE", "RETIRED"]),
], fks=[("created_by", "analyst_users", "user_id")], cardinality="1:N alerts",
    lifecycle="Version N retired when version N+1 becomes effective.")

table("alerts", "A rule hit with its triggering fact, explainable score and case link.", ["alert_id"], [
    F("alert_id", "string", unique=True, pattern=r"^ALT-2026-\d{6}$"),
    F("rule_version_id", "string"), F("account_id", "string"),
    F("trigger_txn_id", "string", ddl_null=True, desc="The transaction that completed the rule condition",
      rule="required; transaction must belong to account_id and precede alert_ts"),
    F("alert_ts", "timestamp", rule=">= trigger transaction timestamp"),
    F("base_score", "number", unit="points"),
    F("adjustment_total", "number", unit="points", rule="= sum of score_components points"),
    F("risk_score", "number", unit="points", rule="= clip(base_score + adjustment_total, 0, 100)"),
    F("priority_band", "string", allowed=PRIORITY, rule="P1 >= 70, P2 40-69, P3 < 40"),
    F("group_key", "string", req=False, desc="Shared by alerts from the same detected structure (e.g. a transfer cycle)"),
    F("explanation", "string", desc="Human-readable reason"),
    F("evidence", "json", desc="Facts and thresholds that fired the rule"),
    F("score_components", "json", desc="[{code, points}] adjustments applied to base score"),
    F("case_id", "string"),
], fks=[("rule_version_id", "risk_rules", "rule_version_id"), ("account_id", "accounts", "account_id"),
        ("trigger_txn_id", "transactions", "transaction_id"), ("case_id", "cases", "case_id")],
    cardinality="N:1 cases; N:1 risk_rules; N:M transactions via alert_transactions",
    lifecycle="Immutable once raised; disposition lives on the case.")

table("alert_transactions", "Bridge table: N:M alerts <-> transactions (trigger + supporting evidence).",
      ["alert_id", "transaction_id"], [
    F("alert_id", "string"), F("transaction_id", "string"),
    F("evidence_role", "string", allowed=["TRIGGER", "EVIDENCE"]),
], fks=[("alert_id", "alerts", "alert_id"), ("transaction_id", "transactions", "transaction_id")], cardinality="N:M")

table("cases", "Groups related alerts for analyst review; state is derived from analyst_actions.", ["case_id"], [
    F("case_id", "string", unique=True, pattern=r"^CASE-2026-\d{6}$"),
    F("opened_at", "timestamp", desc="Earliest alert_ts + 5 minutes"),
    F("closed_at", "timestamp", req=False, desc="Timestamp of CLOSE_CASE action"),
    F("status", "string", allowed=CASE_STATUS, desc="Derived: to_status of the latest analyst action (OPEN if none)"),
    F("priority_band", "string", allowed=PRIORITY),
    F("case_score", "number", unit="points", desc="min(100, max alert score + 5 per additional distinct rule)"),
    F("alert_count", "integer", rule="= number of alerts with this case_id"),
    F("primary_account_id", "string"), F("primary_customer_id", "string"),
    F("assigned_user_id", "string", req=False),
    F("sla_due_at", "timestamp", desc="opened_at + 24h (P1) / 72h (P2) / 168h (P3)"),
    F("disposition", "string", req=False, allowed=DISPOSITION, desc="Set only when status = CLOSED"),
], fks=[("primary_account_id", "accounts", "account_id"), ("primary_customer_id", "customers", "customer_id"),
        ("assigned_user_id", "analyst_users", "user_id")],
    cardinality="1:N alerts; 1:N analyst_actions",
    lifecycle="OPEN -> IN_REVIEW -> [ESCALATED] -> CLOSED.")

table("analyst_actions", "Append-only analyst/reviewer event log; every case state change is one row.",
      ["action_id"], [
    F("action_id", "string", unique=True, pattern=r"^ACT-2026-\d{6}$"),
    F("case_id", "string"), F("user_id", "string"),
    F("action_ts", "timestamp", rule=">= case.opened_at"),
    F("action_type", "string", allowed=ACTION_TYPES),
    F("from_status", "string", allowed=CASE_STATUS), F("to_status", "string", allowed=CASE_STATUS,
                                                      rule="(action_type, from, to) must be an allowed transition"),
    F("disposition", "string", req=False, allowed=DISPOSITION, desc="Required on CLOSE_CASE"),
    F("account_id", "string", req=False, desc="Target account for FREEZE_ACCOUNT"),
    F("notes", "string", desc="Rationale referencing rules/scores"),
], fks=[("case_id", "cases", "case_id"), ("user_id", "analyst_users", "user_id"),
        ("account_id", "accounts", "account_id")], cardinality="N:1 cases; N:1 analyst_users")

TYPE_SQL = {"string": "TEXT", "integer": "INTEGER", "number": "REAL", "date": "TEXT", "timestamp": "TEXT",
            "json": "TEXT", "boolean": "INTEGER"}


def ddl():
    out = []
    for name, t in TABLES.items():
        cols = []
        for f in t["fields"]:
            c = f"  {f['name']} {TYPE_SQL[f['type']]}"
            if f["required"] and not f["ddl_null"]:
                c += " NOT NULL"
            if f["allowed"]:
                vals = ", ".join(repr(v) if isinstance(v, str) else str(v) for v in f["allowed"])
                c += f" CHECK ({f['name']} IN ({vals}))" if not f["required"] else f" CHECK ({f['name']} IN ({vals}))"
            cols.append(c)
        cols.append(f"  PRIMARY KEY ({', '.join(t['pk'])})")
        for f in t["fields"]:
            if f["unique"] and t["pk"] != [f["name"]]:
                cols.append(f"  UNIQUE ({f['name']})")
        for c, pt, pc in t["fks"]:
            cols.append(f"  FOREIGN KEY ({c}) REFERENCES {pt}({pc})")
        out.append(f"CREATE TABLE {name} (\n" + ",\n".join(cols) + "\n);")
    return "\n\n".join(out) + "\n"


def data_dictionary_md():
    L = ["# Data dictionary", "",
         "Generated from `src/alertgen/schema.py` (the same definitions drive the SQL DDL and the validator).", "",
         "Conventions: timestamps are ISO-8601 local naive (`YYYY-MM-DDTHH:MM:SS`), dates `YYYY-MM-DD`, money in INR "
         "with 2 decimals, `Req` = required (NOT NULL unless noted), IDs immutable and zero-padded.", ""]
    for name, t in TABLES.items():
        L += [f"## {name}", "", t["desc"], "",
              f"- **Primary key:** `{', '.join(t['pk'])}`",
              "- **Foreign keys:** " + (", ".join(f"`{c}` -> `{p}.{pc}`" for c, p, pc in t["fks"]) or "none"),
              f"- **Cardinality:** {t['cardinality'] or 'n/a'}"]
        if t["lifecycle"]:
            L.append(f"- **Lifecycle / effective dating:** {t['lifecycle']}")
        L += ["", "| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |",
              "|---|---|---|---|---|---|---|"]
        for f in t["fields"]:
            allowed = ", ".join(map(str, f["allowed"])) if f["allowed"] else (f"`{f['pattern']}`" if f["pattern"] else "")
            note = "; ".join(x for x in [f["desc"], f["rule"]] if x)
            L.append(f"| {f['name']} | {f['type']} | {'Y' if f['required'] else 'N'} | {allowed} | {f['unit']} | "
                     f"{'Y' if f['unique'] or f['name'] in t['pk'] and len(t['pk']) == 1 else ''} | {note} |")
        L.append("")
    return "\n".join(L)
