# Phase 6 Results — GBPUSD Opening-Auction State Machine

**Execution date:** 2026-08-30  
**Evidence:** 2024 and 2025, reported separately  
**Research status:** exploratory only  
**Validation decision:** none; both years were inspected before Phase 6

## 1. Question tested

Phase 6 removed the low-frequency outside-value filter and classified every
complete London and New York opening after three M5 bars:

```text
directional efficient auction -> imbalance -> continuation
rotational auction            -> balance   -> fade toward fair value
```

The state, direction, next-open entry, structural stop, 0.1-pip adverse
slippage per side, and three exit variants were registered before inspecting
Phase-6 P&L.

## 2. Frequency result

The coverage objective was achieved.

| Year | Scheduled openings | Phase-1 eligible | Complete/traded | Trades/month |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 524 | 520 | 517 | 43.08 |
| 2025 | 522 | 518 | 518 | 43.17 |

Only three eligible 2024 New York events lacked the exact full-session M5
window. Every other complete eligible opening produced one trade per exit
variant. Phase 6 therefore resolves the Phase-4/5 sample-size bottleneck.

## 3. Combined London and New York result

| Year | Exit | Trades | Win rate | Avg win | Avg loss | Expectancy | Net R | Profit factor | Mean-R 95% CI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024 | Fixed 2R | 517 | 26.9% | +2.000R | -0.998R | -0.192R | -99.35R | 0.737 | [-0.284, -0.101] |
| 2024 | Session hold | 517 | 11.4% | +4.941R | -0.995R | -0.318R | -164.33R | 0.639 | [-0.421, -0.215] |
| 2024 | Trailing | 517 | 26.9% | +1.722R | -0.974R | -0.149R | -77.23R | 0.756 | [-0.254, -0.034] |
| 2025 | Fixed 2R | 518 | 28.0% | +1.982R | -1.000R | -0.165R | -85.66R | 0.770 | [-0.258, -0.078] |
| 2025 | Session hold | 518 | 10.6% | +5.036R | -0.999R | -0.358R | -185.30R | 0.599 | [-0.566, -0.141] |
| 2025 | Trailing | 518 | 27.6% | +1.624R | -0.977R | -0.142R | -73.55R | 0.759 | [-0.266, -0.007] |

All six combined intervals are below zero. None reaches the descriptive
`+0.10R` benchmark.

## 4. Why fixed 2R failed

The fixed target arithmetic behaved almost exactly like the simple 2R/-1R
model:

| Year | 2R targets | Stops | Session-cutoff exits | Target hit rate |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 139 | 377 | 1 | 26.9% |
| 2025 | 143 | 373 | 2 | 27.6% |

A clean 2R/-1R system needs approximately 33.3% wins before remaining costs.
The classifier produced only 27--28%. This is not a case where an acceptable
directional edge was hidden by a poor average payoff: nearly every fixed-2R
trade reached either its stop or target, and the target frequency itself was
too low.

## 5. Exit attribution

Trailing reduced the loss relative to fixed 2R by approximately:

- `+0.043R/trade` in 2024; and
- `+0.023R/trade` in 2025.

The paired session-level intervals generally crossed zero. The improvement was
too small to turn expectancy positive.

Holding until the session cutoff was worse than fixed 2R. The remaining trades
occasionally produced winners near +5R, but 88--89% hit their structural stop
before the cutoff. A long holding horizon could not repair the entry-direction
classification.

## 6. State diagnosis

No state was positive and stable across both calendar years.

The most tempting post-hoc subgroup was `imbalance_down` in 2025:

| Session | Exit | 2024 expectancy | 2025 expectancy |
| --- | --- | ---: | ---: |
| London | Trailing | -0.229R | +0.351R |
| New York | Trailing | -0.100R | +0.164R |

London 2025 contained only 44 observations and its positive result did not
replicate in 2024. Selecting it now would be outcome-driven filtering, not a
validated discovery.

`balance_low` was consistently weak, especially in New York, but removing it
after observing these results would likewise be a new hypothesis rather than a
repair of Phase 6.

## 7. Monthly outcome

| Year | Exit | Positive months | Mean monthly R | Worst month | Best month |
| --- | --- | ---: | ---: | ---: | ---: |
| 2024 | Fixed 2R | 1/12 | -8.28R | -19.00R | +3.00R |
| 2024 | Session hold | 0/12 | -13.69R | -27.24R | -2.52R |
| 2024 | Trailing | 3/12 | -6.44R | -17.26R | +9.82R |
| 2025 | Fixed 2R | 2/12 | -7.14R | -18.00R | +3.00R |
| 2025 | Session hold | 3/12 | -15.44R | -35.67R | +17.31R |
| 2025 | Trailing | 3/12 | -6.13R | -18.38R | +13.38R |

Increasing frequency amplified the negative expectancy; it did not diversify
it away.

## 8. Conclusion

Phase 6 answers two separate questions:

1. **Can the pipeline generate approximately 40 London/New York trades per
   month?** Yes: it generated approximately 43.
2. **Does this frozen 15-minute balance/imbalance mapping predict a profitable
   direction?** No. It was negative for every combined exit/year result.

The failure does not disprove Auction Market Theory. It rejects this specific
operationalization:

```text
15-minute efficiency + close location
    -> continuation for imbalance
    -> unconditional midpoint fade for balance
```

The next experiment must add one observable predictor at a time and must not
silently tune the `0.60/0.70` thresholds on these outcomes. Candidate context
features may include pre-session range location or displacement normalized by
pre-session volatility, but any such rule requires a separately frozen plan
and a new untouched forward period for a validation claim.

## 9. Reproducible artifacts

```text
data/processed/reports/phase6/20240101_20260101_0471cd14/
├── run_manifest.json
├── data_quality.json
├── auction_events.parquet
├── auction_trades.parquet
├── event_funnel.csv
├── variant_statistics.csv
├── state_statistics.csv
├── paired_exit_deltas.csv
├── monthly_statistics.csv
├── figures/
└── report.md
```

All execution invariants passed. The run contains 1,046 scheduled event rows,
1,035 executed opening trades per exit variant, and 3,105 trade-variant rows.
