# Phase 4 Results — GBPUSD Opening-Only Volume Profile Validation

**Execution date:** 2026-08-30  
**Development context:** `[2024-01-01, 2025-01-01)`  
**Untouched validation:** `[2025-01-01, 2026-01-01)`  
**Primary session:** New York  
**Authoritative decision:** **FAIL**

## 1. What was tested

Phase 4 converted the surviving Phase-1/2 observation into one frozen,
cost-aware rule before inspecting its executable P&L:

- only London and New York opening events were considered;
- only opens outside the previous eligible value area were candidates;
- the first completed M5 close back across VAH/VAL within 30 minutes signaled
  reversion;
- entry occurred at the next M5 bid/ask open;
- the stop was one pip beyond the known opening excursion;
- previous POC was the sole target;
- every trade ended by target, stop, or 90-minute hard timeout; and
- observed spread plus 0.1-pip adverse slippage per side were included.

New York was the sole primary hypothesis. London used identical rules as a
replication and had no authority to rescue the validation decision.

## 2. Data and upstream gates

The zero-cost HistData adapter downloaded and checksum-manifested all twelve
2025 tick archives:

- compressed source size: approximately 155 MB;
- uncompressed CSV size: approximately 951 MB;
- normalized observations: about 24.4 million ticks and 74,650 M5 bars; and
- all monthly tick and M5 quality reports: valid.

Phase 1 produced 518 eligible 2025 openings. Its single-year source/opening
effect gate passed. The legacy overall flag remained false only because it also
contains the deliberately multi-year-only check.

Phase 2 produced 516 value-eligible events and passed data quality with
`99.61%` value-feature coverage and all point-in-time invariants true. Its
material state-contrast research gate remained false, as expected; the frozen
Phase-4 plan required Phase-2 data quality rather than reusing that development
materiality decision as a validation prerequisite.

## 3. Performance

| Sample | Session | Trades | Long / short | Active months | Mean R | 95% month-cluster CI | PF | Max DD | Stressed mean R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 development | London | 19 | 8 / 11 | 9 | +0.065 | [-0.361, +0.360] | 1.17 | 2.81R | -0.020 |
| 2024 development | New York | 22 | 10 / 12 | 11 | +0.234 | [-0.160, +0.543] | 1.71 | 3.64R | +0.151 |
| 2025 validation | London | 21 | 11 / 10 | 9 | -0.137 | [-0.572, +0.302] | 0.70 | 4.58R | -0.213 |
| **2025 validation** | **New York** | **14** | **10 / 4** | **8** | **-0.191** | **[-0.678, +0.285]** | **0.69** | **3.62R** | **-0.285** |

The 2024 New York result was positive but uncertain and had only 22 trades. It
did not carry into the untouched year: New York 2025 was negative before and
after the cost stress. London was also negative in validation.

## 4. Funnel

| Sample | Session | Scheduled | Value eligible | Outside-value candidates | Signals | Trades |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | London | 262 | 259 | 143 | 19 | 19 |
| 2024 | New York | 262 | 259 | 164 | 22 | 22 |
| 2025 | London | 261 | 258 | 151 | 22 | 21 |
| 2025 | New York | 261 | 258 | 165 | 17 | 14 |

For 2025 New York, 148 candidates did not complete a value-area re-entry by the
30-minute deadline. Three signals were excluded because POC was not favorable
from the executable entry. The remaining 14 trades were too sparse for the
registered estimation safeguards.

## 5. Validation gate

Passed:

- execution and point-in-time invariants;
- Phase-1 source/opening-effect prerequisite;
- Phase-2 data-quality prerequisite;
- value-feature coverage;
- minimum long trades; and
- maximum drawdown limit.

Failed:

- at least 30 New York trades: `14`;
- at least nine active months: `8`;
- at least ten short trades: `4`;
- mean expectancy at least `+0.10R`: `-0.191R`;
- 95% cluster interval strictly above zero: lower bound `-0.678R`;
- profit factor at least `1.20`: `0.69`; and
- positive stressed expectancy: `-0.285R`.

The gate therefore fails on both sample sufficiency and realized performance.
This is not a data-quality failure.

## 6. Interpretation

The evidence supports a narrower conclusion than “Volume Profile never works”:

1. GBPUSD still exhibits unusually large movement around London and New York
   opens. That is a timing/volatility property, not a directional edge.
2. Previous value area can describe opening context, but this particular
   30-minute close-back-inside entry, excursion stop, and POC target did not
   generalize to 2025 New York.
3. The positive 2024 New York mean was compatible with sampling noise or a
   year-specific regime. Its uncertainty interval already crossed zero.
4. Sparse re-entry signals are a structural limitation of this exact rule, not
   permission to relax the deadline after observing 2025.

The frozen Phase-4 rule should be preserved as a failed validation. Any new
opening-range or Volume Profile setup must be registered as a new phase and
must not use 2025 as an untouched validation sample again.

## 7. Reproducible artifacts

```text
data/processed/reports/phase4/20240101_20260101_f1e2a5af/
├── run_manifest.json
├── data_quality.json
├── development_events.parquet
├── development_trades.parquet
├── validation_events.parquet
├── validation_trades.parquet
├── event_funnel.csv
├── performance_statistics.csv
├── monthly_statistics.csv
├── exclusion_statistics.csv
├── figures/
└── report.md
```

The manifest records the frozen configuration hash, upstream Phase-1/2
manifest hashes, all 2024/2025 source archive checksums, Git state, artifact
checksums, row counts, and the authoritative decision.

Reproduce the final run with:

```bash
.venv/bin/python -m gbpusd_research run-phase4 \
  --research config/research_2024.yaml \
  --validation-research config/research_2025.yaml \
  --value-state config/value_state.yaml \
  --opening-value config/opening_value_strategy.yaml
```
