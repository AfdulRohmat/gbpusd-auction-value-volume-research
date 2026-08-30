# Technical Plan — GBPUSD Opening-Only Volume Profile Validation Phase 4

**Status:** Frozen before inspecting executable 2024 P&L or any 2025 outcomes  
**Branch:** `phase/04-volume-profile-validation`  
**Development context:** `[2024-01-01, 2025-01-01)`  
**Untouched validation:** `[2025-01-01, 2026-01-01)`  
**Dependencies:** Phase-1 opening expansion and Phase-2 previous-value state

## 1. Objective and research boundary

Phase 4 tests whether the Phase-1 opening expansion and Phase-2 outside-value
reversion observation can be converted into an executable, cost-aware trading
rule. Volume Profile is used only around the London and New York opens. There
are no all-day entries.

The primary hypothesis is New York outside-value reversion. London runs the
identical frozen rules as a separately reported replication; London cannot pass
or rescue the primary gate. Fundamental direction, VWAP direction, news impact,
and Phase-3 scores are excluded.

Phase 4 is the first P&L study. It remains a fixed one-unit-risk research model,
not a production portfolio, leverage recommendation, or live-trading system.

## 2. Evidence carried forward

The rules are derived only from already published aggregate Phase-1/2 results:

- 2024 opening 60-minute range exceeded fixed/matched controls for both London
  and New York, with all four intervals above zero;
- opening outside previous value did not produce reliably larger range than
  opening inside value;
- New York outside-value state-aligned return was negative at 30, 60, and 90
  minutes, with resolved mean reversion at 60 and 90 minutes; and
- London outside-value direction was not statistically resolved.

No Phase-4 trade list or executable P&L was inspected when selecting the rules.

## 3. Frozen data and session contract

- Source: existing zero-cost HistData bid/ask ticks normalized to M5.
- Sessions: `08:00 Europe/London` and `08:00 America/New_York`, converted with
  IANA DST rules.
- Context profile: the latest eligible New-York-close FX-day profile completed
  strictly before the opening event.
- Profile construction: existing 1-pip tick-activity bins and 70% value area.
- Opening state: existing 1-pip buffer around previous VAH/VAL.
- Signal-entry window: first 30 minutes after the named session opens.
- Hard trade timeout: 90 minutes after session open.
- Bar interval: M5; no intrabar ordering is inferred from OHLC.
- Maximum: one trade per named session event.

The 2025 Phase-1 artifact must pass its registered source-quality and opening
effect gate. The 2025 Phase-2 artifact need not reproduce its development
materiality gate before Phase 4 runs, but its data-quality flag, feature
coverage, eligible profiles, and point-in-time invariants must pass.

## 4. Frozen candidate and signal

Only a value-eligible event opening outside prior value is a candidate:

```text
open > previous VAH + 1 pip -> short-reversion candidate
open < previous VAL - 1 pip -> long-reversion candidate
inside value               -> no trade
```

Re-entry is observed only on a completed M5 close across the raw nearest value
boundary:

```text
short candidate: first M5 close <= previous VAH
long candidate:  first M5 close >= previous VAL
```

Eligible signal bars start at the session open and end at minute 25. Entry is
at the next exact M5 bar open, so entry timestamps range from minute 5 through
minute 30. This one-bar delay prevents use of a closing price before it exists.
If the next M5 bar is missing or begins after minute 30, the event is excluded.

There is no immediate outside-value entry, breakout entry, second attempt,
scale-in, or signal after minute 30.

## 5. Frozen execution model

### 5.1 Entry

- Long enters at next-bar `ask_open` plus 0.1 pip adverse slippage.
- Short enters at next-bar `bid_open` minus 0.1 pip adverse slippage.
- Observed bid/ask spread is therefore embedded rather than subtracted again.

### 5.2 Stop

The stop is placed one pip beyond the known opening excursion through the
completed signal bar:

```text
long stop  = minimum mid-low from open through signal bar - 1 pip
short stop = maximum mid-high from open through signal bar + 1 pip
```

There is no optimized fixed-pip or ATR stop. A non-positive stop distance makes
the trade invalid.

### 5.3 Target

The sole target is previous POC. It must lie in the favorable direction from
the executable entry. There is no minimum reward/risk filter, partial exit,
trailing stop, or alternative VA boundary target.

### 5.4 Bar execution and ambiguity

- Long stops trigger on `bid_low`; long targets on `bid_high`.
- Short stops trigger on `ask_high`; short targets on `ask_low`.
- If stop and target are both touched in one M5 bar, the stop is recorded first.
- A stop gap fills at the worse of the stop level or executable bar open.
- Target gaps receive the target price, not a favorable price improvement.
- Every exit receives another 0.1 pip adverse slippage.
- If neither level triggers, exit at the executable bid/ask close of the final
  bar ending at minute 90, with exit slippage.

Initial risk includes the adverse stop-exit slippage. Net P&L already includes
the observed spread and both fixed slippage charges:

```text
R multiple = net P&L pips / initial risk pips
```

The robustness cost stress increases slippage from 0.1 to 0.5 pip per side,
equivalent to subtracting another 0.8 pip from each completed trade. It is
diagnostic and cannot change the primary rules.

## 6. Frozen datasets and sequencing

### 6.1 Development context

2024 trade statistics are generated only after this document, configuration,
engine, and synthetic execution tests are complete. They are descriptive and
have no gate authority. No rule may be altered after viewing them.

### 6.2 Untouched validation

All twelve 2025 source months will be downloaded and checksum-manifested from
the same HistData adapter, built with the existing pipeline, and passed through
the unchanged Phase-1 and Phase-2 features. Phase-4 validation is then run once.

If 2025 source or feature quality fails, the result is a data blocker rather
than permission to fall back to 2024 or mix sources.

## 7. Statistics

Report 2024 and 2025 separately and London/New York separately:

- scheduled, value-eligible, candidate, signal, trade, and exclusion counts;
- long/short counts and calendar-month breadth;
- win rate, target/stop/timeout counts, mean/median P&L pips and R;
- gross profit, gross loss, profit factor, cumulative R, and maximum drawdown R;
- average initial risk and reward/risk;
- monthly R and trade counts;
- primary versus stressed-cost expectancy; and
- deterministic 10,000-resample calendar-month cluster-bootstrap interval for
  mean R.

The cluster unit is the entry calendar month, limiting false precision from
serially related opening regimes. The interval confidence level is 95% because
there is exactly one authoritative primary session/hypothesis.

## 8. Frozen validation gate

The Phase-4 gate passes only if every data/execution invariant passes and the
**2025 New York primary** meets all conditions:

- 2025 Phase-1 source/opening-effect gate passes;
- 2025 Phase-2 data-quality flag passes;
- at least 95% of otherwise eligible opening events receive value features;
- at least 30 completed New York trades across at least nine calendar months;
- at least 10 long and 10 short trades;
- mean net expectancy is at least `+0.10R`;
- the 95% calendar-month cluster-bootstrap interval for mean R is strictly
  above zero;
- net profit factor is at least `1.20`;
- maximum drawdown does not exceed `10R`; and
- mean R remains above zero under 0.5-pip-per-side slippage stress.

The sample thresholds are estimation safeguards, not universal statistical
laws. Failing any condition preserves a failed validation result; it does not
authorize tuning on 2025. London metrics and 2024 metrics cannot participate in
the gate.

## 9. Required invariants

- every joined profile day is strictly earlier than the event FX day;
- candidate direction matches the event-time value state;
- signal close precedes entry and both are inside the frozen 0–30-minute window;
- every price used at entry existed at or before the entry timestamp;
- stop uses no high/low after the completed signal bar;
- exit is after entry and no later than minute 90;
- long execution uses ask entry/bid exit and short uses bid entry/ask exit;
- stop/target mapping, P&L, risk, R, and stressed R arithmetic is exact;
- a future-bar sentinel cannot change a prior signal, entry, or stop; and
- the same event never produces more than one trade.

## 10. Implementation and artifacts

```text
config/opening_value_strategy.yaml
config/research_2025.yaml
src/gbpusd_research/
├── config.py
├── research/opening_value_strategy.py
└── research/phase4.py
tests/
├── test_opening_value_strategy.py
└── test_phase4.py
```

Command:

```bash
python -m gbpusd_research run-phase4 \
  --research config/research_2024.yaml \
  --validation-research config/research_2025.yaml \
  --value-state config/value_state.yaml \
  --opening-value config/opening_value_strategy.yaml
```

Generated artifact contract:

```text
data/processed/reports/phase4/<run_id>/
├── run_manifest.json
├── data_quality.json
├── development_trades.parquet
├── validation_trades.parquet
├── development_events.parquet
├── validation_events.parquet
├── event_funnel.csv
├── performance_statistics.csv
├── monthly_statistics.csv
├── exclusion_statistics.csv
├── figures/
└── report.md
```

The manifest hashes both research configurations, the strategy/value
configurations, Phase-1/2 input manifests, all source archive snapshots, Git
state, runtime, artifact row counts, and the single validation decision.
