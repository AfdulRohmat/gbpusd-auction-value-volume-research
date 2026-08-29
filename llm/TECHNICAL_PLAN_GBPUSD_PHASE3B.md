# Technical Plan — GBPUSD Relative Fundamental Strength Phase 3B

**Status:** Frozen before reading 2024 return outcomes  
**Branch:** `phase/03-fundamental-bias`  
**Development sample:** `[2024-01-01, 2025-01-01)`  
**Baseline dependency:** `llm/PHASE3_RESULTS_2024.md`

## 1. Objective and research boundary

Phase 3B tests the original relative-strength hypothesis: score GBP and USD
independently from information available at each London and New York open, then
use the difference as the GBPUSD directional bias.

This is a separately registered extension of the failed `policy_bias_v1` study.
It does not replace, retune, or erase that result. It remains an event study:
there are no entries, fills, costs, stops, sizing, exits, or P&L.

## 2. Frozen primary score

Each currency has four pillar scores in `{-1, 0, +1}`. All primary weights are
one:

```text
GBP_score = GBP_policy + GBP_inflation + GBP_labor + GBP_yield
USD_score = USD_policy + USD_inflation + USD_labor + USD_yield

relative_score = GBP_score - USD_score

relative_score >= +2  -> long GBPUSD
relative_score <= -2  -> short GBPUSD
otherwise             -> neutral
```

The primary range is `[-8, +8]`. No weight, lookback, deadband, or bias threshold
will be selected using 2024 returns.

## 3. Frozen pillar transforms

### 3.1 Policy

Use the latest enacted official policy rate and the preceding rate observation:

```text
policy_score = sign(current_policy_rate - previous_policy_rate)
```

The score from the latest rate change persists until the next rate change.
Unchanged meetings are not separate observations because they do not change the
state. Bank of England and Federal Reserve publication timestamps follow the
existing audited Phase-3 policy ledger.

### 3.2 Inflation

Use the headline and core CPI twelve-month rates as printed in the archived
initial release. For each indicator:

```text
indicator_signal = sign(latest_yoy_rate - preceding_release_yoy_rate)
inflation_score = sign(headline_signal + core_signal)
```

An unchanged value has signal zero. If headline and core directions disagree,
the pillar is neutral. Later database revisions must not overwrite an archived
release value.

### 3.3 Labor

Use annual regular/hourly earnings growth from the archived initial labor
release. For each currency:

```text
labor_score = sign(latest_yoy_growth - preceding_release_yoy_growth)
```

Rising earnings growth is positive and an unchanged value is neutral. US
payroll count and UK employment-level changes are excluded because their
definitions and revision behavior are not symmetric enough for this relative
score. Unemployment is also excluded from both currencies: the UK series moved
between experimental/adjusted estimates and reweighted Labour Force Survey
estimates around February 2024, so its adjacent release-time changes are not a
consistent economic signal in this development interval.

### 3.4 Two-year yield expectation

Use official daily two-year government yields as a market confirmation of the
expected policy path. Neither session can use its own day's close. Publication
latency is explicit: a Bank of England curve is available at noon London on the
following observed UK business date, while a US Treasury curve is available at
18:00 New York on its observation date. The latter is after the approximately
15:30 market quotation time and Treasury's stated usual publication window.

```text
yield_change = latest_prior_close - close_20_observations_earlier

yield_score = +1 when yield_change >= +0.10 percentage point
yield_score = -1 when yield_change <= -0.10 percentage point
yield_score =  0 otherwise
```

The 20-observation lookback and 10-basis-point deadband are fixed. UK data use
the Bank of England two-year nominal government curve; US data use the official
two-year Treasury constant-maturity/par-yield history. Yield receives only one
primary vote because it can reflect the other three pillars.

## 4. Data and point-in-time contract

Checked-in reference ledgers contain only the small 2023 anchors and 2024 data
needed for this development run:

```text
data/reference/policy_rate_events.csv
data/reference/macro_release_events.csv
data/reference/two_year_yields.csv
```

Required macro fields are event ID, currency, pillar, indicator, reference
period, public UTC timestamp, release-time value/unit, and official source URL.
Required yield fields are currency, observation date, value, unit, and official
source URL.

For every session/day output row:

- every release used must satisfy `available_at_utc <= event_timestamp_utc`;
- later sentinels must never change earlier features;
- both latest and preceding releases must exist for every macro indicator;
- yield availability timestamp must be no later than the event and its
  observation date must be earlier than the event's local date;
- the exact source event IDs/dates and as-of timestamps must be retained; and
- a missing pillar makes the complete primary model unavailable rather than
  silently treating missing data as neutral.

This permits a same-day London and New York bias to differ when an official
release occurs between their opens.

## 5. Frozen robustness model

The impact-weighted model is diagnostic only:

```text
weighted_currency_score =
    3 * policy + 2 * inflation + 2 * labor + 1 * yield

weighted_relative_score = weighted_GBP - weighted_USD

weighted_relative_score >= +3 -> long
weighted_relative_score <= -3 -> short
otherwise                      -> neutral
```

Report primary/weighted direction agreement, disagreement, and neutral cases.
The weighted model cannot rescue a failed primary gate and will not be used to
choose primary parameters.

## 6. Registered analyses and gate

For London and New York separately at 15/30/60/90 minutes:

1. primary bias-aligned return versus zero;
2. return, range, MFE, and MAE by long/neutral/short primary bias;
3. outside-value reversion when the primary bias supports versus opposes it;
4. incremental comparison with `policy_bias_v1`; and
5. primary versus weighted direction agreement as descriptive sensitivity.

Bootstrap settings remain 10,000 resamples, 95% confidence, and the existing
fixed random seed. A primary cell needs at least 30 events. Long and short gate
directions must each span at least two calendar months.

The Phase-3B development gate requires:

- complete-feature coverage of at least 95% of Phase-2 eligible events;
- all point-in-time and arithmetic invariants to pass;
- at least one valid primary long/short directional group and one registered
  comparison satisfying the 30-event minimum;
- required month breadth; and
- at least one **primary equal-weight** registered contrast of at least 2 pips
  whose 95% bootstrap interval excludes zero.

Weighted results do not participate in the gate. Passing still authorizes only
technical-setup research, not a profitability claim.

## 7. Implementation and report contract

```text
config/fundamental_strength.yaml
src/gbpusd_research/
├── data/macro.py
├── features/fundamental_strength.py
└── research/phase3b.py
tests/
├── test_macro.py
├── test_fundamental_strength.py
└── test_phase3b.py
```

Command:

```bash
python -m gbpusd_research run-phase3b \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-strength config/fundamental_strength.yaml
```

Generated artifacts:

```text
data/processed/reports/phase3b/<run_id>/
├── run_manifest.json
├── data_quality.json
├── macro_timeline.csv
├── yield_timeline.csv
├── session_bias.parquet
├── event_exclusions.parquet
├── conditional_statistics.csv
├── statistical_comparisons.csv
├── sensitivity_statistics.csv
├── figures/
└── report.md
```

The manifest hashes every input ledger and configuration, records Phase-2 and
Phase-3 V1 provenance, Git state, runtime, row counts, exclusions, and the gate
decision.

## 8. Delivery sequence

1. validate the official release and yield ledgers;
2. attach point-in-time pillar state to every eligible session open;
3. compute equal-weight primary and weighted sensitivity biases;
4. calculate registered directional/value-state comparisons;
5. produce a reproducible 2024 report; and
6. preserve the result without post-result parameter changes.
