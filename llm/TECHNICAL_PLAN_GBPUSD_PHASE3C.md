# Technical Plan — GBPUSD Market-Implied Fundamental Surprise Phase 3C

**Status:** Frozen before reading 2024 next-session return outcomes  
**Branch:** `phase/03-fundamental-bias`  
**Development sample:** `[2024-01-01, 2025-01-01)`  
**Baseline dependencies:** `llm/PHASE3_RESULTS_2024.md` and
`llm/PHASE3B_RESULTS_2024.md`

## 1. Objective and research boundary

Phase 3C tests whether the market's repricing after official UK and US
fundamental catalysts produces a useful GBPUSD bias at subsequent London and
New York opens. It is a separately registered experiment and does not retune or
replace the failed Phase-3 and Phase-3B models.

This remains a signal/event study. It contains no entries, fills, costs, stops,
sizing, exits, or P&L. The development result cannot establish a deployable
strategy or an out-of-sample edge.

## 2. Why the model uses a repricing proxy

Official statistical agencies publish actual releases, but do not publish a
free historical economist-consensus series. Calendar vendors expose historical
actual-versus-consensus data behind authenticated or paid products. Under the
project's zero-cost constraint, Phase 3C must not fabricate consensus, scrape an
unstable website, or silently substitute the vendor's current forecast.

The registered primary feature is therefore **market-implied surprise**, not
literal actual-minus-consensus:

```text
event-day yield shock = 2Y yield on catalyst date
                       - preceding 2Y yield observation
```

The change in the short government yield is a noisy net measure of how the
market interpreted the release, including surprise, revisions, guidance, and
same-day information. It is not a causal isolation of the named catalyst.

## 3. Frozen catalyst universe

Only these official 2024 catalysts are eligible:

- Bank of England and Federal Reserve policy decisions, including unchanged
  decisions;
- UK and US headline/core CPI releases, bundled once per currency and release
  timestamp; and
- UK regular earnings and US average hourly earnings releases.

The existing archived macro ledger supplies release timestamps and source URLs.
A new policy-decision ledger includes all eight decisions for each central bank,
not only meetings that changed the enacted rate.

Each catalyst is mapped to the same currency-local observation date in the
official two-year yield ledger. A missing same-day or preceding observation
makes that catalyst unavailable; it is never mapped to a later market day.

## 4. Point-in-time availability

For every catalyst:

```text
shock_available_at = max(release_timestamp, yield_available_at)
```

The yield availability rules remain the conservative audited Phase-3B rules:

- a Bank of England curve observation becomes available at noon London on the
  next observed UK business date; and
- a US Treasury observation becomes available at 18:00 New York on its
  observation date.

A session may use a shock only when `shock_available_at <= session_open_utc`.
Consequently, the backtest excludes the price move that created the shock and
tests only subsequent continuation. London and New York can legitimately have
different same-day states.

## 5. Frozen per-session bias

For each currency and session open, select the latest available catalyst shock.
It remains active through five official yield observations, counting the event
observation as age zero. A newer catalyst replaces an older one. No active
catalyst is a valid zero shock, not missing data.

```text
GBP_shock = latest active GBP event-day 2Y shock in basis points, else 0
USD_shock = latest active USD event-day 2Y shock in basis points, else 0

relative_shock = GBP_shock - USD_shock

relative_shock >= +5 bp  -> long GBPUSD
relative_shock <= -5 bp  -> short GBPUSD
otherwise                -> neutral
```

The five-observation lifetime, five-basis-point deadband, latest-event rule, and
yield publication assumptions are frozen. They will not be selected using 2024
GBPUSD returns.

## 6. Frozen outcomes and inference

Outcomes use the next open of the **same named session**, not an intraday bar:

```text
1d return = next same-session open - current open
3d return = third subsequent same-session open - current open
5d return = fifth subsequent same-session open - current open
```

The unit is pips. These are trading-session steps, so weekends and holidays are
skipped. Phase-2 `open_price_mid` is reused; no new price source is introduced.

The primary hypothesis is positive bias-aligned return at three session days.
One- and five-day outcomes are registered horizon-consistency checks. Results
are reported separately for London and New York.

Because one catalyst can generate several daily rows, inference uses a cluster
bootstrap over unique GBP/USD catalyst-state pairs rather than treating every
row as independent. Settings are 10,000 resamples and the existing fixed random
seed. The two primary session tests use two-sided 97.5% intervals, a Bonferroni
family-wise 95% rule across London and New York.

## 7. Frozen development gate

The Phase-3C gate passes only if all data and arithmetic invariants pass and at
least one session satisfies every primary condition:

- all registered 2024 catalysts map to same-day and preceding yield
  observations;
- every shock and signal used was available by its session open;
- at least 10 unique directional catalyst-state pairs are present;
- long and short each span at least three unique catalyst-state pairs;
- the three-day mean bias-aligned return is at least +6 pips and its 97.5%
  cluster-bootstrap interval is strictly above zero; and
- the same session's one-day and five-day mean bias-aligned returns are both
  positive.

The counts are estimation safeguards, not universal laws or proof of adequate
statistical power. Passing authorizes only an untouched-period validation.
Failing does not authorize threshold, lifetime, catalyst, or horizon tuning on
the 2024 sample.

## 8. Registered secondary diagnostics

The report also includes, without gate authority:

- long/neutral/short counts and raw returns;
- direction and catalyst-regime breadth;
- results split by catalyst pillar;
- interaction with Phase-2 outside-value reversion; and
- contemporaneous limitations and missing mappings.

## 9. Implementation and artifacts

```text
config/fundamental_repricing.yaml
data/reference/policy_decision_events_2024.csv
src/gbpusd_research/
├── data/macro.py
├── features/fundamental_repricing.py
└── research/phase3c.py
tests/
├── test_fundamental_repricing.py
└── test_phase3c.py
```

Command:

```bash
python -m gbpusd_research run-phase3c \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-repricing config/fundamental_repricing.yaml
```

Generated artifacts:

```text
data/processed/reports/phase3c/<run_id>/
├── run_manifest.json
├── data_quality.json
├── catalyst_yield_shocks.csv
├── session_bias.parquet
├── directional_statistics.csv
├── catalyst_statistics.csv
├── value_interactions.csv
├── figures/
└── report.md
```

