# Technical Plan — GBPUSD Session Research Phase 1

**Status:** Draft for review  
**Source PRD:** `llm/PRD_GBPUSD_Session_Value_Fundamental_Research.md`  
**Scope:** Repository foundation, market-data pipeline, session tagging, and opening event study  
**Explicitly excluded:** VWAP/Volume Profile signals, fundamental scoring, trade simulation, and optimization

## 1. Objective and Delivery Boundary

Phase 1 must answer one question before strategy development begins:

> Are movements after the London and New York FX opens materially and consistently different from comparable non-opening periods?

The phase is complete when the repository can reproducibly:

1. download and cache GBPUSD tick data;
2. validate and normalize it to UTC;
3. produce a clean M5 dataset with bid, ask, mid, spread, and activity fields;
4. tag DST-aware London and New York session events;
5. construct opening and matched-control event datasets without look-ahead;
6. generate tables, charts, uncertainty estimates, and machine-readable results; and
7. pass automated unit, integration, and data-quality tests.

No entry signal or P&L backtest will be implemented before the Phase-1 report is reviewed.

## 2. Technical Decisions

### 2.1 Runtime and package management

- Python: `>=3.12,<3.14`
- Packaging: standard `pyproject.toml`
- Environment: project-local virtual environment (`.venv`)
- Source layout: `src/gbpusd_research`
- Tests: `pytest`
- Primary table format: Parquet with PyArrow
- Human-editable configuration: YAML
- CLI: `python -m gbpusd_research ...` initially; a console script may be added later

Core dependencies:

- pandas
- numpy
- scipy
- pyarrow
- matplotlib
- pydantic
- PyYAML
- httpx
- pytest as a development dependency

`zoneinfo`, `datetime`, `lzma`, and `logging` will use the Python standard library.

### 2.2 Price source and rollout

- V1 source: HistData Generic ASCII tick quotes (free browser download).
- Raw source ZIP files are immutable after successful download.
- HistData publishes monthly archives containing millisecond timestamp, bid,
  ask, and an unused provider volume field.
- Source timestamps are fixed EST (`UTC-05:00`) without daylight-saving
  adjustment and must be converted to UTC using a fixed offset.
- Tick count is the V1 activity proxy because the supplied volume field is zero
  and is not treated as traded volume.

This replaces the PRD's preferred Dukascopy source. On 2026-08-29, the legacy
Dukascopy hourly endpoint repeatedly reset TLS connections, while the current
official archive required AWS S3 Requester Pays. HistData was selected to
preserve the V1 zero-cost requirement and still provide millisecond bid/ask
quotes for 2023–2024.
- Initial smoke-test period: January 2024.
- Initial download cap: `2023-01-01` inclusive to `2025-01-01` exclusive.
- Development period after smoke test: calendar year 2023.
- Validation period: calendar year 2024.
- No separate final holdout is claimed inside this initial two-year dataset.
- Additional years will only be downloaded after the two-year Phase-1 review.

The date ranges are configuration, not code constants. Availability and continuity will be measured before the final split is locked.

### 2.3 Canonical time model

- Every stored timestamp is timezone-aware UTC.
- London timezone: `Europe/London`.
- New York timezone: `America/New_York`.
- Initial London open: `08:00` London local time.
- Initial New York open: `08:00` New York local time.
- Initial session-study window: 90 minutes from each configured open.
- FX trading-day boundary: `17:00 America/New_York`.

Session timestamps are generated in local civil time and converted to UTC. UTC open hours must never be hard-coded. The New York definition is a research convention for the FX session, not the US equity open.

### 2.4 Canonical price and units

- Phase-1 return and excursion measurements use mid prices.
- `mid = (bid + ask) / 2` at tick level.
- GBPUSD pip size: `0.0001`.
- All user-facing movement and spread measurements are stored in pips.
- Raw bid and ask remain available so later execution research can use executable sides.
- Tick count is an activity proxy, not centralized volume.

## 3. Repository Layout

```text
.
├── README.md
├── pyproject.toml
├── .gitignore
├── config/
│   ├── research.yaml
│   └── sessions.yaml
├── data/
│   ├── raw/histdata/        # immutable monthly ZIP source cache
│   ├── interim/ticks/       # normalized daily tick partitions
│   └── processed/
│       ├── m5/              # clean M5 partitions
│       ├── events/          # opening/control event tables
│       └── reports/phase1/  # generated tables and figures
├── notebooks/               # optional exploration only
├── src/gbpusd_research/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── data/
│   │   ├── histdata.py
│   │   ├── normalize.py
│   │   ├── resample.py
│   │   └── validation.py
│   ├── features/
│   │   └── sessions.py
│   ├── research/
│   │   ├── event_study.py
│   │   ├── controls.py
│   │   ├── statistics.py
│   │   └── report.py
│   └── utils/
│       ├── paths.py
│       └── time.py
└── tests/
    ├── fixtures/
    ├── test_config.py
    ├── test_histdata.py
    ├── test_resample.py
    ├── test_validation.py
    ├── test_sessions.py
    ├── test_controls.py
    ├── test_event_study.py
    └── test_no_lookahead.py
```

Generated data and reports are ignored by Git. Small synthetic fixtures and configuration are versioned.

## 4. Configuration Contract

`config/research.yaml` owns data and study parameters:

```yaml
instrument:
  symbol: GBPUSD
  pip_size: 0.0001
  price_decimals: 5

data:
  source: histdata
  raw_frequency: tick
  output_frequency: 5min
  start: "2024-01-01"
  end: "2024-02-01"       # exclusive
  paths:
    raw: data/raw/histdata
    interim: data/interim/ticks
    processed: data/processed

quality:
  reject_crossed_quotes: true
  max_spread_pips_warning: 10.0
  event_min_coverage_ratio: 0.95
  exclude_weekends: true

study:
  horizons_minutes: [5, 15, 30, 60, 90]
  preopen_windows_minutes: [30, 60, 90]
  random_seed: 20240801
  bootstrap_resamples: 10000
  confidence_level: 0.95
```

`config/sessions.yaml` owns all local-time definitions:

```yaml
trading_day:
  timezone: America/New_York
  boundary: "17:00"

sessions:
  london:
    timezone: Europe/London
    open: "08:00"
    study_minutes: 90
  new_york:
    timezone: America/New_York
    open: "08:00"
    study_minutes: 90

controls:
  exclusion_minutes_around_session_open: 120
  samples_per_event: 1
  matching: [weekday, calendar_month, local_start_time_pool]
```

Configuration is parsed into validated, immutable Pydantic models. Unknown keys fail fast to prevent silent misspellings.

## 5. Data Pipeline

### 5.1 Download and raw cache

HistData tick quotes are requested by calendar month. The downloader will:

1. enumerate required GBPUSD month archives for `[start, end)`;
2. open the provider's free download page and extract its short-lived token;
3. POST the documented form fields and stream the ZIP to a temporary file;
4. validate ZIP integrity and require exactly one Generic ASCII CSV member;
5. calculate a SHA-256 checksum;
6. atomically move the valid object into the raw cache; and
7. record status in a manifest.

The manifest contains:

- symbol;
- year and month;
- provider and source page;
- local relative path;
- HTTP/result status;
- byte size;
- uncompressed CSV byte size;
- checksum;
- download timestamp; and
- CSV member name.

Connection retries are bounded. A missing archive is distinct from a network or
parse failure. Re-running a completed month must validate and reuse the cached
archive without downloading it again.

### 5.2 Tick decoding and normalization

Each decoded tick has the following canonical schema:

| Column | Type | Meaning |
|---|---|---|
| `timestamp` | `datetime64[ns, UTC]` | UTC event time |
| `bid` | `float64` | bid price |
| `ask` | `float64` | ask price |
| `mid` | `float64` | arithmetic bid/ask midpoint |
| `spread_pips` | `float32` | `(ask-bid)/pip_size` |
| `activity` | `int8` | one per observed quote; tick-count proxy |
| `source_volume` | `float32` | retained provider field; not used as volume |
| `source_archive` | string | immutable monthly ZIP identifier |

Ordering is stable by timestamp. Exact duplicate records are removed and counted; conflicting records with the same timestamp are retained with a quality flag unless source inspection supports a deterministic rule.

Source timestamps are parsed as fixed EST without DST, then converted to UTC.
Monthly CSV files are read in chunks and daily UTC Parquet partitions are
written atomically. Source prices are checked against plausible GBPUSD ranges
without silently deleting outliers.

### 5.3 M5 resampling

M5 bins are UTC-aligned, left-closed and labeled by bar start. For each price side (`bid`, `ask`, `mid`), store:

- open: first tick;
- high: maximum tick;
- low: minimum tick; and
- close: last tick.

Also store:

- `tick_count`;
- activity count (equal to observed quote count in V1);
- spread open, median, mean, p95, and maximum;
- first and last tick timestamps;
- observed tick span; and
- quality flags.

No synthetic OHLC bars are forward-filled into research calculations. Missing bars remain explicit. Event eligibility is determined from the coverage rule rather than silently imputing prices.

## 6. Data-Quality Gates

Quality checks run at tick, M5, day, and event levels.

### 6.1 Tick-level checks

- timestamps are UTC-aware and monotonic after stable sorting;
- no impossible/non-positive prices;
- `bid <= ask`;
- finite price and activity values;
- duplicate and conflicting timestamp counts;
- negative or implausibly large spread counts; and
- decoding record count agrees with file size.

### 6.2 M5/day-level checks

- OHLC invariants hold for bid, ask, and mid;
- expected weekday coverage is reported, not assumed;
- missing-bar runs are listed;
- weekend observations are identified;
- daily tick counts and spread quantiles are monitored;
- extreme changes are surfaced for inspection; and
- first/last observed timestamps are reported per FX trading day.

### 6.3 Event eligibility

An event is excluded with an explicit reason if:

- the event-open bar is missing;
- required pre-open history is incomplete;
- the requested forward horizon is incomplete;
- coverage falls below the configured threshold;
- crossed/invalid quotes contaminate required bars; or
- the local date is not a valid trading weekday.

Both included and excluded events are exported. Exclusions must never disappear silently.

## 7. Session Calendar and DST

For each local calendar date and configured session:

1. construct the local open using the session's IANA timezone;
2. convert the result to UTC;
3. attach local weekday, UTC offset, and DST status;
4. map it to its FX trading-day identifier; and
5. generate the required pre/post-open intervals.

Tests must cover:

- ordinary winter dates;
- ordinary summer dates;
- the weeks when US DST has changed but UK DST has not;
- the weeks when UK DST has changed but US DST has not;
- year boundaries; and
- conversion around the New York trading-day boundary.

Expected UTC hours in tests are derived from known examples and asserted explicitly. The test must fail if code replaces timezone conversion with a fixed UTC offset.

## 8. Event-Study Dataset

One row represents one session-date event. Primary key:

```text
(instrument, session_name, local_session_date)
```

### 8.1 Identification fields

- instrument;
- session name;
- local session date;
- event-open timestamp UTC;
- event-open timestamp local;
- local UTC offset;
- weekday;
- calendar year/month; and
- FX trading-day identifier.

### 8.2 Pre-open fields

For each 30/60/90-minute window:

- high-low range in pips;
- close-to-close signed return in pips;
- absolute return in pips;
- realized volatility proxy;
- tick count; and
- spread distribution.

All pre-open intervals are half-open and end at the event timestamp, preventing use of the event-open tick or bar.

### 8.3 Forward fields

The reference price is the mid open of the event M5 bar. For each 5/15/30/60/90-minute horizon:

- signed close return in pips;
- absolute close return in pips;
- high-low range in pips;
- maximum upward excursion from reference;
- maximum downward excursion from reference;
- close-direction MFE and MAE;
- tick count/coverage; and
- spread summary.

Directional MFE/MAE does not imply a trade. Both upward and downward excursions are retained so later hypotheses can be evaluated without reconstructing events.

Interval semantics are fixed and tested: a 60-minute outcome at an 08:00 open uses bars starting at 08:00 through 08:55, with the 08:55 close as the horizon close.

### 8.4 Normalized fields

To compare regimes with different volatility, store:

- forward range divided by pre-open 60-minute range;
- absolute forward return divided by pre-open 60-minute range; and
- excursions divided by pre-open 60-minute range.

Zero or unavailable denominators produce null with a reason flag, not infinity.

## 9. Control Design

Two controls will be reported so the conclusion is not dependent on one construction.

### 9.1 Fixed-time controls

Fixed local-time windows will be configured after inspecting data coverage. They must:

- use the same horizon as the corresponding opening event;
- not overlap either configured session-open exclusion zone; and
- remain expressed in an IANA local timezone.

Their purpose is interpretability, not perfect causal matching.

### 9.2 Deterministic matched-random controls

For every valid opening event, select one eligible non-opening start using a fixed seed. Candidate controls must:

- come from the same broad sample split;
- match weekday and calendar month;
- have complete pre/post data;
- not overlap an opening exclusion zone;
- not reuse the opening event interval; and
- use an M5-aligned start.

Sampling metadata and the seed are exported. A second run with identical inputs must select identical controls. Sensitivity runs with several registered seeds may be reported, but the primary seed is fixed before reading results.

Controls are paired at the matching-stratum level where possible. They are not treated as fully independent observations if multiple events share a trading date.

## 10. Statistical Analysis

### 10.1 Pre-registered primary endpoint

Primary endpoint:

- 60-minute high-low range in pips and normalized by the pre-open 60-minute range.

Primary comparisons:

- London open versus its matched control;
- New York open versus its matched control.

The 5/15/30/90-minute horizons and directional returns are secondary endpoints.

### 10.2 Required summaries

For each session/control and horizon:

- observation and exclusion counts;
- mean, standard deviation, median, and MAD;
- p05, p10, p25, p50, p75, p90, and p95;
- mean/median paired difference;
- relative median/mean ratio where defined;
- probability opening movement exceeds matched control;
- 95% confidence interval; and
- effect-size measure.

### 10.3 Inference

- Use a cluster bootstrap by FX trading day to preserve within-day dependence.
- Use paired resampling for matched-control differences.
- Report confidence intervals as the main uncertainty measure.
- Non-parametric permutation or signed-rank tests may supplement, but not replace, effect sizes.
- Secondary multiple comparisons are labeled exploratory; if p-values are reported, apply Benjamini-Hochberg correction by analysis family.
- Do not infer economic tradability from statistical significance.

### 10.4 Required breakdowns

- London versus New York;
- year;
- weekday;
- DST-offset state;
- development versus validation split; and
- spread regime quantile.

Small groups include uncertainty warnings and may be suppressed below a configured minimum sample count.

## 11. Phase-1 Report Contract

One command produces a timestamped report directory containing:

```text
data/processed/reports/phase1/<run_id>/
├── run_manifest.json
├── data_quality.json
├── event_exclusions.parquet
├── events.parquet
├── controls.parquet
├── summary_overall.csv
├── summary_by_year.csv
├── summary_by_weekday.csv
├── statistical_comparisons.csv
├── figures/
│   ├── distribution_by_session.png
│   ├── quantiles_by_horizon.png
│   ├── normalized_range_by_year.png
│   └── spread_around_open.png
└── report.md
```

`run_manifest.json` records:

- configuration snapshot and hash;
- source-data manifest hash;
- Git commit when available;
- Python/package versions;
- start/end time;
- random seed; and
- row counts for every output.

The Markdown report distinguishes observed facts, statistical uncertainty, and interpretation. It must state explicitly that an opening movement effect is not yet a profitable strategy.

## 12. CLI Workflows

Planned commands:

```bash
python -m gbpusd_research download --date 2024-01-02
python -m gbpusd_research build-m5 --date 2024-01-02
python -m gbpusd_research tag-sessions --date 2024-01-02
python -m gbpusd_research validate --config config/research.yaml
python -m gbpusd_research build-events --config config/research.yaml
python -m gbpusd_research report-phase1 --config config/research.yaml
python -m gbpusd_research run-phase1 --config config/research.yaml
```

Every command supports `--dry-run` where useful and uses non-zero exit codes for validation failure. Logging must not contain credentials or dump large binary payloads.

## 13. Test Strategy

### 13.1 Unit tests

- URL and partition enumeration;
- HistData ZIP/CSV decoding from a small synthetic fixture;
- fixed EST-to-UTC conversion, explicitly without DST;
- instrument price scaling;
- tick-to-M5 OHLC aggregation;
- spread statistics;
- session timezone conversion;
- FX trading-day mapping;
- horizon interval boundaries;
- MFE/MAE calculations;
- deterministic control sampling; and
- config rejection for invalid or unknown values.

### 13.2 Property/invariant tests

- OHLC invariants always hold;
- mid remains between bid and ask;
- event features never access timestamps outside declared intervals;
- identical input/config produces identical output and control selection; and
- partition concatenation does not change event results.

### 13.3 Integration tests

- decode a small raw fixture through M5 and event output;
- rerun download/build without duplicating data;
- deliberately remove bars and verify explicit event exclusion;
- run across UK/US DST mismatch weeks; and
- generate a complete small report from synthetic deterministic data.

Network calls are mocked in routine tests. A separately marked smoke test may
contact HistData and is not required for every local test run.

## 14. No-Look-Ahead Controls

Although Phase 1 has no signals, temporal leakage can still distort results. The implementation will enforce:

- pre-open slices use timestamps strictly before event open;
- future outcomes are stored only as labels, never merged into pre-open features;
- M5 bar availability follows bar close time, not bar start time;
- source and output intervals use explicit half-open boundaries;
- control eligibility is based on data availability rules, not outcome magnitude; and
- all later feature tables will distinguish `event_time`, `available_time`, and `observation_time` where applicable.

Tests use sentinel future values to prove that changing data after a horizon cannot alter earlier fields.

## 15. Implementation Milestones

### M0 — Foundation

- initialize Git and Python package;
- add config models, logging, paths, and CLI skeleton;
- configure pytest and lint/format tooling;
- document setup and reproducibility commands.

Exit: clean installation and test command succeeds.

### M1 — One-day market-data vertical slice

- download/decode one UTC day;
- persist normalized ticks;
- produce M5 bars;
- export quality results.

Exit: manually inspect known prices, timestamps, record count, OHLC, and spreads.

### M2 — January 2024 smoke dataset

- download full month;
- validate continuity and cache idempotency;
- pass DST/session tests;
- produce London/NY events and exclusions.

Exit: event counts and several hand-calculated events match code output.

### M3 — Phase-1 development sample

- process calendar year 2023;
- construct fixed and matched controls;
- freeze endpoint definitions;
- generate development report.

Exit: data-quality issues are resolved or explicitly documented.

### M4 — Validation sample

- run the frozen pipeline on calendar year 2024;
- compare effect direction and magnitude with development;
- generate consolidated report.

January 2024 may be inspected during the smoke test only for technical correctness
(decoding, timestamps, bars, and hand-calculated event agreement), not for choosing
research thresholds or judging the opening effect.

Exit: Phase-1 evidence reviewed with 2023 as development and 2024 as validation.

### M5 — Research gate

Choose one outcome:

- continue to value-state research;
- revise the session/control definitions and repeat transparently as a new registered run; or
- stop/rethink because the opening effect is weak, unstable, or data quality is inadequate.

A new, untouched holdout period must be added later before making a final strategy
claim. The initial two-year study is sufficient for pipeline validation and an
early research decision, but not for a definitive robustness claim.

## 16. Review Gate and Evidence Standard

Phase 1 does not have an automatic “pass” based only on a p-value. Continue when the combined evidence shows:

- economically non-trivial opening/control range difference;
- uncertainty intervals that do not indicate the result is entirely noise;
- broadly consistent direction across multiple years;
- validation behavior reasonably consistent with development;
- sufficient sample size and acceptable data coverage; and
- an effect that is not explained solely by extreme spreads or a few outlier dates.

Before running the full development sample, we will register a provisional materiality threshold using January 2024 only for pipeline validation—not for estimating the edge. Changing an endpoint or threshold after seeing development results creates a new versioned research specification.

## 17. Known Risks and Mitigations

| Risk | Mitigation |
|---|---|
| HistData page/form or archive format changes | isolate adapter, fixtures, manifest, strict ZIP/CSV validation |
| Source uses fixed EST rather than New York civil time | explicit UTC−5 parser and boundary tests |
| Large tick-data volume | monthly ZIP cache, chunked CSV reads, daily Parquet partitions |
| DST mistakes | local-time construction, explicit mismatch-week tests |
| Missing/quiet intervals mistaken for zero movement | no synthetic fills; event coverage gate |
| Tick activity interpreted as traded volume | precise naming and report disclaimer |
| Controls accidentally include another open | configurable exclusion zones and overlap tests |
| Multiple-comparison fishing | primary endpoint frozen; secondary work labeled exploratory |
| Holdout leakage | split labels in config and immutable run manifests |
| Opening effect confused with tradability | defer execution/P&L claims until later phases |

## 18. Decisions to Confirm Before M3

The implementation can begin with the defaults above, but these decisions must be confirmed before the multi-year development report:

1. whether `08:00 America/New_York` remains the primary NY FX open or whether `09:30` is included as a separately named equity-open event;
2. exact fixed-time control windows;
3. event minimum coverage rule after observing normal HistData tick behavior;
4. provisional economic-materiality threshold for the primary endpoint; and
5. whether the FX trading-day boundary should remain 17:00 New York for all later profile work.

Any alternative session time must be modeled as a separate named event. It must not silently replace the registered primary definition after results are observed.

## 19. Immediate Build Sequence

The first implementation increment should be deliberately narrow:

1. create M0 repository structure and configuration models;
2. implement and test time/session utilities first;
3. implement a one-month HistData download and one-day decoder;
4. build the tick-to-M5 vertical slice;
5. manually validate one London and one New York event; and
6. only then scale to January 2024.

This sequence exposes timestamp, source-format, and bar-boundary errors before a large historical download makes them expensive to correct.
