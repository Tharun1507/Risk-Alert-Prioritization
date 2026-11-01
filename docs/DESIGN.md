# Design notes

## Pipeline
`datagen.World` (master data -> background behaviour -> labelled scenarios -> chronological ledger simulation)
-> `rules.evaluate` (independent engine, reads inputs only) -> `casework.run` (cases, analyst actions, derived
account/customer state, labelled alert/case anomalies) -> `build` (export, validate, evaluate, manifest).

## Why alerts are not "randomly assigned"
Background activity (salary, spends <= INR 19,500, transfers <= INR 15,000, monthly/quarterly credits that keep every
account active) is built to stay below every rule threshold. Alerts arise only where a *planted, labelled scenario*
crosses a documented threshold, and are produced by `rules.py`, which never sees ground truth.
Scenario counts are calibrated (same seed, deterministic loop) so total alerts equal exactly the requested number.

## Rules (see `risk_rules` table; versioned and effective-dated)
| Rule | Fires when | Base |
|---|---|---|
| RULE-001 VELOCITY_SPIKE | >= 6 debits on one account within 60 min | 40 |
| RULE-002 NEW_DEVICE_HIGH_VALUE | debit >= 25,000 (v1) / 20,000 (v2 from 2026-07-01) within 24h of device first link | 35 |
| RULE-003 CIRCULAR_TRANSFER | 2-4 transfer loop, legs >= 20,000 and within 15% of first leg, within 72h | 35/55/65 by length |
| RULE-004 DORMANT_REACTIVATION | first txn after >= 180 days idle is a debit >= 15,000 | 45 |

## Explainable score
`risk_score = clip(base + sum(adjustments), 0, 100)`. Adjustments are stored per alert in `score_components`:
amount >= 50k/+5, >= 100k/+10; HIGH/MEDIUM-risk merchant +10/+5; burst >= 8 txns +10; burst total >= 30k +5;
device shared across accounts +10; KYC tier HIGH/MEDIUM +10/+5; KYC not verified +10; customer < 180 days +5;
**mitigation -20** for established (>= 730 days), KYC-verified customers whose history is consistent with the event
(rule-specific). Bands: P1 >= 70, P2 40-69, P3 < 40.

## Case grouping and disposition
Alerts join one case when they share a customer (<= 14 days), a device (<= 30 days) or a detected structure
(`group_key`, e.g. a transfer ring). Dispositions follow a deterministic policy (see `casework.py` docstring);
randomness only affects timing/assignment. Analysts can disagree with ground truth (e.g. weak true positives
that land in the mid band and are closed as false positive after an info request) - measured in the evaluation.

## Injected edge cases (labelled in `ground_truth/injected_anomalies.csv`)
| issue_type | What is injected | Check that must catch it |
|---|---|---|
| TXN_REFERENCES_CLOSED_ACCOUNT | purchase posted 2-20 days after account close | txn_not_after_account_close |
| NEGATIVE_BALANCE_NO_OVERDRAFT | debit sized from running balance on an account with 0 overdraft | negative_balance_without_overdraft |
| TRANSFER_LEGS_DO_NOT_RECONCILE | credit leg inflated 8% vs debit leg | transfer_legs_reconcile |
| ALERT_HAS_NO_TRIGGERING_TRANSACTION | alert with NULL trigger and no evidence rows | alert_has_trigger_txn |
| CASE_DISPOSITION_PRECEDES_ALERT | a closed case's whole action chain shifted before its first alert | 3 case-timing checks |

Validation passes only when observed violations == labelled set (no silent corruption, no missed injection).

## Known limitations
- Planted scenarios are cleanly separable (AUC ~1.0); this demonstrates traceability and detectability, not real-world
  model difficulty. Analyst-vs-truth disagreement (~7%) and mid-band cases add some realism.
- Volume is compressed (~20 txns/customer over 9.5 months), so balances grow faster than real-life.
- Single currency (INR), no reversals/chargebacks, naive local timestamps.
