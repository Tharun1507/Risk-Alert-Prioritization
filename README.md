# Risk Alert Prioritization – synthetic data generator, transparent rules & case workflow

Prioritizes transactions and linked accounts for analyst review using **transparent, versioned rules** and
**synthetic risk indicators**. One seeded generator produces a fully related dataset
(Customer → KYC → Account → Device/Merchant → Transaction → TransferLink → Alert → Case → AnalystAction),
runs an independent rule engine over it, groups alerts into cases, records analyst dispositions, injects
**labelled** edge cases, validates everything and reports measurable outcomes.

100 % synthetic. No external data, no real personal data (see `manifest.json` → `provenance`).
Python 3.10+, standard library only (pytest for tests).

## Quick start
```bash
git clone <this-repo> && cd risk-alert-prioritization
pip install pytest                      # tests only
export PYTHONPATH=src                   # (Windows PowerShell: $env:PYTHONPATH="src")

# starter dataset (250 customers / 350 accounts / 5,000 txns / 50 alerts) – <1 s
python -m alertgen.build --preset starter --out data/starter

# full dataset (5,000 / 7,000 / 100,000 / 1,000) – ~15 s
python -m alertgen.build --seed 20261022 --scale 1.0 --out data/full --formats csv,json,sql

python -m pytest -q tests               # 13 tests
python scripts/verify_reproducibility.py   # regenerates twice, compares row counts + SHA-256
```
`--scale` parameterizes every volume, so starter and full use the identical generator. Default seed `20261022`.
The command exits non-zero if any validation check fails.

## What you get (`data/<name>/`)
| Path | Content |
|---|---|
| `csv/`, `json/`, `sql/schema.sql` + `sql/seed.sql` | 16 tables (core 10 + `kyc_profiles`, `account_parties`, `account_status_events`, `device_account_links`, `alert_transactions`, `analyst_users`) |
| `ground_truth/` | `ground_truth.json`, `scenarios.csv`, `scenario_entities.csv`, `injected_anomalies.csv` – **never** referenced by input tables or the rule engine |
| `data_dictionary.md` | Field-level: type, required, allowed values, unit, uniqueness, validation rule; PKs, FKs, cardinality, lifecycle |
| `validation_report.json` | 129 checks (PK, required, enums, patterns, FKs, dates, reconciliation, lifecycle, duplicates) + SQLite load / `PRAGMA foreign_key_check` |
| `evaluation_metrics.json` / `evaluation_report.md` | Detection, ranking and case KPIs vs ground truth |
| `manifest.json` | Seed, command, config, calibration, provenance, per-file SHA-256 and row counts |

The committed `data/full` contains CSV + SQL (JSON is ~44 MB; regenerate with `make full`).
`docs/reproducibility_report.json` holds the two-run comparison.

## IDs
`CUS-000001`, `ACC-CUS-000001-01`, `TXN-2026-00000001`, `ALT-2026-000001`, `CASE-2026-000001`
(plus `TRF-2026-000001`, `ACT-2026-000001`, `MRC-/DEV-/KYC-/ASE-/USR-`). Zero-padded, immutable, deterministic.

## How the requirements are met
- **Master first, then children**: customers/KYC/accounts/devices/merchants → events → transactions → transfers → alerts → cases → actions; every FK validated (CSV round-trip and in SQLite).
- **Balances reconcile**: chronological ledger simulation; infeasible debits are dropped (transfers atomically); `balance_after` is a running balance; `accounts.current_balance`, statuses and customer status are *derived*.
- **No history overwritten**: `account_status_events` and `analyst_actions` are append-only; case/account state is derived from them; rules are effective-dated versions (RULE-002 v1→v2 on 2026-07-01).
- **Outcomes from documented rules, not random**: alerts come from the independent engine in `rules.py`; dispositions from a deterministic policy (`casework.py` docstring); `docs/DESIGN.md` lists thresholds and score components.
- **Coverage**: normal behaviour, velocity spikes, new-device activity, circular transfers (rings of 3–4), dormant reactivation, benign **false positives** for each pattern, and **boundary no-alert** cases (24,999 vs 25,000; 5 vs 6 debits; 150–175 vs 180 idle days).
- **Edge cases (labelled, not silent)**: transaction on closed account; negative balance without overdraft; transfer legs that don't reconcile; alert without triggering transaction; case disposition before alert. Validation passes only if observed violations equal the labelled set exactly.
- **Explainable ranking**: each alert stores `base_score`, named `score_components`, `evidence` JSON, human-readable `explanation`, trigger + supporting transactions (`alert_transactions`).
- **Reproducible**: per-stage seeded RNG streams, no wall-clock or hash-order dependence (verified across `PYTHONHASHSEED`).

## Headline results (full dataset, seed 20261022)
| Metric | Value |
|---|---|
| Volumes | 5,000 customers · 7,000 accounts · 100,000 transactions · 1,000 alerts · 820 cases |
| Validation | 129/129 checks pass; 42 labelled violations detected (6 per injected type, 7 checks) |
| Suspicious planted scenarios detected | 518 / 518 (recall 1.00); 0 unexpected alerts; 0 alerts on boundary no-alert scenarios |
| Ranking AUC (suspicious vs benign false positive) | risk score **0.9999** vs amount-only 0.722 vs FIFO 0.504 |
| Precision in top 20 % of alerts | risk score 1.00 · amount-only 1.00 · FIFO 0.65 |
| Benign false positives routed to low-priority band P3 | 99.7 % |
| Cases | 806 closed, 10 in review, 4 escalated; SLA breach rate 0.30; analyst vs ground-truth agreement 0.93 |

Caveat: planted scenarios are cleanly separable, so AUC ≈ 1 demonstrates traceable, detectable patterns – not real-world
difficulty. See "Known limitations" in `docs/DESIGN.md`.

## Layout
```
src/alertgen/  schema.py (data dictionary→DDL→validator) · datagen.py · rules.py · casework.py
               validate.py · evaluate.py · build.py (CLI)
tests/         13 pytest tests (volumes, IDs, validation vs labelled anomalies, leakage, reproducibility, ...)
scripts/       verify_reproducibility.py
docs/          DESIGN.md · data_dictionary.md · reproducibility_report.json
data/          starter/ (all formats) · full/ (csv + sql)
```

## Lawful use / provenance
Synthetic only. Public references (FFIEC BSA/AML manual, FATF recommendations) were used for typology/schema
inspiration only; no data was retrieved. Names are random first/last-name combinations and do not identify anyone.
MIT licensed.
