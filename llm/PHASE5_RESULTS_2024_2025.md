# Phase 5 Results — GBPUSD Opening-Auction Ablation

**Execution date:** 2026-08-30  
**Evidence:** 2024 and 2025, reported separately  
**Research status:** exploratory diagnosis only  
**Validation decision:** none; both years have already been inspected

## 1. Objective

Phase 5 decomposed the failed Phase-4 rule to identify which component reduced
frequency and which component changed expectancy. Ten variants were registered
before their new outcomes were inspected. All variants used executable bid/ask
prices and 0.1-pip adverse slippage per side.

Variants without stops are return diagnostics, not live risk models.

## 2. Opportunity count

| Year | Session | Scheduled | Outside-value | Re-entry by 30m | Favorable POC | Phase-4 trades |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | London | 262 | 143 | 19 | 19 | 19 |
| 2024 | New York | 262 | 164 | 22 | 22 | 22 |
| 2025 | London | 261 | 151 | 22 | 21 | 21 |
| 2025 | New York | 261 | 165 | 17 | 14 | 14 |

Across both sessions, approximately 43–44 openings were observed per month and
about 26 opened outside value. Requiring a complete return across VAH/VAL by
minute 30 reduced that population to approximately three signals per month.

## 3. Immediate outside-value fade

The first ablation entered against the outside state directly at the executable
session open and exited at a fixed horizon. It therefore restored 143–165
observations per year/session.

### New York

| Variant | 2024 mean pips [95% CI] | 2025 mean pips [95% CI] |
| --- | ---: | ---: |
| Open to +30m | +0.169 [-0.903, +1.223] | -0.921 [-2.801, +1.144] |
| Open to +60m | +2.763 [-0.118, +6.667] | +0.499 [-1.969, +3.284] |
| Open to +90m | +2.432 [-1.344, +7.394] | -0.415 [-2.756, +2.368] |
| Nearest boundary or +90m | +1.355 [-0.789, +3.999] | -0.469 [-2.669, +2.192] |
| POC or +90m | +1.851 [-1.140, +5.858] | -0.513 [-2.652, +2.062] |

The larger population solves the frequency problem, but not the evidence
problem. Every interval crosses zero and the signs are not stable across years.
The no-stop 90-minute variants also produced drawdowns of roughly 193 pips in
2024 and 310–324 pips in 2025.

London showed the same lack of stable positive expectancy and was generally
weaker in 2025.

## 4. What the re-entry filter actually selects

For events that eventually produced a Phase-4 signal, entering at the original
session open and holding to minute 90 produced:

| Session | 2024 | 2025 |
| --- | ---: | ---: |
| London | +10.511 pips [6.464, 14.495] | +4.300 pips [-4.594, 12.290] |
| New York | +13.150 pips [4.752, 19.419] | +4.112 pips [-2.523, 10.431] |

Thus the re-entry cohort is indeed a strongly mean-reverting subgroup from the
opening price, especially in 2024. The problem is that membership in this group
is only known after price has already returned to the value boundary. Using it
to justify an entry at the earlier session open would be lookahead.

## 5. Marginal cost of waiting for confirmation

The cleanest ablation compared the same signal events, same direction, and same
minute-90 exit. Only entry timing changed from session open to the next M5 open
after confirmed re-entry.

| Year | Session | Events | Confirmation delta | 95% month-cluster CI | Events improved |
| --- | --- | ---: | ---: | ---: | ---: |
| 2024 | London | 19 | -8.105 pips | [-9.491, -5.900] | 0% |
| 2024 | New York | 22 | -6.455 pips | [-7.592, -5.410] | 0% |
| 2025 | London | 22 | -8.550 pips | [-10.938, -6.143] | 0% |
| 2025 | New York | 17 | -7.365 pips | [-8.740, -5.778] | 0% |

This result is mechanically consistent: the confirmation requires price to
complete the favorable outside-to-boundary leg before entry. It therefore gives
away approximately 6–9 pips on every selected event. The filter identifies
mean reversion after much of that mean reversion has already occurred.

After confirmation, the New York timeout result changed from +6.695 pips in
2024 to -3.253 pips in 2025. The post-confirmation leg was not stable.

## 6. Remaining components

### Favorable-POC filter

- It removed no events in either 2024 session.
- In 2025 it retained 21/22 London and 14/17 New York signals.
- Its mean effect was small and inconsistent: -0.360 pips for London and
  +1.689 pips for New York in 2025.

There is no evidence that this filter caused the main sample or expectancy
loss.

### POC target

On the same favorable New York events, replacing the minute-90 exit with the
POC target changed mean P&L by:

- 2024: `-3.191` pips;
- 2025: `-5.021` pips.

Both estimates are underpowered and their cluster intervals include zero, but
the target did not improve the mean in either year. It frequently capped the
remaining winner while only occasionally avoiding a later reversal.

### Excursion stop

Adding the Phase-4 stop after the POC/no-stop variant changed mean New York P&L
by `-0.998` pips in 2024 and `+2.939` pips in 2025. The intervals were wide and
the sign reversed. The stop limited individual downside but did not provide a
stable expectancy contribution.

## 7. Diagnosis

The Phase-4 bottleneck is now localized:

```text
Outside-value event
    → potentially useful mean-reversion move begins at the open
    → full boundary re-entry occurs in only 10–15% of candidates
    → confirmation becomes observable after 6–9 pips have been surrendered
    → POC target and stop do not restore stable expectancy
```

The re-entry event is a useful **outcome label**, but not yet an actionable
opening-time predictor. It tells us which auctions rejected the outside price
after the rejection has happened.

Conversely, entering every outside open restores approximately 26 opportunities
per month across both sessions, but the resulting fade expectancy is unstable
and every annual New York confidence interval includes zero.

## 8. Research conclusion

Phase 5 does not uncover a tradable edge. It does provide a higher-information
direction for future research:

> The next problem is not to loosen the 30-minute confirmation threshold. It is
> to find information observable at or shortly after the open that distinguishes
> future rejection from continued price discovery before the outside-to-value
> move is consumed.

That is an Auction Market Theory state-classification problem. Any proposed
predictor must be tested incrementally rather than stacked as assumed
confluence. Neither 2024 nor 2025 may be reused as untouched validation.

## 9. Reproducible artifacts

```text
data/processed/reports/phase5/20240101_20260101_f4ea6de7/
├── run_manifest.json
├── data_quality.json
├── ablation_results.parquet
├── retention_funnel.csv
├── variant_statistics.csv
├── selection_effects.csv
├── paired_deltas.csv
├── monthly_statistics.csv
├── figures/
└── report.md
```

All execution invariants passed. The run contains 3,503 event-variant results,
40 variant summaries, eight selection contrasts, and 12 paired component
contrasts.
