# Technical Plan — GBPUSD Auction-State Taxonomy and Transition Study Phase 7

**Status:** Frozen before inspecting Phase-7 episode/transition outcomes
**Branch:** `phase/07-auction-state-taxonomy`
**Evidence:** continuous GBPUSD M5 data for 2024 and 2025
**Authority:** exploratory taxonomy only; no trading or validation decision

## 1. Objective

Phase 7 steps back from strategy construction and asks:

1. What observable M5 price geometry characterizes rotational balance and
   directional imbalance?
2. How long do confirmed states persist?
3. At what civil times and session phases do balance-to-imbalance transitions
   occur?
4. Which point-in-time antecedents are associated with those transitions?
5. Are the episode and transition properties stable between 2024 and 2025?

No entry, stop, target, P&L, or strategy gate is permitted in this phase.

## 2. Two independent axes

Phase 7 does not equate volatility with imbalance:

| Auction state | Quiet activity | Active activity |
| --- | --- | --- |
| Balance | compression | wide/two-sided chop |
| Imbalance | slow directional grind | impulsive expansion |

`auction_state` is defined only by directionality, rotation, overlap, and close
location. `activity_regime` is defined independently from range relative to its
point-in-time trailing baseline. Tick count may be reported later as a
descriptive covariate but cannot define the primary taxonomy.

## 3. Continuous data contract

- Input: existing HistData GBPUSD M5 mid/bid/ask bars.
- Scope: every available bar in `[2024-01-01, 2026-01-01)`.
- Calendar years are processed and reported separately.
- A timestamp gap other than exactly five minutes starts a new segment and
  resets rolling features, confirmation streaks, and activity baselines.
- The first complete 30-minute window in each segment becomes available only
  after its final M5 bar closes.
- State features at timestamp `t` use bars completed no later than `t`.

## 4. Rolling auction features

Each complete six-bar/30-minute window calculates:

```text
net displacement = final close - first open
path length      = |close_1 - first open| + sum(|close_i - close_i-1|)
efficiency       = |net displacement| / path length
overlap          = mean adjacent-bar overlap / smaller adjacent range
persistence      = share of non-zero price changes aligned with net direction
midpoint crosses = sign changes around the window high/low midpoint
close location   = (final close - window low) / (window high - window low)
```

Zero denominators receive neutral, explicitly tested values.

## 5. Raw state labels

Frozen definitions:

```text
RAW_BALANCE:
    efficiency <= 0.35
    mean overlap >= 0.50
    midpoint crosses >= 1

RAW_IMBALANCE_UP:
    net displacement > 0
    efficiency >= 0.65
    directional persistence >= 0.67
    close location >= 0.70

RAW_IMBALANCE_DOWN:
    net displacement < 0
    efficiency >= 0.65
    directional persistence >= 0.67
    close location <= 0.30

otherwise -> RAW_TRANSITION
```

`RAW_TRANSITION` is a legitimate uncertain state. It must not be forced into
balance or imbalance.

## 6. Confirmation and episodes

The persistent state machine requires two consecutive raw windows of the same
stable state:

```text
UNKNOWN
  -> BALANCE
  -> IMBALANCE_UP
  -> IMBALANCE_DOWN
```

- The candidate episode starts at the first qualifying raw window.
- `confirmed_at` is the second consecutive qualifying window.
- Until another stable state is confirmed, the live observable state remains
  the previous confirmed state; raw transition windows do not force a flip.
- When a new state is confirmed, the prior episode ends at the candidate start
  of the new state.
- Every episode records start, confirmation, end, duration, censoring, width,
  directional change, and dominant activity regime.
- The timeline retains both raw and live-observable states so future event
  analyses cannot backdate knowledge from confirmation.

## 7. Activity regime

The 30-minute window range is divided by the median of the previous 72 M5
observations of that same rolling-range series, excluding the current row.
At least 36 prior observations are required.

```text
activity ratio <= 0.75 -> quiet
activity ratio >= 1.50 -> active
otherwise              -> normal
```

Activity never changes the primary auction-state label.

## 8. Transition records and signatures

Adjacent confirmed episodes create transitions. Primary transitions are
`balance -> imbalance_up/down`.

Each transition records only information available at or before its
confirmation:

- candidate start and confirmation timestamp;
- prior balance duration and range width;
- break direction and trigger close;
- whether the trigger close exceeded the completed balance boundary;
- upper/lower boundary-test counts within a one-pip tolerance;
- pre-transition and transition activity ratios;
- activity-burst flag (`transition/pre-transition >= 1.50`);
- London and New York local hour;
- distance from both registered session opens; and
- an operational signature:
  `boundary_break_with_activity_burst`, `boundary_break`, or
  `directional_repricing_inside_balance`.

These are associated antecedents/signatures, not proof of economic causation.

## 9. Time and duration analysis

Report separately by year:

- transition matrix and conditional probabilities;
- episode counts and duration quantiles by auction state;
- episode duration by quiet/normal/active regime;
- balance survival and transition hazard for ages
  `[0,30)`, `[30,60)`, `[60,120)`, `[120,240)`, `[240,480)`, and `480+` minutes;
- balance-to-imbalance counts by London-local hour and New-York-local hour;
- transition signature frequencies; and
- direction and activity decomposition.

An episode is right-censored when its segment ends before a subsequent state is
confirmed. Censored observations remain in duration tables and at-risk counts
but are not counted as observed transitions.

## 10. Opening catalyst study

Build DST-aware calendars for:

- registered London/New York opens;
- registered fixed controls; and
- deterministic matched non-opening controls using the existing Phase-1
  weekday/month matching and opening-exclusion rules.

For each event, take the latest state whose `available_at <= event_timestamp`.
Exclude stale states separated from the event by a data gap. Among events
observable as balance at the start, measure a confirmed transition to either
imbalance direction within 15, 30, 60, and 90 minutes.

Report counts and Wilson intervals by year, session, event kind, and horizon.
Opening-minus-control differences are descriptive because the histories have
already been inspected.

## 11. Required invariants

- feature windows never cross a timestamp gap;
- feature availability follows the final input bar close;
- raw-state mapping exactly follows the frozen thresholds;
- activity baseline is shifted and cannot include the current/future range;
- confirmation requires exactly the registered consecutive count;
- episode keys and transition keys are unique;
- episodes do not overlap within a segment;
- transitions connect adjacent episodes in the same segment;
- transition confirmation never precedes candidate start;
- live state at an opening/control event is available no later than the event;
- controls respect their existing deterministic contracts; and
- 2024 and 2025 are never pooled to manufacture a stability claim.

## 12. Artifacts

```text
config/auction_state_taxonomy.yaml
src/gbpusd_research/research/
├── auction_state_taxonomy.py
└── phase7.py
tests/
├── test_auction_state_taxonomy.py
└── test_phase7.py

data/processed/reports/phase7/<run_id>/
├── run_manifest.json
├── data_quality.json
├── state_timeline.parquet
├── state_episodes.parquet
├── state_transitions.parquet
├── state_occupancy.csv
├── episode_statistics.csv
├── transition_matrix.csv
├── balance_hazard.csv
├── transitions_by_clock.csv
├── transition_signatures.csv
├── transition_antecedents.csv
├── opening_control_events.parquet
├── opening_control_statistics.csv
├── opening_control_differences.csv
├── figures/
└── report.md
```

Command:

```bash
.venv/bin/python -m gbpusd_research run-phase7 \
  --research config/research_2024.yaml \
  --second-research config/research_2025.yaml \
  --taxonomy config/auction_state_taxonomy.yaml
```
