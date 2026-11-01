-- Generated from src/alertgen/schema.py
CREATE TABLE customers (
  customer_id TEXT NOT NULL,
  full_name TEXT NOT NULL,
  date_of_birth TEXT NOT NULL,
  city TEXT NOT NULL,
  country_code TEXT NOT NULL CHECK (country_code IN ('IN')),
  occupation_band TEXT NOT NULL CHECK (occupation_band IN ('SALARIED', 'SELF_EMPLOYED', 'STUDENT', 'RETIRED', 'BUSINESS_OWNER')),
  monthly_income REAL NOT NULL,
  customer_since TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CLOSED')),
  PRIMARY KEY (customer_id)
);

CREATE TABLE kyc_profiles (
  kyc_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  kyc_status TEXT NOT NULL CHECK (kyc_status IN ('VERIFIED', 'PENDING', 'EXPIRED')),
  risk_tier TEXT NOT NULL CHECK (risk_tier IN ('LOW', 'MEDIUM', 'HIGH')),
  verified_date TEXT,
  expiry_date TEXT,
  id_document_type TEXT NOT NULL CHECK (id_document_type IN ('PASSPORT', 'NATIONAL_ID', 'DRIVING_LICENCE')),
  pep_flag INTEGER NOT NULL CHECK (pep_flag IN (0, 1)),
  source_of_funds TEXT NOT NULL CHECK (source_of_funds IN ('SALARY', 'BUSINESS_INCOME', 'PENSION', 'SAVINGS', 'FAMILY_SUPPORT')),
  effective_from TEXT NOT NULL,
  PRIMARY KEY (kyc_id),
  UNIQUE (customer_id),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE accounts (
  account_id TEXT NOT NULL,
  primary_customer_id TEXT NOT NULL,
  account_type TEXT NOT NULL CHECK (account_type IN ('CHECKING', 'SAVINGS')),
  currency TEXT NOT NULL CHECK (currency IN ('INR')),
  open_date TEXT NOT NULL,
  close_date TEXT,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DORMANT', 'FROZEN', 'CLOSED')),
  opening_balance REAL NOT NULL,
  overdraft_limit REAL NOT NULL,
  current_balance REAL NOT NULL,
  last_activity_at TEXT,
  PRIMARY KEY (account_id),
  FOREIGN KEY (primary_customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE account_parties (
  account_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  party_role TEXT NOT NULL CHECK (party_role IN ('OWNER', 'JOINT_OWNER')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  PRIMARY KEY (account_id, customer_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE account_status_events (
  event_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  event_ts TEXT NOT NULL,
  from_status TEXT CHECK (from_status IN ('ACTIVE', 'DORMANT', 'FROZEN', 'CLOSED')),
  to_status TEXT NOT NULL CHECK (to_status IN ('ACTIVE', 'DORMANT', 'FROZEN', 'CLOSED')),
  reason_code TEXT NOT NULL CHECK (reason_code IN ('ACCOUNT_OPENED', 'INACTIVITY_180D', 'ACTIVITY_RESUMED', 'CUSTOMER_REQUEST', 'ANALYST_FREEZE')),
  source TEXT NOT NULL CHECK (source IN ('SYSTEM', 'ANALYST_ACTION')),
  action_id TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (action_id) REFERENCES analyst_actions(action_id)
);

CREATE TABLE merchants (
  merchant_id TEXT NOT NULL,
  merchant_name TEXT NOT NULL,
  mcc INTEGER NOT NULL,
  category TEXT NOT NULL,
  risk_level TEXT NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
  country_code TEXT NOT NULL CHECK (country_code IN ('IN')),
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  onboarded_date TEXT NOT NULL,
  PRIMARY KEY (merchant_id)
);

CREATE TABLE devices (
  device_id TEXT NOT NULL,
  device_type TEXT NOT NULL CHECK (device_type IN ('MOBILE', 'TABLET', 'DESKTOP')),
  os_family TEXT NOT NULL CHECK (os_family IN ('ANDROID', 'IOS', 'WINDOWS', 'MACOS')),
  fingerprint_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'BLOCKED')),
  PRIMARY KEY (device_id),
  UNIQUE (fingerprint_hash)
);

CREATE TABLE device_account_links (
  device_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT,
  PRIMARY KEY (device_id, account_id),
  FOREIGN KEY (device_id) REFERENCES devices(device_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE transactions (
  transaction_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  txn_ts TEXT NOT NULL,
  txn_type TEXT NOT NULL CHECK (txn_type IN ('SALARY_CREDIT', 'INTEREST_CREDIT', 'CASH_DEPOSIT', 'TRANSFER_IN', 'POS_PURCHASE', 'ONLINE_PURCHASE', 'ATM_WITHDRAWAL', 'TRANSFER_OUT')),
  direction TEXT NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
  amount REAL NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('INR')),
  balance_after REAL NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('BRANCH', 'ATM', 'POS', 'MOBILE_APP', 'WEB', 'SYSTEM')),
  merchant_id TEXT,
  device_id TEXT,
  transfer_id TEXT,
  description TEXT NOT NULL,
  PRIMARY KEY (transaction_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (merchant_id) REFERENCES merchants(merchant_id),
  FOREIGN KEY (device_id) REFERENCES devices(device_id),
  FOREIGN KEY (transfer_id) REFERENCES transfer_links(transfer_id)
);

CREATE TABLE transfer_links (
  transfer_id TEXT NOT NULL,
  source_account_id TEXT NOT NULL,
  dest_account_id TEXT NOT NULL,
  out_txn_id TEXT NOT NULL,
  in_txn_id TEXT NOT NULL,
  amount REAL NOT NULL,
  initiated_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETED')),
  purpose TEXT NOT NULL CHECK (purpose IN ('FAMILY_SUPPORT', 'RENT', 'PURCHASE', 'INVESTMENT', 'OTHER')),
  PRIMARY KEY (transfer_id),
  UNIQUE (out_txn_id),
  UNIQUE (in_txn_id),
  FOREIGN KEY (source_account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (dest_account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (out_txn_id) REFERENCES transactions(transaction_id),
  FOREIGN KEY (in_txn_id) REFERENCES transactions(transaction_id)
);

CREATE TABLE analyst_users (
  user_id TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('OPS_ANALYST', 'RISK_REVIEWER', 'SYS_ADMIN')),
  team TEXT NOT NULL,
  active_from TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  PRIMARY KEY (user_id)
);

CREATE TABLE risk_rules (
  rule_version_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  rule_name TEXT NOT NULL,
  version INTEGER NOT NULL,
  description TEXT NOT NULL,
  base_weight REAL NOT NULL,
  params TEXT NOT NULL,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  created_by TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
  PRIMARY KEY (rule_version_id),
  FOREIGN KEY (created_by) REFERENCES analyst_users(user_id)
);

CREATE TABLE alerts (
  alert_id TEXT NOT NULL,
  rule_version_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  trigger_txn_id TEXT,
  alert_ts TEXT NOT NULL,
  base_score REAL NOT NULL,
  adjustment_total REAL NOT NULL,
  risk_score REAL NOT NULL,
  priority_band TEXT NOT NULL CHECK (priority_band IN ('P1', 'P2', 'P3')),
  group_key TEXT,
  explanation TEXT NOT NULL,
  evidence TEXT NOT NULL,
  score_components TEXT NOT NULL,
  case_id TEXT NOT NULL,
  PRIMARY KEY (alert_id),
  FOREIGN KEY (rule_version_id) REFERENCES risk_rules(rule_version_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (trigger_txn_id) REFERENCES transactions(transaction_id),
  FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE alert_transactions (
  alert_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  evidence_role TEXT NOT NULL CHECK (evidence_role IN ('TRIGGER', 'EVIDENCE')),
  PRIMARY KEY (alert_id, transaction_id),
  FOREIGN KEY (alert_id) REFERENCES alerts(alert_id),
  FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);

CREATE TABLE cases (
  case_id TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'IN_REVIEW', 'ESCALATED', 'CLOSED')),
  priority_band TEXT NOT NULL CHECK (priority_band IN ('P1', 'P2', 'P3')),
  case_score REAL NOT NULL,
  alert_count INTEGER NOT NULL,
  primary_account_id TEXT NOT NULL,
  primary_customer_id TEXT NOT NULL,
  assigned_user_id TEXT,
  sla_due_at TEXT NOT NULL,
  disposition TEXT CHECK (disposition IN ('CONFIRMED_SUSPICIOUS', 'FALSE_POSITIVE')),
  PRIMARY KEY (case_id),
  FOREIGN KEY (primary_account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (primary_customer_id) REFERENCES customers(customer_id),
  FOREIGN KEY (assigned_user_id) REFERENCES analyst_users(user_id)
);

CREATE TABLE analyst_actions (
  action_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  action_ts TEXT NOT NULL,
  action_type TEXT NOT NULL CHECK (action_type IN ('ASSIGN', 'REQUEST_INFO', 'ESCALATE', 'FREEZE_ACCOUNT', 'CLOSE_CASE')),
  from_status TEXT NOT NULL CHECK (from_status IN ('OPEN', 'IN_REVIEW', 'ESCALATED', 'CLOSED')),
  to_status TEXT NOT NULL CHECK (to_status IN ('OPEN', 'IN_REVIEW', 'ESCALATED', 'CLOSED')),
  disposition TEXT CHECK (disposition IN ('CONFIRMED_SUSPICIOUS', 'FALSE_POSITIVE')),
  account_id TEXT,
  notes TEXT NOT NULL,
  PRIMARY KEY (action_id),
  FOREIGN KEY (case_id) REFERENCES cases(case_id),
  FOREIGN KEY (user_id) REFERENCES analyst_users(user_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);
