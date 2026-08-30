# Phase 8 Results — GBPUSD Balance-Boundary Strategy

**Execution date:** 2026-08-30
**Evidence:** GBPUSD M5 for 2024 and 2025, reported separately
**Research status:** exploratory development and replication only
**Validation decision:** none; both histories were already inspected

## 1. Question tested

Phase 8 tested whether the boundary of a confirmed Phase-7 balance episode acts
as executable support/resistance during the London and New York opening window.

The frozen state machine selected the first of two mutually exclusive paths:

```text
boundary rejection + close back inside
    -> next-bar rotation toward the frozen midpoint

two closes outside + confirmed imbalance
    -> next-bar continuation after accepted breakout
```

All entries and exits used bid/ask prices, 0.1-pip adverse slippage per side,
stop-first same-bar ambiguity, and registered session cutoffs.

## 2. Frequency and retention

| Year | Scheduled openings | Observable balance | Valid triggers/trades | Retention from balance | Trades/month |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | 524 | 398 | 184 | 46.2% | 15.33 |
| 2025 | 522 | 408 | 246 | 60.3% | 20.50 |

The balance-at-opening filter preserved approximately 33–34 candidate contexts
per month, but only 15–21 produced an executable rejection or acceptance trigger.
It therefore did not preserve the earlier objective of approximately 40 trades
per month.

Across both years, the event router selected:

- 294 boundary rejections; and
- 136 accepted breakouts.

There were also 53 ambiguous bars that touched/rejected both boundaries and were
conservatively excluded.

## 3. Primary hypothesis: rejection to midpoint

| Year | Trades | Win rate | Mean winner | Mean loser | Expectancy | Net R | Profit factor | Mean-R 95% CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | 123 | 21.1% | +2.465R | -1.000R | -0.267R | -32.90R | 0.661 | [-0.516, -0.081] |
| 2025 | 171 | 22.2% | +2.690R | -1.001R | -0.180R | -30.83R | 0.768 | [-0.420, +0.092] |

The average payoff was not the main problem. A winner earned approximately
2.5–2.7R, but the midpoint was reached too rarely. With those realized winner
sizes and approximately one-R losses, break-even required roughly 27–29% wins;
the strategy achieved only 21–22%.

The failure mechanism was immediate adverse continuation:

| Diagnostic | 2024 | 2025 |
| --- | ---: | ---: |
| Stop exits | 78.9% | 77.8% |
| Median initial risk | 2.8 pips | 2.9 pips |
| Stops within first 5 minutes, conditional on stop | 69.1% | 66.2% |
| Stops within first 10 minutes, conditional on stop | 83.5% | 83.5% |
| Median MFE before exit | 0.47R | 0.55R |
| Median MAE before exit | 1.38R | 1.35R |

A single close back inside the range therefore did not establish sufficient
acceptance back into balance for this one-pip-outside stop. The result rejects
this exact rejection/stop combination. It does not by itself prove whether the
boundary is meaningless or whether the registered stop was too close; changing
the buffer now would be a new, outcome-informed hypothesis.

Post-hoc direction decomposition also showed that long rotations were near flat
while short rotations were strongly negative in both years. This is diagnostic
only. Selecting longs after inspecting the result would not be a valid repair.

## 4. Comparator: accepted breakout

| Year | Exit | Trades | Win rate | Expectancy | Net R | Profit factor | Mean-R 95% CI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | Fixed 2R | 61 | 24.6% | -0.272R | -16.61R | 0.603 | [-0.565, +0.045] |
| 2024 | Trailing | 61 | 31.1% | -0.289R | -17.64R | 0.521 | [-0.498, -0.062] |
| 2025 | Fixed 2R | 75 | 40.0% | +0.102R | +7.63R | 1.176 | [-0.208, +0.401] |
| 2025 | Trailing | 75 | 48.0% | +0.152R | +11.39R | 1.341 | [-0.163, +0.526] |

Acceptance continuation improved markedly in 2025, but the sign reversed from
2024 and every fixed-2R interval crossed zero. The 2024 trailing interval was
entirely negative. The positive 2025 outcome is therefore regime-dependent
exploratory evidence, not a replicated edge.

The fixed 2R target-hit rate rose from 19.7% in 2024 to 29.3% in 2025, while stop
frequency fell from 65.6% to 56.0%. That instability, rather than the exit
choice, explains most of the annual difference.

## 5. Combined route

| Year | Route | Trades/month | Win rate | Expectancy | Net R | Profit factor | Mean-R 95% CI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | Rotation + fixed breakout | 15.33 | 22.3% | -0.269R | -49.51R | 0.643 | [-0.449, -0.118] |
| 2024 | Rotation + trailing breakout | 15.33 | 24.5% | -0.275R | -50.55R | 0.622 | [-0.445, -0.125] |
| 2025 | Rotation + fixed breakout | 20.50 | 27.6% | -0.094R | -23.20R | 0.869 | [-0.308, +0.143] |
| 2025 | Rotation + trailing breakout | 20.50 | 30.1% | -0.079R | -19.44R | 0.883 | [-0.295, +0.154] |

Both combined routes were negative in both years. Their 2024 intervals were
fully below zero. Increasing the 2025 acceptance contribution reduced the loss,
but could not overcome the negative rotation component.

Only 2 of 12 combined-route months were positive in 2024 and 5 of 12 in 2025.
The outcome was not a small positive edge obscured by monthly variance.

## 6. Did trailing repair the accepted breakout?

No stable paired improvement appeared. Mean trailing-minus-fixed differences
ranged from `-0.077R` to `+0.114R` across session/year cells, and every paired
interval crossed zero. The median difference was zero in all four cells.

Trailing altered the distribution but was not the source of the 2025 accepted-
breakout improvement.

## 7. What Phase 8 establishes

The test separates three claims:

1. **Can confirmed balance provide enough opening contexts?** Yes, approximately
   33–34 per month.
2. **Does one close back inside its boundary predict rotation to midpoint with a
   one-pip structural buffer?** No. The rule was negative in both years and most
   stops occurred almost immediately.
3. **Does two-close, confirmed acceptance predict continuation?** Not stably.
   It was negative in 2024 and positive in 2025.

Consequently, the Phase-7 boundary cannot yet be treated as proven tradeable
support/resistance. It remains a useful regime descriptor, but this frozen
execution mapping has no replicated edge.

The responsible next step is not to optimize the one-pip stop or select the 2025
winner. A separate diagnostic phase could measure the complete post-rejection
path—maximum excursion beyond the boundary, time to midpoint, and failed-break
depth—without placing trades. Any revised acceptance or stop rule must then be
frozen and evaluated on new forward evidence.

## 8. Reproducible artifacts

```text
data/processed/reports/phase8/20240101_20260101_abab7dca/
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

The run processed 1,046 scheduled openings, 566 setup-exit trade rows, and 1,426
registered analysis-route rows. All 24 execution invariants passed.
