# Technical Plan — GBPUSD Opening-Auction Ablation Phase 5

**Status:** Frozen before inspecting new variant outcomes  
**Branch:** `phase/05-opening-auction-ablation`  
**Evidence sample:** 2024 and 2025, reported separately  
**Authority:** exploratory diagnosis only; no untouched validation remains

## 1. Objective

Phase 5 diagnoses where Phase 4 lost sample size and expectancy. It does not
retune Phase 4 and cannot reverse its failed validation. The study asks:

1. Does the outside-value fade exist from the executable session open?
2. Does selecting events that re-enter within 30 minutes improve that
   open-time population?
3. On the same signal events, does waiting for the confirmed re-entry improve
   or damage P&L relative to entering at the open?
4. Does requiring a favorable POC improve the confirmed cohort?
5. On the same favorable cohort, what is the marginal effect of the POC target?
6. On the same trades, what is the marginal effect of the Phase-4 excursion
   stop?

This is an ablation of observable rule components, not a search over thresholds.

## 2. Research governance

- Both 2024 and 2025 have already been inspected and are development evidence.
- Results are reported by calendar year and session; pooling cannot create a
  validation claim.
- No threshold, entry deadline, stop buffer, target, cost, or session rule may
  change after variant results are viewed.
- Phase 5 has no PASS gate and cannot authorize live trading.
- Any later strategy proposal requires a new registration and a new untouched
  forward period.

## 3. Shared data and execution contract

- Inputs are the registered Phase-2 value events and M5 bid/ask bars for 2024
  and 2025.
- Value levels remain the previous completed FX-day tick-activity profile.
- Only `value_eligible` events opening `above_value` or `below_value` receive a
  fade direction.
- Above value maps to short; below value maps to long.
- Entry and exit use executable bid/ask prices.
- Every entry and exit receives the Phase-4 0.1-pip adverse slippage.
- A complete, exact M5 window through minute 90 is required for every variant,
  keeping cohorts comparable.
- Target touches use long `bid_high` and short `ask_low`.
- Target gaps do not receive favorable price improvement.
- Timeout exits use the executable close of the bar ending at the registered
  horizon.
- Maximum one result per event and variant.

Variants without stops deliberately expose the raw directional/exit component.
They are diagnostic return experiments, not executable risk recommendations.

## 4. Frozen variant matrix

| Variant | Cohort | Entry | Exit |
| --- | --- | --- | --- |
| `open_timeout_30` | all outside | session bid/ask open | fixed +30m close |
| `open_timeout_60` | all outside | session bid/ask open | fixed +60m close |
| `open_timeout_90` | all outside | session bid/ask open | fixed +90m close |
| `open_boundary_90` | all outside | session bid/ask open | nearest VA boundary or +90m |
| `open_poc_90` | all outside | session bid/ask open | previous POC or +90m |
| `signal_cohort_open_timeout_90` | re-entry by 30m | session bid/ask open | fixed +90m close |
| `confirmed_timeout_all` | re-entry by 30m | next M5 open | fixed +90m close |
| `confirmed_timeout_favorable` | re-entry and favorable POC | next M5 open | fixed +90m close |
| `confirmed_poc_no_stop` | re-entry and favorable POC | next M5 open | POC or +90m |
| `phase4_full` | re-entry and favorable POC | next M5 open | excursion stop, POC, or +90m |

The re-entry definition and Phase-4 full execution are unchanged:

```text
short: first completed M5 close <= previous VAH
long:  first completed M5 close >= previous VAL
signal bars: session open through minute 25
entry: next exact M5 open, no later than minute 30
```

## 5. Attribution contrasts

The report must distinguish selection effects from execution effects:

| Contrast | Interpretation |
| --- | --- |
| all-outside open timeout vs signal-cohort open timeout | re-entry selection |
| signal-cohort open timeout vs confirmed timeout | confirmation delay, paired |
| confirmed all vs confirmed favorable | POC-favorable selection |
| favorable confirmed timeout vs POC/no-stop | POC target, paired |
| POC/no-stop vs Phase-4 full | excursion stop, paired |

Paired contrasts use only common event IDs and calculate the event-level P&L
difference before aggregation. A positive delta means the second component
improved net P&L.

## 6. Statistics

For every year, session, and variant report:

- eligible event and retained-result count;
- retention from scheduled and outside-value populations;
- long/short counts and active months;
- mean/median/net P&L pips and win rate;
- gross-profit/gross-loss profit factor;
- maximum drawdown and worst trade in pips;
- mean maximum favorable and adverse M5-bar excursion through the exit bar
  (inclusive, with no intrabar ordering claim); and
- deterministic 95% calendar-month cluster-bootstrap interval for mean P&L.

For every attribution contrast report common-event count, mean/median delta,
the share improved, and the month-cluster interval of mean delta.

Rows with fewer than 30 events per year/session are explicitly marked
`underpowered`. This is a descriptive safeguard, not a universal statistical
law.

## 7. Required invariants

- profile day is strictly earlier than event FX day;
- outside state and direction mapping are exact;
- all open entries use information available at the session timestamp;
- signal entries occur exactly one M5 bar after a completed qualifying close;
- no exit occurs after the registered horizon or minute 90;
- long entries use ask and exits use bid; short entries use bid and exits ask;
- P&L, slippage, target, timeout, MFE, and MAE arithmetic are exact;
- paired contrasts join the same event and sample only once; and
- a future-bar sentinel cannot change an earlier signal or entry.

## 8. Implementation and artifacts

```text
config/opening_ablation.yaml
src/gbpusd_research/
├── config.py
└── research/
    ├── opening_ablation.py
    └── phase5.py
tests/
├── test_opening_ablation.py
└── test_phase5.py
```

Command:

```bash
.venv/bin/python -m gbpusd_research run-phase5 \
  --research config/research_2024.yaml \
  --second-research config/research_2025.yaml \
  --value-state config/value_state.yaml \
  --opening-value config/opening_value_strategy.yaml \
  --ablation config/opening_ablation.yaml
```

Artifact contract:

```text
data/processed/reports/phase5/<run_id>/
├── run_manifest.json
├── data_quality.json
├── ablation_results.parquet
├── retention_funnel.csv
├── variant_statistics.csv
├── selection_effects.csv
├── paired_deltas.csv
├── monthly_statistics.csv
├── figures/
└── report.md
```
