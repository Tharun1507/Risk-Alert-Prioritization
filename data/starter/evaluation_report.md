# Evaluation report

## Volumes

| Table | Rows |
|---|---|
| customers | 250 |
| kyc_profiles | 250 |
| accounts | 350 |
| account_parties | 383 |
| account_status_events | 382 |
| merchants | 40 |
| devices | 350 |
| device_account_links | 488 |
| transactions | 5,000 |
| transfer_links | 159 |
| analyst_users | 8 |
| risk_rules | 5 |
| alerts | 50 |
| alert_transactions | 150 |
| cases | 41 |
| analyst_actions | 114 |

## Detection vs ground truth

- Suspicious planted scenarios detected: **25/25** (recall 1.0)
- Unexpected alerts (no matching planted scenario): **0**
- Boundary no-alert scenarios wrongly alerted: **0**
- Alerts without trigger (injected anomaly): 2

| Scenario type | Label | Scenarios | Expected alerts | Found | Recall |
|---|---|---|---|---|---|
| dormant_reactivation | SUSPICIOUS | 4 | 4 | 4 | 1.0 |
| dormant_reactivation_benign | BENIGN_FALSE_POSITIVE | 2 | 2 | 2 | 1.0 |
| boundary_dormant_gap_lt_180 | BOUNDARY_NO_ALERT | 1 | 0 | 0 | None |
| velocity_spike | SUSPICIOUS | 9 | 9 | 9 | 1.0 |
| velocity_benign | BENIGN_FALSE_POSITIVE | 3 | 3 | 3 | 1.0 |
| new_device_high_value | SUSPICIOUS | 10 | 10 | 10 | 1.0 |
| circular_transfer | SUSPICIOUS | 2 | 6 | 6 | 1.0 |
| reciprocal_transfer_benign | BENIGN_FALSE_POSITIVE | 1 | 2 | 2 | 1.0 |
| boundary_new_device_below_threshold | BOUNDARY_NO_ALERT | 1 | 0 | 0 | None |
| boundary_velocity_below_count | BOUNDARY_NO_ALERT | 1 | 0 | 0 | None |
| new_device_benign | BENIGN_FALSE_POSITIVE | 12 | 12 | 12 | 1.0 |

## Ranking quality (explainable risk score vs naive orderings)

Labelled alerts: 48 (29 suspicious / 19 benign false positives; base rate 0.6042).

| Metric | Risk score | Amount only | FIFO (time) |
|---|---|---|---|
| AUC | 1.0 | 0.7205 | 0.6334 |
| Precision@50 | 0.6042 | 0.6042 | 0.6042 |
| Precision@100 | 0.6042 | 0.6042 | 0.6042 |
| Precision in top 20% of alerts | 1.0 | 1.0 | 0.6667 |
| False positives sent to low-priority band P3 | 1.0 | - | - |

Mean score - suspicious 60.34, benign false positive 23.16.

| Band | Suspicious | Benign FP |
|---|---|---|
| P1 | 4 | 0 |
| P2 | 25 | 0 |
| P3 | 0 | 19 |

## Cases and analyst workflow

- Cases: 41 (multi-alert cases: 7, largest: 3 alerts)
- Status: {'CLOSED': 40, 'IN_REVIEW': 1}
- Priority: {'P3': 18, 'P2': 19, 'P1': 4}
- Dispositions: {'FALSE_POSITIVE': 21, 'CONFIRMED_SUSPICIOUS': 19}
- False-positive rate among closed cases: 0.525
- SLA breach rate: 0.3659
- Mean hours to close by band: {'P1': 76.5, 'P2': 78.9, 'P3': 123.7}
- Analyst disposition vs ground truth: {'closed_cases_with_label': 38, 'agree': 36, 'accuracy': 0.9474, 'confusion': {'expected=CONFIRMED_SUSPICIOUS|analyst=CONFIRMED_SUSPICIOUS': 19, 'expected=CONFIRMED_SUSPICIOUS|analyst=FALSE_POSITIVE': 2, 'expected=FALSE_POSITIVE|analyst=FALSE_POSITIVE': 17}}
