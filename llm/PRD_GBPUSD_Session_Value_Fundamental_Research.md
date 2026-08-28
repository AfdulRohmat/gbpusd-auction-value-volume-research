# PRD — GBPUSD Session-Driven Value + Fundamental Bias Research

**Status:** Research V1  
**Primary environment:** Python on macOS  
**Primary instrument:** GBPUSD  
**Execution timeframe:** M5  
**Primary sessions:** London Open and New York Open  
**Initial budget for data:** Free only  

---

## 1. Executive Summary

Build a Python research and backtesting pipeline to test whether GBPUSD has a repeatable intraday edge around the London and New York opens when trade direction is filtered by relative GBP-vs-USD fundamental bias and entries are conditioned on market location using VWAP and Volume Profile.

The project must start as an empirical research pipeline, not as a strategy optimizer. The first objective is to measure conditional distributions around session opens and verify that the observed opening expansion is persistent. Only after that should executable trading rules be formalized.

Core idea:

> **Fundamental bias determines direction; session timing determines when; VWAP/Volume Profile determine where; M5 behavior determines entry.**

---

## 2. Problem Statement

Previous VWAP/Volume Profile tests were too permissive: trades could occur throughout the day and across different market regimes. This likely mixed high-information opening periods with low-information balanced/choppy periods and allowed the same entry logic to operate in both trend and mean-reversion environments.

Observed discretionary behavior on GBPUSD suggests that meaningful expansion frequently occurs near London and New York opens. The research should test whether restricting activity to these windows and conditioning trades on relative GBP/USD fundamentals and value-state improves expectancy, profit factor, and drawdown.

---

## 3. Research Backbone

The project is based on four research themes:

1. **Intraday FX seasonality** — FX activity and volatility vary by time of day; London, New York, and their overlap are structurally important liquidity windows.
2. **Macroeconomic repricing** — currency pairs react to relative macro conditions, rate expectations, economic surprises, and policy expectations.
3. **Market value / auction framing** — VWAP and Volume Profile are used as state/location variables rather than standalone buy/sell signals.
4. **Opening transition hypothesis** — the strategy focuses on the transition from pre-session balance into opening expansion, rejection, or acceptance.

Important: this backbone motivates the hypotheses but does **not** prove the combined strategy has an edge. The project must establish that empirically.

---

## 4. Goals

### 4.1 Primary Goals

- Verify statistically whether GBPUSD exhibits materially higher range expansion around London and NY opens than control periods.
- Measure how session-open behavior changes conditional on price location relative to VWAP and prior value area.
- Build a point-in-time relative GBP-vs-USD fundamental bias model using only data available before each simulated entry.
- Test whether fundamental alignment improves technical setup expectancy.
- Build a reproducible Python backtest with realistic spread/commission assumptions.

### 4.2 Secondary Goals

- Compare London vs New York behavior separately.
- Distinguish trend continuation from failed-auction / mean-reversion regimes.
- Produce event-level datasets suitable for later ML/statistical modeling.
- Prepare a clean architecture that can later incorporate footprint/intrabar confirmation.

### 4.3 Non-Goals for V1

- No live automated trading.
- No parameter-heavy optimization.
- No machine-learning prediction model.
- No NLP/news sentiment engine.
- No full exchange-grade footprint/order-flow reconstruction.
- No multi-pair portfolio in V1.
- No assumption that spot-FX tick volume equals centralized traded volume.

---

## 5. Key Research Hypotheses

### H0 — Baseline
VWAP/Volume Profile setups applied indiscriminately across the full trading day do not produce a robust edge after costs.

### H1 — Session Timing
The distribution of GBPUSD movement during the first 30–90 minutes after London and New York opens differs materially from comparable non-opening windows.

### H2 — Fundamental Alignment
Setups aligned with relative GBP-vs-USD fundamental bias have higher expectancy than identical counter-bias setups.

### H3 — Value State
Opening from/through value and opening outside value represent different regimes and should not use identical entry logic.

### H4 — First Pullback / Retest
The first quality pullback or retest after opening displacement may offer better risk-adjusted entry than immediate opening breakout entry.

H4 is explicitly an empirical hypothesis and must not be assumed true.

---

## 6. Data Requirements

### 6.1 GBPUSD Price Data

**Preferred V1 source:** Dukascopy historical data.

Required fields where available:
- timestamp
- bid
- ask
- bid/ask volume or tick activity

Target raw granularity:
- tick or M1 preferred
- resample internally to M5

Target period:
- ideally 2020–present
- start with the longest clean continuous period available

Derived M5 fields:
- bid OHLC
- ask OHLC where possible
- mid OHLC
- spread statistics
- tick count / activity proxy
- session tags

### 6.2 Macro / Fundamental Data

V1 must remain free.

Potential free sources:
- FRED
- ALFRED for vintage/revision-safe US series
- official UK/US public datasets where practical

V1 fundamental engine does **not** require a full historical economic-calendar consensus dataset.

Initial fundamental proxies may include:
- UK vs US policy-rate direction
- UK vs US inflation trend
- UK vs US labor/economic trend
- UK vs US short-rate / yield differential where clean free data is available
- broad GBP and USD relative market-strength proxies if added later

### 6.3 Future Data Layer

After V1 proves useful, evaluate adding historical actual-vs-consensus economic surprise data from a free or affordable source.

---

## 7. Time and Session Handling

This is critical.

All raw timestamps must be normalized internally to **UTC**.

Session definitions must handle UK and US daylight-saving changes independently.

Do **not** hard-code London and New York opens as the same UTC hour throughout the year.

Recommended implementation:
- Python `zoneinfo`
- London timezone: `Europe/London`
- New York timezone: `America/New_York`

Default research windows:
- London: first 90 minutes from London session-open definition
- New York: first 90 minutes from NY session-open definition

Exact open definitions must be stored as configuration and tested rather than buried in strategy code.

---

## 8. Market-State Variables

### 8.1 Session VWAP

VWAP is a state/reference variable, not an automatic signal.

For spot FX, use a clearly labeled proxy:

`VWAP_proxy = sum(price * activity_weight) / sum(activity_weight)`

Possible V1 weighting:
- tick activity / available volume proxy

Store:
- current VWAP
- distance from VWAP in pips
- rolling/session standard deviation
- VWAP z-score
- normalized VWAP slope

### 8.2 Volume Profile

Use fixed historical profiles for decision-making wherever possible.

For London research:
- previous day VAH / VAL / POC
- optional Asian/pre-London profile in later versions

For NY research:
- previous day profile
- London-session VAH / VAL / POC when completed/available at the decision timestamp

Profile must use only information available before the simulated decision.

Store:
- POC
- VAH
- VAL
- distance to each level
- inside/outside prior value

V1 must explicitly label this as **tick-activity / FX-volume proxy profile**, not centralized exchange volume.

---

## 9. Fundamental Bias Engine V1

Output must be deliberately simple:

- `+1` = bullish GBPUSD / long-only bias
- `0` = neutral / no directional edge
- `-1` = bearish GBPUSD / short-only bias

Conceptual model:

`relative_bias = GBP_score - USD_score`

Possible score dimensions:
- monetary-policy direction
- inflation trend
- labor/economic trend
- yield/rate differential

Requirements:
- point-in-time safe
- no future data
- no later revisions unless using vintage data correctly
- score timestamped so each session can use the latest valid information

V1 should favor robustness over complexity. Avoid dozens of weighted variables.

---

## 10. Phase 1 — Empirical Session Study

Before placing any simulated trades, generate an event dataset for every valid London and NY session.

For each session open, capture:
- pre-open 30/60/90-minute range
- open price
- previous day VAH / VAL / POC
- distance to VWAP
- inside/outside value
- 5m return after open
- 15m return
- 30m return
- 60m return
- 90m return
- maximum favorable excursion up/down
- maximum adverse excursion up/down
- range expansion
- spread around open
- fundamental bias if available

Control groups:
- comparable non-opening windows
- random time-of-day samples matched by weekday where useful

Primary question:

> Is session-open movement statistically different enough to justify building a strategy around it?

Deliverables:
- descriptive statistics
- distributions
- quantiles
- bootstrap confidence intervals where appropriate
- London vs NY comparison
- by weekday
- by year/regime

---

## 11. Phase 2 — Technical Setup Construction

Only begin after Phase 1 results justify continuation.

### Setup A — Opening Expansion + VWAP First Pullback

Bullish example:
1. fundamental bias = long
2. opening displacement upward
3. price above VWAP
4. VWAP slope positive
5. first M5 pullback toward VWAP/value boundary
6. no acceptance back below invalidation area
7. enter long

Bearish is symmetric.

### Setup B — Value-Area Rejection

Bullish example:
1. long fundamental bias
2. price tests/sweeps prior VAL
3. M5 fails to accept below VAL
4. closes/reclaims back above VAL
5. long

Bearish at VAH is symmetric.

### Setup C — Value-Area Breakout + Retest

Bullish example:
1. long bias
2. break above prior VAH
3. acceptance above VAH
4. first retest holds
5. long

Bearish below VAL is symmetric.

---

## 12. Definitions That Must Be Formalized Before Backtest

Avoid vague discretionary terms in code.

### Displacement
Candidate definition should use normalized movement, e.g.:
- M5 return relative to recent ATR/range
- close location
- body-to-range ratio

### Acceptance
Must require more than a wick.
Candidate approaches:
- N consecutive M5 closes outside a level
- time spent outside
- combination of close + retest hold

### Rejection
Candidate requirements:
- excursion through level
- close back across level
- minimum wick/rejection fraction

### First Pullback
Define exactly how the first eligible pullback is identified and when it expires.

All thresholds must be configuration parameters with sane defaults; do not optimize them prematurely.

---

## 13. Execution and Risk Model

Baseline research rules:
- M5 entries
- London and NY opening windows only
- max 1 trade per session
- max 2 trades/day
- one open position at a time for V1
- long trades only when bias is long
- short trades only when bias is short
- neutral bias = no fundamental-filtered trade

Initial exits:
- structural stop preferred
- fixed 1:2 RR baseline for comparability

Later compare:
- 1.5R
- 2R
- session target / opposing value level
- time-based exit

Costs must include:
- bid/ask spread from source when possible
- configurable commission per lot
- optional slippage stress test

Broker-specific cost profile should be configurable rather than hard-coded.

---

## 14. Backtest Methodology

### 14.1 No Look-Ahead
At timestamp `t`, only data published or completed at/before `t` may be used.

This applies to:
- macro data
- profile calculations
- VWAP
- M5 bars
- fundamental score

### 14.2 Train / Validation / Test
Do not optimize on the full history.

Example split if 2020–2026 is available:
- Research/train: 2020–2023
- Validation: 2024–2025
- Final holdout: 2026

Exact split may change based on data availability.

### 14.3 Walk-Forward
If parameters are optimized later, use walk-forward or rolling validation.

### 14.4 Ablation Tests
Mandatory comparisons:

- A: technical setup all day
- B: technical setup London/NY only
- C: B + VWAP/VP state
- D: C + fundamental directional filter
- E: D + future footprint confirmation

Goal: identify which component actually contributes edge.

---

## 15. Metrics

Do not judge strategy by net profit alone.

Required:
- number of trades
- win rate
- average win
- average loss
- expectancy per trade
- profit factor
- maximum drawdown
- return / drawdown
- Sharpe-like return metric where appropriate
- average MFE
- average MAE
- average holding time

Breakdowns:
- London vs NY
- long vs short
- year
- weekday
- fundamental-bias strength
- setup type
- inside vs outside previous value
- trend vs balanced state

Also report confidence/uncertainty for small samples.

---

## 16. Project Architecture

Recommended repository structure:

```text
gbpusd-session-research/
├── README.md
├── pyproject.toml
├── .gitignore
├── config/
│   ├── research.yaml
│   ├── sessions.yaml
│   └── costs.yaml
├── data/
│   ├── raw/
│   ├── interim/
│   └── processed/
├── notebooks/
│   ├── 01_data_quality.ipynb
│   ├── 02_session_distribution.ipynb
│   ├── 03_vwap_vp_study.ipynb
│   ├── 04_fundamental_bias.ipynb
│   └── 05_strategy_analysis.ipynb
├── src/
│   └── gbpusd_research/
│       ├── data/
│       │   ├── dukascopy.py
│       │   ├── macro.py
│       │   └── validation.py
│       ├── features/
│       │   ├── sessions.py
│       │   ├── vwap.py
│       │   ├── volume_profile.py
│       │   ├── volatility.py
│       │   └── fundamentals.py
│       ├── research/
│       │   ├── event_study.py
│       │   └── conditional_stats.py
│       ├── strategy/
│       │   ├── signals.py
│       │   ├── execution.py
│       │   └── risk.py
│       ├── backtest/
│       │   ├── engine.py
│       │   ├── metrics.py
│       │   └── reports.py
│       └── utils/
│           └── time.py
└── tests/
    ├── test_sessions.py
    ├── test_vwap.py
    ├── test_volume_profile.py
    ├── test_no_lookahead.py
    └── test_execution.py
```

Prefer reusable modules over putting strategy logic inside notebooks.

---

## 17. Technology Choices

Recommended minimal stack:
- Python 3.12+
- pandas
- numpy
- scipy
- matplotlib
- pyarrow
- pydantic or dataclasses for config/models
- PyYAML
- pytest
- requests/httpx only where required for data downloads

Optional later:
- polars for performance
- numba for heavy profile/tick calculations
- statsmodels for formal statistical tests

Avoid introducing a full backtesting framework until the research requirements demand it. A small transparent event-driven/vectorized hybrid engine is preferable initially because execution assumptions must remain auditable.

---

## 18. Data Quality Checks

Before analysis:
- duplicate timestamps
- missing bars
- weekend data
- DST/session correctness
- abnormal spread spikes
- impossible OHLC
- bid > ask errors
- timezone consistency
- missing macro observations

Generate a data-quality report before every major research run.

---

## 19. Acceptance Criteria

### Milestone 1 — Data Pipeline
- GBPUSD historical data downloaded and cached locally.
- Resampling to M5 reproducible.
- UTC timestamps clean.
- London/NY session tagging passes DST tests.
- Spread statistics available.

### Milestone 2 — Session Event Study
- At least 2–3 years of valid sessions analyzed, preferably more.
- Opening return/MFE/MAE distributions produced.
- London and NY compared against control periods.
- Results segmented by year.

### Milestone 3 — VWAP/VP Study
- No look-ahead in VWAP/profile features.
- Prior VAH/VAL/POC available at each event.
- Conditional opening distributions by value-state generated.

### Milestone 4 — Fundamental V1
- GBP and USD scores reproducibly generated.
- Score is point-in-time safe.
- `long/neutral/short` bias emitted for each session.

### Milestone 5 — Baseline Strategy
- Maximum one entry per session.
- Costs modeled.
- Full metrics and trade ledger exported.
- Ablation comparison available.

### Milestone 6 — Robustness
- Out-of-sample results available.
- Performance does not depend on a single year/month.
- Parameter sensitivity checked around chosen defaults.

---

## 20. Success Criteria for Continuing the Research

Do **not** require a spectacular equity curve in V1.

Continue if evidence shows some combination of:
- session-open movement is consistently different from control periods
- conditional value-state materially changes forward return/MFE/MAE distribution
- fundamental alignment improves expectancy or reduces adverse excursion
- edge persists across multiple years and both London/NY subsets

Stop/rethink if:
- effect exists only in one short period
- costs eliminate expectancy
- results are highly threshold-sensitive
- fundamental filter only works through look-ahead/revised data
- VP/VWAP adds no measurable information beyond session timing

---

## 21. Future Phase — Footprint Confirmation

Only after the session/value/fundamental framework is validated.

Possible features:
- M1/intrabar delta proxy
- effort-vs-result
- local imbalance clusters
- failed continuation
- absorption proxy

Purpose:

> Improve entry precision / false-signal rejection, not determine macro direction.

Run as an ablation layer against the validated baseline.

---

## 22. First Codex Tasks

1. Initialize repository and Python environment.
2. Implement configurable data directory and YAML config.
3. Implement Dukascopy GBPUSD downloader/cache.
4. Normalize raw data to UTC and build clean M5 dataset.
5. Implement DST-aware London/NY session tagging with tests.
6. Build Phase-1 session event-study dataset.
7. Produce first report comparing 5/15/30/60/90-minute opening moves vs control windows.
8. Only after reviewing that report, implement VWAP and previous-day Volume Profile.

**Do not implement trading signals before Task 7 is reviewed.**

---

## 23. Guiding Principle

The project should answer questions in this order:

```text
Does the opening effect exist?
        ↓
When is it strongest?
        ↓
Does value-state explain the direction/type of move?
        ↓
Does GBP-vs-USD fundamental bias improve conditional expectancy?
        ↓
Can a simple M5 entry rule monetize it after costs?
        ↓
Does footprint add incremental value?
```

Avoid the reverse workflow of creating a complicated strategy first and searching for data that makes it profitable.

