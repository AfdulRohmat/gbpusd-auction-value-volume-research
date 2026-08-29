# Phase 1 Results — GBPUSD Session Event Study, 2023–2024

**Branch:** `phase/01-event-study`  
**Run:** `20230101_20250101_5da6674a`  
**2024 development run:** `20240101_20250101_de41ae56`
**Decision:** Quality gate failed; do not advance to Phase 2 from this run

## Decision update: 2024 development sample

The rejected two-year run remains preserved as audit evidence. The active
development specification now excludes 2023 and uses the half-open interval
`[2024-01-01, 2025-01-01)` via `config/research_2024.yaml`.

For 2024, both London and New York have 260/262 eligible openings (99.2%). The
60-minute mean range differences are:

| Control | London | New York |
|---|---:|---:|
| Fixed | +10.60 pips | +2.85 pips |
| Matched | +8.19 pips | +9.09 pips |

All four 95% confidence intervals are above zero. The 2024 run is therefore
accepted as a **development pass** and may guide exploratory Phase 2 work. It is
not an out-of-sample validation, a robustness claim, or evidence of strategy
profitability. Parameters selected during development must be frozen before a
new validation year is evaluated.

## What was executed

- Downloaded and checksum-validated 24 monthly HistData GBPUSD tick archives.
- Converted 46,955,766 quotes into 139,566 M5 bars.
- Generated 1,044 scheduled London/New York opening events, fixed controls, and
  deterministic matched controls.
- Ran 10,000-resample paired bootstraps for 5/15/30/60/90-minute horizons.
- Generated the complete report contract, including machine-readable quality,
  exclusions, statistics, four figures, and a run manifest.

Raw ZIP size is approximately 289 MB and monthly M5 Parquet size is
approximately 14 MB. Generated data and reports remain excluded from Git.

## Quality result

All source-month schema checks passed: no crossed quotes, nonpositive prices,
required-field nulls, or OHLC violations were found. This does not imply
complete temporal coverage.

| Year | Session | Scheduled | Eligible | Coverage |
|---:|---|---:|---:|---:|
| 2023 | London | 260 | 255 | 98.1% |
| 2023 | New York | 260 | 143 | 55.0% |
| 2024 | London | 262 | 260 | 99.2% |
| 2024 | New York | 262 | 260 | 99.2% |

The registered minimum is 90% in every session-year. The run therefore fails.

Inspection of the raw CSV confirms that HistData's 2023 tick archives omit
entire hourly blocks from late February through July. This is not caused by the
fixed EST-to-UTC conversion or by M5 resampling. It destroys most New York
opening windows in March–June and makes the surviving 2023 New York sample
selection-biased.

## Qualified primary estimates

These estimates are retained for audit and pipeline verification only. They are
not accepted as Phase-1 validation evidence.

| Control | Session | Eligible pairs | Mean 60m range difference | 95% CI |
|---|---|---:|---:|---:|
| Fixed | London | 515 | +12.98 pips | [12.01, 13.98] |
| Fixed | New York | 401 | +5.03 pips | [3.08, 7.03] |
| Matched | London | 428 | +9.85 pips | [8.62, 11.12] |
| Matched | New York | 377 | +11.89 pips | [10.08, 13.77] |

The direction is encouraging, and 2024 alone has excellent opening coverage,
but positive estimates cannot override the pre-registered data-quality gate.

## Required next decision

Use one consistent replacement source for the incomplete 2023 period and rerun
the frozen endpoint/control definitions. HistData M1 is free and may be useful
for diagnosing the tick export, but its Generic ASCII schema is bid-only OHLC
and has no ask/spread; mixing it into the current tick study would change the
methodology and must be treated as a newly registered data specification.

Provider references:

- <https://www.histdata.com/download-free-forex-data/>
- <https://www.histdata.com/f-a-q/data-files-detailed-specification/>
