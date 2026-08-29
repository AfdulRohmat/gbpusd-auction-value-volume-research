# Phase-3C Results — GBPUSD Market-Implied Fundamental Surprise (2024)

**Status:** Development gate failed  
**Branch:** `phase/03-fundamental-bias`  
**Development interval:** `[2024-01-01, 2025-01-01)`  
**Run ID:** `20240101_20250101_1eadffa7`  
**Definitions:** `llm/TECHNICAL_PLAN_GBPUSD_PHASE3C.md`

## 1. Decision

Do not promote this Phase-3C model to technical-entry or P&L construction.

The zero-cost market-implied surprise pipeline worked as designed and all data,
mapping, point-in-time, and score invariants passed. The registered directional
gate nevertheless failed:

- London three-session-day aligned return was only +2.59 pips, with a 97.5%
  cluster-bootstrap interval of `[-17.80, +22.25]`;
- New York was +4.53 pips, with `[-17.14, +27.16]`;
- both means were below the frozen +6-pip materiality requirement;
- both intervals crossed zero; and
- the one-day effect was negative in both sessions, failing the registered
  horizon-consistency rule.

The positive five-day point estimates are too imprecise to establish an edge.
The threshold, catalyst lifetime, catalyst set, and primary horizon must not be
changed using this 2024 result.

## 2. Data-source decision

Phase 3C was initially intended to use actual-minus-consensus release surprise.
That could not be implemented defensibly under the fixed zero-cost constraint:

- official statistical agencies publish actual releases but no historical
  economist-consensus series;
- FXStreet documents actual and consensus in its calendar API, but all
  endpoints require OAuth authentication; and
- Trading Economics documents historical actual/consensus and point-in-time
  calendar data, but calendar API access is part of its paid plan.

The frozen substitute was therefore explicitly named **market-implied
surprise**. It uses the event-day change in the official two-year yield as a
noisy proxy for the market's net interpretation. It is not presented as literal
actual-minus-consensus.

References:

- <https://docs.fxstreet.com/api/calendar/>
- <https://tradingeconomics.com/api/calendar.aspx>
- <https://tradingeconomics.com/api/pricing.aspx?source=basic-pricing-list>

## 3. Frozen model tested

The registered 2024 catalyst universe contained all BoE/Fed decisions,
headline/core CPI releases, and the symmetric earnings releases used in Phase
3B. Headline and core CPI rows at the same timestamp were one catalyst bundle.

For each catalyst:

```text
event-day shock = 2Y yield on the local release date
                  - preceding 2Y yield observation
```

At each London and New York open, the latest available shock remained active
for five yield observations. A newer catalyst replaced the older one.

```text
relative shock = active GBP shock - active USD shock

>= +5 bp -> long GBPUSD
<= -5 bp -> short GBPUSD
otherwise -> neutral
```

The primary outcome was the open-to-open return at the third subsequent open of
the same session. One- and five-session-day outcomes were registered
consistency checks. These are trading-session steps, so weekends and market
holidays are skipped.

## 4. Point-in-time data quality

- All 64 registered catalyst bundles mapped to both same-day and preceding
  official yield observations: 100% mapping coverage.
- The catalyst set contains 16 policy decisions and 48 macro bundles: 12 CPI
  and 12 earnings releases for each currency.
- All 518 Phase-2 eligible session opens received a valid repricing state: 100%
  incremental feature coverage.
- All unique-ID, date ordering, release/yield availability, signal age,
  relative-shock arithmetic, and bias-threshold checks passed.
- UK yield observations were usable only at noon London on the next observed
  UK business date; US Treasury observations were usable only at 18:00 New
  York on their observation date.
- A future shock sentinel cannot change an earlier bias in the automated tests.

The conservative availability rule means the release-day currency move is not
part of the tested return. The study asks whether observed repricing continues
after the official historical yield observation is available.

## 5. Bias and regime balance

| Session | Long opens | Neutral opens | Short opens | Directional regimes (long/short) |
|---|---:|---:|---:|---:|
| London | 63 | 150 | 46 | 39 (22/17) |
| New York | 60 | 154 | 45 | 45 (28/17) |

Both sessions exceeded the frozen minimum of 10 directional catalyst-state
regimes, and each direction exceeded three regimes. The failure is therefore
not caused by a sparse directional group.

## 6. Registered directional result

Positive aligned return means GBPUSD moved in the direction of the repricing
bias. Intervals use a cluster bootstrap over unique GBP/USD catalyst-state
pairs. The 97.5% level implements the registered Bonferroni family-wise 95%
rule across the two primary session tests.

| Session | Horizon | N | Regimes | Mean | Median | Hit rate | 97.5% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| London | 1d | 109 | 39 | -2.64 | -1.80 | 49.5% | [-11.44, +4.18] |
| London | 3d | 109 | 39 | +2.59 | +6.10 | 51.4% | [-17.80, +22.25] |
| London | 5d | 107 | 39 | +12.72 | +20.45 | 56.1% | [-19.18, +43.58] |
| New York | 1d | 105 | 45 | -3.25 | -2.60 | 43.8% | [-14.25, +5.97] |
| New York | 3d | 105 | 45 | +4.53 | +10.65 | 52.4% | [-17.14, +27.16] |
| New York | 5d | 103 | 45 | +15.14 | +11.90 | 54.4% | [-17.23, +46.69] |

The point estimates resemble delayed continuation—negative at one day and
positive by five days—but uncertainty is too wide to distinguish this pattern
from noise. Selecting five days after seeing this table would be post-result
horizon selection, so it is not allowed as a rescue.

## 7. Catalyst-pillar diagnostic

The three-day descriptive splits were:

| Session | Inflation | Labor | Mixed active catalysts | Policy |
|---|---:|---:|---:|---:|
| London | +2.73 | +30.78 | +6.36 | -50.62 |
| New York | -7.60 | +38.03 | +7.06 | -38.24 |

Labor looks positive and policy negative in this single development year, but
these are non-gating subgroup diagnostics with only 7–14 unique regimes per
cell and no multiplicity-adjusted inference. They do not justify removing
policy, using labor alone, or changing weights on 2024. Such a model would need
a new hypothesis and an untouched dataset.

## 8. Volume Profile interaction

At three days, outside-value reversion returns were descriptively:

| Session | Bias supports reversion | Bias opposes reversion | Supports minus opposes |
|---|---:|---:|---:|
| London | +9.77 | +16.08 | -6.31 |
| New York | +26.02 | +3.60 | +22.42 |

Both sides were positive in both sessions, and the signs of the incremental
comparison disagree between London and New York. This diagnostic therefore
does not show a stable fundamental enhancement to the Phase-2 Volume Profile
observation. It was not part of the Phase-3C gate.

## 9. Interpretation limits

- The sample is one development year with no untouched validation year.
- A daily yield move can include unrelated same-day macro, risk, supply, or
  geopolitical information; it does not causally isolate the named release.
- UK and US official two-year yield histories use different curve construction
  methods. Changes are compared in basis points, not yield levels, but this does
  not remove all measurement asymmetry.
- Repeated daily rows from one catalyst are dependent. The cluster bootstrap
  addresses this partially, but 39–45 regimes still produce wide intervals.
- The signal observes repricing after the market has reacted. It tests delayed
  continuation, not capture of the immediate release surprise.
- No spread-paid execution, slippage, entry, stop, exit, sizing, or P&L was
  simulated.

## 10. Reproducibility

Generated artifacts remain local and ignored by Git:

```text
data/processed/reports/phase3c/20240101_20250101_1eadffa7/
```

The manifest hashes the three ledgers, complete configuration, Phase-2 input,
Phase-3/3B results, frozen Phase-3C plan, Git state, runtime, row counts, and
gate decision. Re-run:

```bash
.venv/bin/python -m gbpusd_research run-phase3c \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-repricing config/fundamental_repricing.yaml
```

## 11. Research decision

Preserve Phase 3C as a failed development experiment. Based on 2024 alone,
there is no demonstrated edge from using post-release relative 2Y repricing as
a daily London/New York directional bias.

The honest next choice is either to stop the fundamental-bias line and return
to the stronger Phase-2 Volume Profile hypothesis, or obtain a genuinely
point-in-time consensus dataset plus a separate validation year before testing
a new actual-minus-consensus model.
