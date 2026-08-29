# Phase-3B Results — GBPUSD Relative Fundamental Strength (2024)

**Status:** Development gate failed  
**Branch:** `phase/03-fundamental-bias`  
**Development interval:** `[2024-01-01, 2025-01-01)`  
**Run ID:** `20240101_20250101_2f451553`  
**Definitions:** `llm/TECHNICAL_PLAN_GBPUSD_PHASE3B.md`

## 1. Decision

Do not proceed to technical-entry or P&L construction from this Phase-3B model.

The relative-strength pipeline and all point-in-time checks passed, but the
development gate failed for two independent reasons:

1. long bias occurred only 14 times at London and 12 times at New York, below
   the frozen 30-event minimum; and
2. no primary equal-weight contrast reached the registered materiality and
   confidence requirement.

The broader GBP-minus-USD concept was implemented as intended, but this exact
four-pillar formulation did not demonstrate a directional edge in 2024. This
result must not be repaired by changing weights or thresholds after seeing it.

## 2. Frozen model tested

For each session open, GBP and USD were scored independently:

```text
currency_score = policy + inflation + labor + yield_expectation
relative_score = GBP_score - USD_score

relative_score >= +2 -> long GBPUSD
relative_score <= -2 -> short GBPUSD
otherwise            -> neutral
```

Every primary pillar had weight one and a state in `{-1, 0, +1}`:

- policy: direction of the latest enacted rate change;
- inflation: combined release-to-release direction of headline and core CPI;
- labor: direction of annual regular/hourly earnings growth;
- yield expectation: 20-observation two-year yield momentum with a fixed
  10-basis-point deadband.

The `3-2-2-1` policy/inflation/labor/yield model was evaluated only as a frozen
sensitivity check. It did not participate in the gate.

## 3. Point-in-time data quality

- 524 session-opening rows were produced; 518 were Phase-2 eligible.
- All 518 eligible rows received all four currency pillars: 100% incremental
  feature coverage.
- The source ledgers contain 9 policy observations, 84 archived CPI/earnings
  observations, and 586 daily two-year yield observations.
- All source timestamps, prior-release joins, score arithmetic, threshold
  mappings, yield dates, and unique-ID checks passed.
- ONS market-sensitive releases were treated as available at 07:00 London;
  archived BLS releases at their printed 08:30 New York embargo time.
- A BoE daily yield curve was treated conservatively as available at noon
  London on the next observed UK business date. US Treasury yield data were
  treated as available at 18:00 New York on the observation date.
- A future macro release and a same-day yield sentinel cannot alter an earlier
  score in the automated tests.

The per-session design is active rather than cosmetic. Across 259 paired dates,
London and New York had different relative scores on 49 dates and different
final bias labels on 18 dates.

## 4. Primary bias balance

| Session | Long | Neutral | Short | Eligible |
|---|---:|---:|---:|---:|
| London | 14 | 166 | 79 | 259 |
| New York | 12 | 164 | 83 | 259 |

The neutral class represented roughly 63% to 64% of each session. Long bias
spanned three calendar months and short bias spanned nine, so month breadth
passed, but the long sample was too small for the frozen 30-event requirement.

## 5. Registered directional result

Positive bias-aligned return means GBPUSD moved in the direction selected by
the equal-weight relative score.

| Session | Horizon | Directional N | Mean | 95% bootstrap CI |
|---|---:|---:|---:|---:|
| London | 15m | 93 | -0.57 pip | [-2.57, +1.74] |
| London | 30m | 93 | -1.13 pip | [-3.45, +1.30] |
| London | 60m | 93 | -0.54 pip | [-3.81, +2.75] |
| London | 90m | 93 | +1.20 pip | [-2.21, +4.85] |
| New York | 15m | 95 | -0.85 pip | [-1.87, +0.15] |
| New York | 30m | 95 | -0.83 pip | [-2.38, +0.61] |
| New York | 60m | 95 | -1.17 pip | [-3.95, +1.60] |
| New York | 90m | 95 | -2.61 pip | [-5.46, +0.23] |

Every confidence interval includes zero. New York at 90 minutes exceeds two
pips in absolute magnitude, but in the wrong direction and without excluding
zero. It is not evidence for either the registered model or an inverted model.

The pooled directional `N` values do not cure the sparse long class: most
directional calls are short, so the minimum direction-group check still fails.

## 6. Volume Profile interaction

The registered comparison is:

```text
relative bias supports outside-value reversion
minus
relative bias opposes outside-value reversion
```

London had only 27 support and 27 opposition observations, below the 30-event
minimum. New York had 33 and 32 respectively, but its intervals all crossed
zero:

| Horizon | New York difference | 95% bootstrap CI |
|---:|---:|---:|
| 15m | -1.57 pip | [-4.08, +0.83] |
| 30m | -1.06 pip | [-4.12, +1.96] |
| 60m | -2.21 pip | [-7.95, +3.84] |
| 90m | -4.97 pip | [-11.10, +1.12] |

The relative fundamental bias therefore did not strengthen the Phase-2
outside-value reversion observation in this sample.

## 7. Incremental value over policy-only V1

The paired comparison below subtracts the policy-only V1 aligned return from
the Phase-3B primary aligned return when both models issued a directional call.

| Session | Horizon | Paired N | Difference | 95% bootstrap CI |
|---|---:|---:|---:|---:|
| London | 15m | 93 | -0.69 pip | [-4.06, +3.49] |
| London | 30m | 93 | -1.78 pip | [-5.40, +2.32] |
| London | 60m | 93 | -0.87 pip | [-5.85, +4.76] |
| London | 90m | 93 | +1.84 pip | [-3.56, +7.71] |
| New York | 15m | 94 | -0.94 pip | [-1.95, +0.06] |
| New York | 30m | 94 | -0.40 pip | [-2.21, +1.41] |
| New York | 60m | 94 | -0.76 pip | [-4.48, +2.83] |
| New York | 90m | 94 | -1.72 pip | [-5.71, +2.28] |

No interval excludes zero. Adding inflation, earnings, and yield expectation
did not show reliable incremental session-direction information over the
already failed policy-only baseline.

## 8. Impact-weight sensitivity

The equal-weight and `3-2-2-1` models selected the same final direction in 458
of 518 eligible rows (88.4%). The remaining 60 rows were directional/neutral
mismatches; there were no opposite-direction cases.

Weighted directional means were not better:

| Session | 15m | 30m | 60m | 90m |
|---|---:|---:|---:|---:|
| London | -0.34 | -1.06 | -1.02 | -0.01 |
| New York | -1.17 | -0.85 | -1.45 | -2.68 |

These are descriptive sensitivity statistics without a gate role. They do not
justify promoting the impact weights to the primary model.

## 9. Interpretation limits

- The development sample is one year and contains no out-of-year validation.
- Bias states persist between monthly releases, so rows remain time-clustered
  regimes rather than 518 independent macro observations.
- Policy is represented by the latest rate-change direction, not market surprise
  or central-bank guidance.
- Inflation and earnings use release-time levels and changes, but no historical
  consensus surprise because a clean free point-in-time consensus archive was
  not available.
- UK labor uses earnings only. Unemployment was excluded symmetrically because
  the UK moved between experimental/adjusted and reweighted survey estimates
  around February 2024.
- The UK yield is a BoE estimated nominal spot curve, while the US yield is an
  official Treasury par curve. The model compares their momentum directions,
  not their levels; BoE archive estimates can also be revised.
- No spread-paid execution, slippage, entry, stop, exit, sizing, or P&L has been
  simulated.

## 10. Reproducibility

Generated artifacts remain local and ignored by Git:

```text
data/processed/reports/phase3b/20240101_20250101_2f451553/
```

The manifest hashes all three reference ledgers, complete configuration,
Phase-2 input manifest, Phase-3 V1 result document, Git state, runtime, row
counts, and gate decision. Re-run:

```bash
.venv/bin/python -m gbpusd_research run-phase3b \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-strength config/fundamental_strength.yaml
```

## 11. Research decision

Preserve Phase 3 V1 and Phase 3B as failed development results. Do not move to
Phase 4 from these data and do not select alternative weights on 2024.

If the broader hypothesis is revisited, the defensible next experiment is a
new preregistered model based on **release surprise versus contemporaneous
consensus and central-bank guidance**, followed by testing on a separate clean
year. That would be a new research model, not a repair of this one.
