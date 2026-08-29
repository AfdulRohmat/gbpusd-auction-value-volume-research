# Technical Plan — GBPUSD Value-State Study Phase 2

**Status:** Approved for implementation on `phase/02-value-state`
**Source PRD:** `llm/PRD_GBPUSD_Session_Value_Fundamental_Research.md`
**Development sample:** `[2024-01-01, 2025-01-01)`
**Phase-1 dependency:** `llm/PHASE1_RESULTS_2023_2024.md`

## 1. Objective and boundary

Phase 2 asks whether point-in-time VWAP and prior completed value-area state
explain the direction or type of the opening move beyond session timing alone.

This phase is a conditional event study. It does not create executable entries,
apply fundamental bias, simulate stops/targets, calculate P&L, or optimize
thresholds. Those layers remain blocked until this report is reviewed.

Phase-1 evidence shapes, but does not determine, the analysis:

- London is studied at every 5/15/30/60/90-minute horizon.
- New York is reported separately for the early 0–30-minute response and the
  30–90-minute expansion because its fixed-control effect appeared later.

## 2. Frozen value definitions

### 2.1 Activity and price

- Canonical price: tick mid, `(bid + ask) / 2`.
- Activity weight: one observed quote equals one unit.
- HistData `source_volume` remains unused because it is zero and is not traded
  volume.
- Every output labels VWAP and Volume Profile as tick-activity proxies for
  decentralized spot FX.

### 2.2 FX trading day

- Boundary: `17:00 America/New_York`, using the existing DST-aware definition.
- A profile labeled day `D` covers the FX day ending at 17:00 New York on `D`.
- At an event during day `D`, only the latest eligible profile with label `< D`
  may be joined. Monday therefore uses Friday, not a nonexistent weekend day.
- A daily profile needs at least 95% of the expected 288 M5 timestamps.

### 2.3 VWAP proxy

M5 aggregation stores exact tick-level sufficient statistics:

```text
mid_activity_sum = sum(mid * activity)
mid_squared_activity_sum = sum(mid^2 * activity)
activity_count = sum(activity)
```

FX-day VWAP is the cumulative ratio of the first and third quantities. At event
time `t`, the last usable M5 bar must have `bar_start + 5 minutes <= t`.

Store at each event:

- VWAP and availability timestamp;
- open distance to VWAP in pips;
- distance divided by cumulative weighted standard deviation;
- 30-minute VWAP slope in pips per 30 minutes; and
- `above_vwap`, `at_vwap`, or `below_vwap` using the frozen boundary buffer.

### 2.4 Previous-day tick-activity profile

- Price-bin width: 1 pip.
- Value-area target: 70% of observed tick activity.
- Tick mid is rounded to its nearest configured bin.
- POC is the highest-activity node. A tie selects the node closest to the
  activity-weighted mean, then the lower node.
- Starting at POC, expand contiguously toward the adjacent node with more
  activity; ties expand lower first. Stop after reaching at least 70%.
- `VAL` and `VAH` are the lowest and highest included bin centers.

The daily output records POC/VAH/VAL, value width, activity count, node count,
M5 coverage, source timestamps, and eligibility.

### 2.5 Event state

Frozen boundary buffer: 1 pip.

- `above_value`: open mid > VAH + buffer.
- `below_value`: open mid < VAL - buffer.
- `inside_value`: all other available-profile events.

Continuous distances to VAH, VAL, POC, and VWAP are retained. Discrete states
are for reporting, not information destruction.

### 2.6 Post-open transition labels

- `acceptance_above`: at least two consecutive completed M5 closes above
  `VAH + buffer`.
- `acceptance_below`: symmetric below `VAL - buffer`.
- `reentered_value`: an outside-value opening later closes across its nearest
  raw value boundary (VAH for above, VAL for below).
- `state_aligned_return`: return multiplied by +1 above value and -1 below
  value; it is undefined for inside-value openings.

Labels are generated for 15/30/60/90 minutes from half-open outcome windows.
They are future outcomes and must never enter event-time features.

## 3. Primary analyses

For London and New York separately:

1. compare 60-minute range for outside-value versus inside-value openings;
2. estimate state-aligned 30/60/90-minute return for outside-value openings;
3. measure re-entry and outside-acceptance probability;
4. compare distributions by VWAP state, distance, z-score, and slope; and
5. report joint value/VWAP state only when each displayed cell has at least 30
   eligible observations.

All horizon, session, and state counts are shown. Bootstrap confidence intervals
use the configured 10,000 resamples and fixed seed. Secondary contrasts are
exploratory.

## 4. Development gate

Phase 2 supports proceeding to fundamental-bias research when:

- value features are available for at least 95% of otherwise eligible opening
  events;
- source/profile/VWAP invariants pass;
- no accepted primary state cell contains fewer than 30 events;
- at least one registered state contrast is at least 2 pips with its 95%
  bootstrap confidence interval excluding zero; and
- the observed separation is not explained by an opening-spread discontinuity.

This is a development gate, not multi-year validation. A failed gate means
VWAP/VP has not shown enough incremental information to justify extra strategy
complexity on the current sample.

## 5. Implementation layout

```text
config/value_state.yaml
src/gbpusd_research/
├── features/
│   ├── vwap.py
│   └── volume_profile.py
└── research/
    ├── value_state.py
    └── phase2.py
tests/
├── test_vwap.py
├── test_volume_profile.py
├── test_value_state.py
└── test_phase2.py
```

Commands:

```bash
python -m gbpusd_research build-range \
  --research config/research_2024.yaml --force
python -m gbpusd_research run-phase2 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml
```

## 6. Report contract

```text
data/processed/reports/phase2/<run_id>/
├── run_manifest.json
├── data_quality.json
├── daily_profiles.parquet
├── value_events.parquet
├── event_exclusions.parquet
├── conditional_statistics.csv
├── continuous_associations.csv
├── statistical_comparisons.csv
├── figures/
│   ├── range_by_value_state.png
│   ├── continuation_by_horizon.png
│   ├── reentry_acceptance.png
│   └── vwap_distance_vs_return.png
└── report.md
```

Generated artifacts remain ignored by Git. The manifest stores configuration
snapshots/hashes, source hashes, Git commit, runtime versions, row counts, and
the development-gate decision.

## 7. Required tests

- exact tick-weighted M5 moments and cumulative VWAP;
- event-time VWAP uses only completed bars;
- FX trading-day mapping across New York DST;
- deterministic POC and value-area expansion including ties;
- Monday event joins Friday profile;
- missing/incomplete profile causes explicit exclusion;
- exact value-boundary and 1-pip-buffer behavior;
- two-close acceptance and re-entry interval boundaries;
- sentinel future changes cannot affect VWAP/profile/event-time state; and
- a synthetic end-to-end run produces every report artifact.

## 8. Delivery sequence

1. enrich and rebuild 2024 M5 sufficient statistics;
2. construct and validate daily tick-activity profiles;
3. attach point-in-time VWAP/profile state to opening events;
4. compute transition outcomes and conditional statistics;
5. generate the complete Phase-2 report;
6. review the development gate before any fundamental or trading-rule code.
