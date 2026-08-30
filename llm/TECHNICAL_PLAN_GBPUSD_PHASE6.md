# Technical Plan — GBPUSD Opening-Auction State Machine Phase 6

**Status:** Frozen before inspecting Phase-6 P&L  
**Branch:** `phase/06-opening-auction-state-machine`  
**Evidence:** 2024 and 2025, reported separately and combined descriptively  
**Authority:** exploratory only; neither inspected year is untouched validation

## 1. Objective

Phase 6 tests a coverage-first Auction Market Theory hypothesis at every
eligible London and New York opening:

> Can the first 15 minutes distinguish directional price discovery from
> two-sided rotation early enough to choose continuation or mean reversion?

The design deliberately avoids the Phase-4 outside-value and confirmed
re-entry filters. Every complete opening auction should produce one state and,
except for a mechanically directionless auction, one trade direction. The
research target is approximately 40 trades per calendar month across the two
sessions, not a forced 56--60% win rate.

## 2. Research governance

- The classifier, stops, exits, costs, cutoffs, and statistics are frozen in
  `config/opening_auction_state_machine.yaml` before Phase-6 P&L is inspected.
- Both 2024 and 2025 were inspected in earlier phases. They are development
  evidence and cannot provide a new validation claim.
- Threshold sensitivity may be proposed only as a separately registered later
  study. Phase 6 does not search observation windows or classifier thresholds.
- Previous value information is retained only as a context label. It cannot
  exclude an otherwise eligible opening or change its direction.
- Phase 6 has no live-trading PASS decision. The `+0.10R` benchmark is a
  descriptive research screen, not authorization to trade.

## 3. Inputs and eligibility

- Source: existing HistData GBPUSD M5 bid/ask bars.
- Events: Phase-2 value-event artifact, because it preserves the Phase-1 event
  contract and point-in-time value context.
- Eligibility: `eligible == true` from Phase 1.
- Value/profile availability is not required.
- Required bars: an exact M5 sequence from session open through the registered
  session cutoff.
- Maximum: one classifier decision and one trade per session event per exit
  variant.

## 4. State machine

```text
PRE_OPEN
    -> OBSERVE_OPENING_AUCTION (three completed M5 bars; minute 0--15)
        -> IMBALANCE_UP   -> long continuation
        -> IMBALANCE_DOWN -> short continuation
        -> BALANCE_HIGH   -> short rotation toward fair value
        -> BALANCE_LOW    -> long rotation toward fair value
        -> NO_DIRECTION   -> no trade (exact degenerate tie only)
    -> MANAGE_POSITION
        -> STOP | TARGET | SESSION_CUTOFF
    -> FLAT
```

The three observation bars have timestamps `open`, `open + 5m`, and
`open + 10m`. Their final close becomes observable at `open + 15m`; entry uses
the executable bid/ask open at that timestamp.

## 5. Opening-auction features

For the first three M5 bars:

```text
O = first mid open
H = maximum mid high
L = minimum mid low
C = final mid close
midpoint = (H + L) / 2
displacement = C - O
path = |close_1 - O| + |close_2 - close_1| + |C - close_2|
efficiency = |displacement| / path
close_location = (C - L) / (H - L)
```

Zero path/range produces zero efficiency and a neutral close location of 0.5.

Frozen classification:

```text
IMBALANCE_UP:
    displacement > 0
    efficiency >= 0.60
    close_location >= 0.70

IMBALANCE_DOWN:
    displacement < 0
    efficiency >= 0.60
    close_location <= 0.30

Otherwise:
    C > midpoint -> BALANCE_HIGH -> short
    C < midpoint -> BALANCE_LOW  -> long
```

If `C == midpoint`, the displacement sign supplies the opposite, rotational
direction. Only an exact zero-displacement and midpoint tie is `NO_DIRECTION`.

## 6. Entry and initial risk

- Long entry: next ask open plus 0.1 pip adverse slippage.
- Short entry: next bid open minus 0.1 pip adverse slippage.
- Imbalance long stop: opening-range midpoint minus 1 pip.
- Imbalance short stop: opening-range midpoint plus 1 pip.
- Balance long stop: opening-range low minus 1 pip.
- Balance short stop: opening-range high plus 1 pip.
- Stop fills use the executable bid for longs and ask for shorts plus 0.1 pip
  adverse slippage. A gap through the stop receives the worse opening fill.
- Non-positive initial risk is excluded as a mechanical invalidity.
- `1R` is the executable entry-to-nominal-stop loss after slippage.

## 7. Session cutoffs and overlap policy

- London positions close at the same-date New York open. This prevents a
  London position from overlapping the next registered opportunity.
- New York positions close at the 17:00 America/New_York FX-day boundary.
- Both cutoffs are constructed from local civil time with IANA timezone rules.
- No position survives its cutoff, and there is no second entry or state flip.

## 8. Exit variants

All variants share the same state, direction, entry, initial stop, and
session cutoff.

### `fixed_2r`

- stop remains fixed;
- target fill realizes exactly `+2R` after registered slippage when touched;
- if neither is touched, close at the session cutoff;
- a bar touching stop and target is resolved stop-first.

### `session_hold`

- initial stop remains fixed;
- no profit target;
- surviving positions close at the session cutoff.

### `trailing_session`

- initial stop remains active;
- after a completed bar first reaches `+1R` favorable excursion, the stop moves
  to an executable break-even trigger for the next bar;
- after activation, the stop also trails the most protective three-completed-
  bar swing low for longs or swing high for shorts;
- stop updates occur only after bar completion and never loosen;
- surviving positions close at the session cutoff.

Processing the active stop before any same-bar break-even/trailing update avoids
assuming favorable intrabar ordering.

## 9. Statistics

Report by year, session, state, and exit variant, plus a combined-session view:

- eligible events, classified events, trades, and retention;
- trades per calendar month and active months;
- long/short counts;
- win rate and exit-reason counts;
- realized average winner, average loser, payoff ratio, and expectancy in R;
- mean/median/net P&L in pips and R;
- profit factor;
- maximum drawdown in R and pips;
- mean MFE/MAE in R;
- deterministic 95% calendar-month cluster-bootstrap interval for mean R.

Monthly combined-session results report trade count, net R, win rate, and
whether the month is positive. Variant comparisons are paired by event so that
exit effects are not confused with selection effects.

## 10. Invariants

- exact three-bar observation window;
- state features use no bar at or after entry;
- state-to-direction mapping is exact;
- all variants for an event share state, entry, stop, and initial R;
- entry occurs exactly 15 minutes after session open;
- executable bid/ask arithmetic and adverse slippage are exact;
- stop-first ambiguity handling is exact;
- trailing updates use completed bars only and never loosen;
- all exits occur no later than the DST-aware session cutoff;
- result keys are unique and registered variants only; and
- changing a future bar cannot change classification, entry, or initial stop.

## 11. Artifacts

```text
config/opening_auction_state_machine.yaml
src/gbpusd_research/research/
├── opening_auction_state_machine.py
└── phase6.py
tests/
├── test_opening_auction_state_machine.py
└── test_phase6.py

data/processed/reports/phase6/<run_id>/
├── run_manifest.json
├── data_quality.json
├── auction_trades.parquet
├── event_funnel.csv
├── variant_statistics.csv
├── state_statistics.csv
├── paired_exit_deltas.csv
├── monthly_statistics.csv
├── figures/
└── report.md
```

Command:

```bash
.venv/bin/python -m gbpusd_research run-phase6 \
  --research config/research_2024.yaml \
  --second-research config/research_2025.yaml \
  --value-state config/value_state.yaml \
  --opening-auction config/opening_auction_state_machine.yaml
```
