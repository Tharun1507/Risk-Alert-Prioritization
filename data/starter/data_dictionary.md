# Data dictionary

Generated from `src/alertgen/schema.py` (the same definitions drive the SQL DDL and the validator).

Conventions: timestamps are ISO-8601 local naive (`YYYY-MM-DDTHH:MM:SS`), dates `YYYY-MM-DD`, money in INR with 2 decimals, `Req` = required (NOT NULL unless noted), IDs immutable and zero-padded.

## customers

Account holder master record (synthetic person).

- **Primary key:** `customer_id`
- **Foreign keys:** none
- **Cardinality:** 1:N accounts (as primary owner); 1:1 kyc_profiles; N:M accounts via account_parties
- **Lifecycle / effective dating:** ACTIVE -> CLOSED (derived from account events).

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| customer_id | string | Y | `^CUS-\d{6}$` |  | Y | Immutable customer key |
| full_name | string | Y |  |  |  | Synthetic name (random first+last, not a real person) |
| date_of_birth | date | Y |  |  |  | Birth date; age >= 18 at customer_since |
| city | string | Y |  |  |  | Home city |
| country_code | string | Y | IN |  |  | ISO-3166 alpha-2 |
| occupation_band | string | Y | SALARIED, SELF_EMPLOYED, STUDENT, RETIRED, BUSINESS_OWNER |  |  | Occupation band |
| monthly_income | number | Y |  | INR |  | Declared monthly income; > 0 |
| customer_since | date | Y |  |  |  | Onboarding date |
| status | string | Y | ACTIVE, CLOSED |  |  | Derived: CLOSED iff every owned account is CLOSED |

## kyc_profiles

KYC profile, one per customer.

- **Primary key:** `kyc_id`
- **Foreign keys:** `customer_id` -> `customers.customer_id`
- **Cardinality:** 1:1 with customers
- **Lifecycle / effective dating:** PENDING -> VERIFIED -> EXPIRED (snapshot at as-of date).

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| kyc_id | string | Y | `^KYC-\d{6}$` |  | Y |  |
| customer_id | string | Y |  |  | Y | 1:1 with customer |
| kyc_status | string | Y | VERIFIED, PENDING, EXPIRED |  |  |  |
| risk_tier | string | Y | LOW, MEDIUM, HIGH |  |  | Static customer risk tier |
| verified_date | date | N |  |  |  | Date of last verification; NULL when PENDING; verified_date >= customer_since |
| expiry_date | date | N |  |  |  | KYC expiry; NULL when PENDING; VERIFIED => expiry >= as-of date; EXPIRED => expiry < as-of date |
| id_document_type | string | Y | PASSPORT, NATIONAL_ID, DRIVING_LICENCE |  |  |  |
| pep_flag | integer | Y | 0, 1 |  |  | Politically exposed person flag |
| source_of_funds | string | Y | SALARY, BUSINESS_INCOME, PENSION, SAVINGS, FAMILY_SUPPORT |  |  |  |
| effective_from | date | Y |  |  |  | Start of validity of this profile row |

## accounts

Deposit account. ID embeds the primary owner: ACC-<customer-id>-NN.

- **Primary key:** `account_id`
- **Foreign keys:** `primary_customer_id` -> `customers.customer_id`
- **Cardinality:** N:1 customers (primary); N:M customers via account_parties; 1:N transactions
- **Lifecycle / effective dating:** ACTIVE -> DORMANT -> ACTIVE; ACTIVE|DORMANT -> FROZEN -> CLOSED; ACTIVE|DORMANT -> CLOSED (see account_status_events).

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| account_id | string | Y | `^ACC-CUS-\d{6}-\d{2}$` |  | Y |  |
| primary_customer_id | string | Y |  |  |  | Primary owner |
| account_type | string | Y | CHECKING, SAVINGS |  |  |  |
| currency | string | Y | INR |  |  |  |
| open_date | date | Y |  |  |  | >= customer_since |
| close_date | date | N |  |  |  | NULL unless status = CLOSED; >= open_date |
| status | string | Y | ACTIVE, DORMANT, FROZEN, CLOSED |  |  | Derived: latest account_status_events.to_status |
| opening_balance | number | Y |  | INR |  | >= 0 |
| overdraft_limit | number | Y |  | INR |  | >= 0; balance_after of a debit must be >= -overdraft_limit |
| current_balance | number | Y |  | INR |  | Derived: balance_after of latest transaction (else opening_balance) |
| last_activity_at | timestamp | N |  |  |  | Derived: timestamp of latest transaction |

## account_parties

Bridge table: N:M customers <-> accounts (owner / joint owner).

- **Primary key:** `account_id, customer_id`
- **Foreign keys:** `account_id` -> `accounts.account_id`, `customer_id` -> `customers.customer_id`
- **Cardinality:** N:M

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| account_id | string | Y |  |  |  |  |
| customer_id | string | Y |  |  |  |  |
| party_role | string | Y | OWNER, JOINT_OWNER |  |  |  |
| effective_from | date | Y |  |  |  |  |
| effective_to | date | N |  |  |  |  |

## account_status_events

Append-only history of account lifecycle state changes (never overwritten).

- **Primary key:** `event_id`
- **Foreign keys:** `account_id` -> `accounts.account_id`, `action_id` -> `analyst_actions.action_id`
- **Cardinality:** N:1 accounts; N:1 analyst_actions (optional)

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| event_id | string | Y | `^ASE-\d{7}$` |  | Y |  |
| account_id | string | Y |  |  |  |  |
| event_ts | timestamp | Y |  |  |  |  |
| from_status | string | N | ACTIVE, DORMANT, FROZEN, CLOSED |  |  | NULL for the opening event |
| to_status | string | Y | ACTIVE, DORMANT, FROZEN, CLOSED |  |  | (from,to) must be an allowed transition |
| reason_code | string | Y | ACCOUNT_OPENED, INACTIVITY_180D, ACTIVITY_RESUMED, CUSTOMER_REQUEST, ANALYST_FREEZE |  |  |  |
| source | string | Y | SYSTEM, ANALYST_ACTION |  |  |  |
| action_id | string | N |  |  |  | Set when source = ANALYST_ACTION |

## merchants

Merchant master.

- **Primary key:** `merchant_id`
- **Foreign keys:** none
- **Cardinality:** 1:N transactions (optional on transaction)

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| merchant_id | string | Y | `^MRC-\d{6}$` |  | Y |  |
| merchant_name | string | Y |  |  |  |  |
| mcc | integer | Y |  |  |  | Merchant category code |
| category | string | Y |  |  |  |  |
| risk_level | string | Y | LOW, MEDIUM, HIGH |  |  |  |
| country_code | string | Y | IN |  |  |  |
| status | string | Y | ACTIVE, INACTIVE |  |  |  |
| onboarded_date | date | Y |  |  |  |  |

## devices

Device master (synthetic fingerprints).

- **Primary key:** `device_id`
- **Foreign keys:** none
- **Cardinality:** N:M accounts via device_account_links

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| device_id | string | Y | `^DEV-\d{6}$` |  | Y |  |
| device_type | string | Y | MOBILE, TABLET, DESKTOP |  |  |  |
| os_family | string | Y | ANDROID, IOS, WINDOWS, MACOS |  |  |  |
| fingerprint_hash | string | Y |  |  | Y | Synthetic SHA-1 prefix, no real device data |
| first_seen_at | timestamp | Y |  |  |  | Derived: earliest device_account_links.first_seen_at |
| status | string | Y | ACTIVE, BLOCKED |  |  |  |

## device_account_links

Bridge table: N:M devices <-> accounts, with first/last use.

- **Primary key:** `device_id, account_id`
- **Foreign keys:** `device_id` -> `devices.device_id`, `account_id` -> `accounts.account_id`
- **Cardinality:** N:M

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| device_id | string | Y |  |  |  |  |
| account_id | string | Y |  |  |  |  |
| first_seen_at | timestamp | Y |  |  |  | <= every transaction using this device on this account |
| last_seen_at | timestamp | N |  |  |  | Derived: latest transaction using the pair |

## transactions

Timestamped debit/credit ledger entries; balance_after is a running balance per account.

- **Primary key:** `transaction_id`
- **Foreign keys:** `account_id` -> `accounts.account_id`, `merchant_id` -> `merchants.merchant_id`, `device_id` -> `devices.device_id`, `transfer_id` -> `transfer_links.transfer_id`
- **Cardinality:** N:1 accounts; N:1 merchants (opt); N:1 devices (opt); N:1 transfer_links (opt, 2 legs)
- **Lifecycle / effective dating:** Append-only; no transaction after account close or freeze.

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| transaction_id | string | Y | `^TXN-2026-\d{8}$` |  | Y |  |
| account_id | string | Y |  |  |  |  |
| txn_ts | timestamp | Y |  |  |  |  |
| txn_type | string | Y | SALARY_CREDIT, INTEREST_CREDIT, CASH_DEPOSIT, TRANSFER_IN, POS_PURCHASE, ONLINE_PURCHASE, ATM_WITHDRAWAL, TRANSFER_OUT |  |  |  |
| direction | string | Y | DEBIT, CREDIT |  |  | must match txn_type |
| amount | number | Y |  | INR |  | > 0 |
| currency | string | Y | INR |  |  |  |
| balance_after | number | Y |  | INR |  | = previous balance_after (or opening_balance) +/- amount |
| channel | string | Y | BRANCH, ATM, POS, MOBILE_APP, WEB, SYSTEM |  |  |  |
| merchant_id | string | N |  |  |  | Set for purchases |
| device_id | string | N |  |  |  | Must be linked to the account via device_account_links |
| transfer_id | string | N |  |  |  | Set for TRANSFER_OUT/TRANSFER_IN legs |
| description | string | Y |  |  |  |  |

## transfer_links

Links the debit leg and credit leg of an internal transfer.

- **Primary key:** `transfer_id`
- **Foreign keys:** `source_account_id` -> `accounts.account_id`, `dest_account_id` -> `accounts.account_id`, `out_txn_id` -> `transactions.transaction_id`, `in_txn_id` -> `transactions.transaction_id`
- **Cardinality:** 1:1 with each of its two transactions

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| transfer_id | string | Y | `^TRF-2026-\d{6}$` |  | Y |  |
| source_account_id | string | Y |  |  |  |  |
| dest_account_id | string | Y |  |  |  | <> source_account_id |
| out_txn_id | string | Y |  |  | Y |  |
| in_txn_id | string | Y |  |  | Y |  |
| amount | number | Y |  | INR |  | = out leg amount = in leg amount |
| initiated_at | timestamp | Y |  |  |  |  |
| completed_at | timestamp | Y |  |  |  | >= initiated_at |
| status | string | Y | COMPLETED |  |  |  |
| purpose | string | Y | FAMILY_SUPPORT, RENT, PURCHASE, INVESTMENT, OTHER |  |  |  |

## analyst_users

Internal users: ops analysts, risk/compliance reviewers, system administrators.

- **Primary key:** `user_id`
- **Foreign keys:** none
- **Cardinality:** 1:N analyst_actions; 1:N risk_rules (created_by)

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| user_id | string | Y | `^USR-\d{3}$` |  | Y |  |
| full_name | string | Y |  |  |  |  |
| role | string | Y | OPS_ANALYST, RISK_REVIEWER, SYS_ADMIN |  |  |  |
| team | string | Y |  |  |  |  |
| active_from | date | Y |  |  |  |  |
| status | string | Y | ACTIVE, INACTIVE |  |  |  |

## risk_rules

Versioned, transparent rule catalogue. One row per rule version (effective-dated).

- **Primary key:** `rule_version_id`
- **Foreign keys:** `created_by` -> `analyst_users.user_id`
- **Cardinality:** 1:N alerts
- **Lifecycle / effective dating:** Version N retired when version N+1 becomes effective.

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| rule_version_id | string | Y | `^RULE-\d{3}-v\d+$` |  | Y |  |
| rule_id | string | Y | `^RULE-\d{3}$` |  |  |  |
| rule_name | string | Y |  |  |  |  |
| version | integer | Y |  |  |  | >= 1 |
| description | string | Y |  |  |  |  |
| base_weight | number | Y |  | points |  | Starting score (RULE-003 base depends on cycle length, see params) |
| params | json | Y |  |  |  | Thresholds used by the engine |
| effective_from | timestamp | Y |  |  |  |  |
| effective_to | timestamp | N |  |  |  | Exclusive; NULL = current |
| created_by | string | Y |  |  |  | SYS_ADMIN user |
| status | string | Y | ACTIVE, RETIRED |  |  |  |

## alerts

A rule hit with its triggering fact, explainable score and case link.

- **Primary key:** `alert_id`
- **Foreign keys:** `rule_version_id` -> `risk_rules.rule_version_id`, `account_id` -> `accounts.account_id`, `trigger_txn_id` -> `transactions.transaction_id`, `case_id` -> `cases.case_id`
- **Cardinality:** N:1 cases; N:1 risk_rules; N:M transactions via alert_transactions
- **Lifecycle / effective dating:** Immutable once raised; disposition lives on the case.

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| alert_id | string | Y | `^ALT-2026-\d{6}$` |  | Y |  |
| rule_version_id | string | Y |  |  |  |  |
| account_id | string | Y |  |  |  |  |
| trigger_txn_id | string | Y |  |  |  | The transaction that completed the rule condition; required; transaction must belong to account_id and precede alert_ts |
| alert_ts | timestamp | Y |  |  |  | >= trigger transaction timestamp |
| base_score | number | Y |  | points |  |  |
| adjustment_total | number | Y |  | points |  | = sum of score_components points |
| risk_score | number | Y |  | points |  | = clip(base_score + adjustment_total, 0, 100) |
| priority_band | string | Y | P1, P2, P3 |  |  | P1 >= 70, P2 40-69, P3 < 40 |
| group_key | string | N |  |  |  | Shared by alerts from the same detected structure (e.g. a transfer cycle) |
| explanation | string | Y |  |  |  | Human-readable reason |
| evidence | json | Y |  |  |  | Facts and thresholds that fired the rule |
| score_components | json | Y |  |  |  | [{code, points}] adjustments applied to base score |
| case_id | string | Y |  |  |  |  |

## alert_transactions

Bridge table: N:M alerts <-> transactions (trigger + supporting evidence).

- **Primary key:** `alert_id, transaction_id`
- **Foreign keys:** `alert_id` -> `alerts.alert_id`, `transaction_id` -> `transactions.transaction_id`
- **Cardinality:** N:M

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| alert_id | string | Y |  |  |  |  |
| transaction_id | string | Y |  |  |  |  |
| evidence_role | string | Y | TRIGGER, EVIDENCE |  |  |  |

## cases

Groups related alerts for analyst review; state is derived from analyst_actions.

- **Primary key:** `case_id`
- **Foreign keys:** `primary_account_id` -> `accounts.account_id`, `primary_customer_id` -> `customers.customer_id`, `assigned_user_id` -> `analyst_users.user_id`
- **Cardinality:** 1:N alerts; 1:N analyst_actions
- **Lifecycle / effective dating:** OPEN -> IN_REVIEW -> [ESCALATED] -> CLOSED.

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| case_id | string | Y | `^CASE-2026-\d{6}$` |  | Y |  |
| opened_at | timestamp | Y |  |  |  | Earliest alert_ts + 5 minutes |
| closed_at | timestamp | N |  |  |  | Timestamp of CLOSE_CASE action |
| status | string | Y | OPEN, IN_REVIEW, ESCALATED, CLOSED |  |  | Derived: to_status of the latest analyst action (OPEN if none) |
| priority_band | string | Y | P1, P2, P3 |  |  |  |
| case_score | number | Y |  | points |  | min(100, max alert score + 5 per additional distinct rule) |
| alert_count | integer | Y |  |  |  | = number of alerts with this case_id |
| primary_account_id | string | Y |  |  |  |  |
| primary_customer_id | string | Y |  |  |  |  |
| assigned_user_id | string | N |  |  |  |  |
| sla_due_at | timestamp | Y |  |  |  | opened_at + 24h (P1) / 72h (P2) / 168h (P3) |
| disposition | string | N | CONFIRMED_SUSPICIOUS, FALSE_POSITIVE |  |  | Set only when status = CLOSED |

## analyst_actions

Append-only analyst/reviewer event log; every case state change is one row.

- **Primary key:** `action_id`
- **Foreign keys:** `case_id` -> `cases.case_id`, `user_id` -> `analyst_users.user_id`, `account_id` -> `accounts.account_id`
- **Cardinality:** N:1 cases; N:1 analyst_users

| Field | Type | Req | Allowed values / pattern | Unit | Unique | Validation rule / notes |
|---|---|---|---|---|---|---|
| action_id | string | Y | `^ACT-2026-\d{6}$` |  | Y |  |
| case_id | string | Y |  |  |  |  |
| user_id | string | Y |  |  |  |  |
| action_ts | timestamp | Y |  |  |  | >= case.opened_at |
| action_type | string | Y | ASSIGN, REQUEST_INFO, ESCALATE, FREEZE_ACCOUNT, CLOSE_CASE |  |  |  |
| from_status | string | Y | OPEN, IN_REVIEW, ESCALATED, CLOSED |  |  |  |
| to_status | string | Y | OPEN, IN_REVIEW, ESCALATED, CLOSED |  |  | (action_type, from, to) must be an allowed transition |
| disposition | string | N | CONFIRMED_SUSPICIOUS, FALSE_POSITIVE |  |  | Required on CLOSE_CASE |
| account_id | string | N |  |  |  | Target account for FREEZE_ACCOUNT |
| notes | string | Y |  |  |  | Rationale referencing rules/scores |
