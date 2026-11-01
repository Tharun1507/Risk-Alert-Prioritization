# Evaluation report

## Volumes

| Table | Rows |
|---|---|
| customers | 5,000 |
| kyc_profiles | 5,000 |
| accounts | 7,000 |
| account_parties | 7,610 |
| account_status_events | 7,733 |
| merchants | 400 |
| devices | 6,843 |
| device_account_links | 9,679 |
| transactions | 100,000 |
| transfer_links | 3,188 |
| analyst_users | 27 |
| risk_rules | 5 |
| alerts | 1,000 |
| alert_transactions | 3,325 |
| cases | 820 |
| analyst_actions | 2,339 |

## Detection vs ground truth

- Suspicious planted scenarios detected: **518/518** (recall 1.0)
- Unexpected alerts (no matching planted scenario): **0**
- Boundary no-alert scenarios wrongly alerted: **0**
- Alerts without trigger (injected anomaly): 6

| Scenario type | Label | Scenarios | Expected alerts | Found | Recall |
|---|---|---|---|---|---|
| dormant_reactivation | SUSPICIOUS | 89 | 89 | 89 | 1.0 |
| dormant_reactivation_benign | BENIGN_FALSE_POSITIVE | 50 | 50 | 50 | 1.0 |
| boundary_dormant_gap_lt_180 | BOUNDARY_NO_ALERT | 30 | 0 | 0 | None |
| velocity_spike | SUSPICIOUS | 189 | 189 | 189 | 1.0 |
| velocity_benign | BENIGN_FALSE_POSITIVE | 60 | 60 | 60 | 1.0 |
| new_device_high_value | SUSPICIOUS | 199 | 199 | 199 | 1.0 |
| circular_transfer | SUSPICIOUS | 41 | 137 | 137 | 1.0 |
| reciprocal_transfer_benign | BENIGN_FALSE_POSITIVE | 25 | 50 | 50 | 1.0 |
| boundary_new_device_below_threshold | BOUNDARY_NO_ALERT | 30 | 0 | 0 | None |
| boundary_velocity_below_count | BOUNDARY_NO_ALERT | 30 | 0 | 0 | None |
| new_device_benign | BENIGN_FALSE_POSITIVE | 220 | 220 | 220 | 1.0 |

## Ranking quality (explainable risk score vs naive orderings)

Labelled alerts: 994 (614 suspicious / 380 benign false positives; base rate 0.6177).

| Metric | Risk score | Amount only | FIFO (time) |
|---|---|---|---|
| AUC | 0.9999 | 0.722 | 0.5044 |
| Precision@50 | 1.0 | 1.0 | 0.76 |
| Precision@100 | 1.0 | 1.0 | 0.78 |
| Precision in top 20% of alerts | 1.0 | 1.0 | 0.6465 |
| False positives sent to low-priority band P3 | 0.9974 | - | - |

Mean score - suspicious 60.27, benign false positive 22.47.

| Band | Suspicious | Benign FP |
|---|---|---|
| P1 | 139 | 0 |
| P2 | 475 | 1 |
| P3 | 0 | 379 |

## Cases and analyst workflow

- Cases: 820 (multi-alert cases: 106, largest: 6 alerts)
- Status: {'CLOSED': 806, 'IN_REVIEW': 10, 'ESCALATED': 4}
- Priority: {'P2': 378, 'P1': 86, 'P3': 356}
- Dispositions: {'CONFIRMED_SUSPICIOUS': 398, 'FALSE_POSITIVE': 408}
- False-positive rate among closed cases: 0.5062
- SLA breach rate: 0.2976
- Mean hours to close by band: {'P1': 163.6, 'P2': 112.4, 'P3': 110.4}
- Analyst disposition vs ground truth: {'closed_cases_with_label': 800, 'agree': 743, 'accuracy': 0.9287, 'confusion': {'expected=CONFIRMED_SUSPICIOUS|analyst=CONFIRMED_SUSPICIOUS': 398, 'expected=CONFIRMED_SUSPICIOUS|analyst=FALSE_POSITIVE': 57, 'expected=FALSE_POSITIVE|analyst=FALSE_POSITIVE': 345}}
