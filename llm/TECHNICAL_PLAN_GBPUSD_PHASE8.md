# Technical Plan — GBPUSD Balance-Boundary Strategy Phase 8

**Status:** Frozen before inspecting Phase-8 P&L
**Branch:** `phase/08-balance-boundary-strategy`
**Evidence:** GBPUSD M5 for 2024 and 2025, reported separately
**Authority:** exploratory development and replication only

## 1. Objective

Phase 8 tests whether the boundary of a point-in-time confirmed balance episode
has economic value as contextual support/resistance during the London and New
York opening windows.

It does not assume that the 78–79% observable balance occupancy found in Phase 7
is an edge. That share includes hysteresis through uncertain raw-transition
windows. Phase 8 instead requires an executable trigger at a boundary and tests
two mutually exclusive auction outcomes:

1. rejection back into balance, followed by rotation toward the midpoint; or
2. acceptance outside balance, followed by directional continuation.

## 2. Evidence status

Both 2024 and 2025 have already informed earlier research. Phase 8 therefore
uses:

- 2024 as development evidence; and
- 2025 as a cross-year replication check, not untouched validation.

No result from this phase can authorize live trading. A successful hypothesis
requires a new forward period with frozen code and configuration.

## 3. Upstream state contract

Phase 8 recomputes the frozen Phase-7 taxonomy from:

```text
config/auction_state_taxonomy.yaml
```

At each registered opening, the strategy takes the latest timeline row whose
`available_at <= event_timestamp`. The opening is eligible only when:

- the state is no more than five minutes stale;
- `observable_state == balance`;
- an observable balance episode identifier exists; and
- a non-zero balance boundary can be reconstructed using only rows available by
  the opening.

The balance high, low, and midpoint are frozen at the opening. They cannot
expand later in response to future prices.

## 4. Session and data contract

- Events: every registered weekday London and New York open.
- Signal window: `[session open, session open + 90 minutes)`.
- London management cutoff: registered New York open on the same civil date.
- New York management cutoff: registered 17:00 New York FX-day boundary.
- Every M5 bar from the opening through the cutoff must exist exactly once.
- All state and trigger decisions use completed M5 bars only.
- Entry occurs at the next M5 bar open after the trigger becomes available.
- At most one setup is selected per session; the first valid trigger wins.

## 5. Frozen boundary

For the balance episode observable at the opening, use every completed bar from
its now-known candidate start through the opening:

```text
balance_high = maximum completed M5 high in the episode by the opening
balance_low  = minimum completed M5 low in the episode by the opening
midpoint     = (balance_high + balance_low) / 2
```

The candidate start may be used only after confirmation has made that episode
identifier observable. A bar available by the opening remains in the boundary
even if a later confirmation retrospectively assigns it to the next episode;
otherwise the boundary would leak knowledge of that later confirmation. Every
bar whose `available_at` is later than the opening remains excluded.

## 6. Setup A — rotation after boundary rejection

This is the primary hypothesis.

Upper rejection:

```text
observable state remains the opening balance episode
raw state is balance or transition
bar high >= balance_high - 1.0 pip
bar close <= balance_high - 0.5 pip
next-bar short entry
```

Lower rejection is symmetric:

```text
bar low <= balance_low + 1.0 pip
bar close >= balance_low + 0.5 pip
next-bar long entry
```

If one completed bar qualifies on both boundaries, the event is classified as
ambiguous and receives no trade. A raw imbalance candidate is never faded.

Execution:

- structural stop: one pip outside the frozen boundary;
- target: frozen midpoint;
- minimum executable target reward: `1.50R` after entry and nominal stop-fill
  slippage;
- if reward or risk is non-positive, the signal is rejected; and
- unresolved trades exit at the registered session cutoff.

The midpoint is the thesis target. It is not extended to a fixed 2R target when
the midpoint offers less than 2R.

## 7. Setup B — accepted balance breakout

This is a comparator, not a post-hoc replacement for rejection.

Upward acceptance requires all of the following after the opening:

```text
Phase-7 transition comes from the opening balance episode
new observable state == imbalance_up
two consecutive completed M5 closes >= balance_high + 0.5 pip
```

Downward acceptance is symmetric below `balance_low - 0.5 pip`. Both accepted
closes must occur inside the registered signal window. Entry is at the next M5
open after confirmation.

Execution:

- stop: one pip back inside the broken frozen boundary;
- primary comparator exit: fixed `2R`;
- registered secondary exit: one-R break-even activation followed by a
  three-completed-bar swing trail; and
- unresolved trades exit at the session cutoff.

## 8. Trigger precedence

Bars are evaluated chronologically. Rejection and acceptance conditions are
disjoint by close location. The first executable setup wins.

- An economically invalid rejection may be skipped while the opening balance
  episode remains observable.
- A confirmed accepted breakout that gaps back through its structural stop at
  the next open is excluded and ends the event; the former balance context has
  already ended.
- No later setup may replace an executed or structurally invalid accepted
  breakout.

## 9. Quote-side execution

- Long entry: next-bar ask open plus `0.1` pip slippage.
- Short entry: next-bar bid open minus `0.1` pip slippage.
- Long stops and targets are triggered on bid prices.
- Short stops and targets are triggered on ask prices.
- Stop fills include adverse gap handling and `0.1` pip slippage.
- Structural midpoint targets require the executable quote to trade through the
  level by the exit-slippage allowance.
- Same-bar stop and target ambiguity is resolved stop-first.
- No position sizing, compounding, or overlapping portfolio leverage is added.

## 10. Registered analyses

Setup variants:

```text
rotation_midpoint
acceptance_fixed_2r
acceptance_trailing_session
```

The two combined route diagnostics use the same first-trigger event stream:

```text
combined_fixed_2r:
    rejection -> rotation_midpoint
    acceptance -> acceptance_fixed_2r

combined_trailing_session:
    rejection -> rotation_midpoint
    acceptance -> acceptance_trailing_session
```

Report separately by year and session:

- event funnel and exclusion reasons;
- context/raw-state and trigger frequencies;
- trades per calendar month;
- win rate, average winner and loser, payoff ratio;
- expectancy, net R, profit factor, and maximum drawdown;
- month-cluster 95% interval for mean R;
- MFE, MAE, holding time, and exit attribution;
- monthly consistency; and
- fixed-versus-trailing paired acceptance delta.

The descriptive benchmark is `+0.10R/trade`, with at least 30 events required
before interpreting a cell. Frequency is an outcome, not a rule to be repaired
after seeing results.

## 11. Required invariants

- event and trade keys are unique;
- state and frozen-boundary inputs are available no later than the opening;
- every traded event was observable as balance at its opening;
- boundary bars never extend beyond the opening;
- trigger bars lie inside the 90-minute signal window;
- entry is exactly the next M5 open after the completed trigger;
- rejection direction, close-inside rule, raw-state restriction, and minimum
  reward-to-risk are exact;
- acceptance originates from the opening balance episode, has the matching
  confirmed direction, and has the registered number of closes outside;
- initial risk is positive and finite;
- stops, targets, slippage, gap fills, and stop-first ambiguity are exact;
- exits occur after entry and no later than the session cutoff;
- trailing stops never loosen;
- setup and combined variants share their registered event executions; and
- 2024 and 2025 are never pooled to claim validation.

## 12. Artifacts

```text
config/balance_boundary_strategy.yaml
src/gbpusd_research/research/
├── balance_boundary_strategy.py
└── phase8.py
tests/
├── test_balance_boundary_strategy.py
└── test_phase8.py

data/processed/reports/phase8/<run_id>/
├── run_manifest.json
├── data_quality.json
├── boundary_events.parquet
├── setup_trades.parquet
├── analysis_trades.parquet
├── event_funnel.csv
├── exclusion_reasons.csv
├── variant_statistics.csv
├── setup_statistics.csv
├── exit_statistics.csv
├── paired_acceptance_deltas.csv
├── monthly_statistics.csv
├── figures/
└── report.md
```

Command:

```bash
.venv/bin/python -m gbpusd_research run-phase8 \
  --research config/research_2024.yaml \
  --second-research config/research_2025.yaml \
  --taxonomy config/auction_state_taxonomy.yaml \
  --balance-boundary config/balance_boundary_strategy.yaml
```
