# Phase 7 Results — GBPUSD Auction-State Taxonomy

**Execution date:** 2026-08-30
**Evidence:** continuous GBPUSD M5 data for 2024 and 2025, reported separately
**Research status:** exploratory taxonomy only
**Trading decision:** none; Phase 7 contains no entry, exit, or P&L

## 1. Question studied

Phase 7 stepped back from the Phase-6 trading rule and classified the continuous
market into two independent dimensions:

```text
auction state:    balance | imbalance_up | imbalance_down
activity regime:  quiet | normal | active
```

The goal was to describe how long balance and imbalance last, when confirmed
balance-to-imbalance transitions occur, and which observable signatures precede
them. The taxonomy used frozen 30-minute rolling-price geometry and required two
consecutive qualifying windows before a new state became observable.

This is one operational definition of Auction Market Theory, not a claim that
the labels are the market's latent or uniquely correct state.

## 2. Primary result: a stable episode structure

| Year | State | Episodes | Median duration | Interquartile range | Median width |
| --- | --- | ---: | ---: | ---: | ---: |
| 2024 | Balance | 1,976 | 105 min | 45–200 min | 14.30 pips |
| 2024 | Imbalance down | 1,037 | 30 min | 20–50 min | 9.10 pips |
| 2024 | Imbalance up | 1,028 | 30 min | 20–50 min | 9.03 pips |
| 2025 | Balance | 1,968 | 105 min | 50–200 min | 17.95 pips |
| 2025 | Imbalance down | 994 | 30 min | 20–45 min | 10.98 pips |
| 2025 | Imbalance up | 1,053 | 30 min | 20–50 min | 11.05 pips |

The duration structure replicated unusually closely across the two years:
confirmed balance lasted a median 105 minutes, while either imbalance direction
lasted a median 30 minutes. Width changed with the 2025 price/activity regime,
but the duration quantiles did not materially change.

The live, point-in-time state was balance for 78.36% of observable M5 windows in
2024 and 79.19% in 2025. Imbalance was split almost evenly by direction. This
large balance share partly reflects the registered hysteresis rule: uncertain
raw-transition windows preserve the most recently confirmed state until a new
state receives two-window confirmation.

## 3. Balance and activity are not synonyms

The independent activity axis behaved as intended:

- 306 of 1,976 balance episodes in 2024 and 251 of 1,968 in 2025 were dominated
  by active conditions; balance can therefore be wide, volatile two-sided chop.
- 300 of 2,065 imbalance episodes in 2024 and 248 of 2,047 in 2025 were dominated
  by quiet conditions; imbalance can therefore be a slow directional grind.
- Active balance was wider than quiet balance in both years: median 21.03 versus
  10.40 pips in 2024, and 26.25 versus 12.80 pips in 2025.

This supports the conceptual correction behind Phase 7: opening volatility and
auction imbalance cannot be treated as the same variable.

## 4. What happens after each state

| Year | From state | Most common next state | Conditional probability |
| --- | --- | --- | ---: |
| 2024 | Imbalance down | Balance | 94.1% |
| 2024 | Imbalance up | Balance | 93.4% |
| 2025 | Imbalance down | Balance | 94.0% |
| 2025 | Imbalance up | Balance | 94.5% |

An imbalance episode normally resolved back into balance. Direct flips from
one imbalance direction to the other occurred in only 5.5–6.6% of completed
imbalance transitions.

Balance broke upward and downward at effectively equal rates:

- 2024: 49.8% up and 50.2% down;
- 2025: 51.5% up and 48.5% down.

The balance label alone therefore contains no unconditional directional bias.

## 5. Balance age is not a countdown clock

Raw conditional transition probability rises across wider age bins because an
older bin contains more minutes in which a transition can happen. After
normalizing by actual balance exposure, the estimated transition rate remained
roughly flat:

| Year | Lowest rate | Highest rate | Mean rate |
| --- | ---: | ---: | ---: |
| 2024 | 0.162 | 0.221 | 0.198 |
| 2025 | 0.158 | 0.216 | 0.196 |

Rates are balance-to-imbalance transitions per 30 minutes of balance exposure.
The similar means and narrow ranges do not support the simple hypothesis that a
long-lived balance becomes progressively more likely to break merely because
it is old.

## 6. Transition signatures

For confirmed balance-to-imbalance transitions:

| Signature | 2024 share | 2025 share |
| --- | ---: | ---: |
| Directional repricing inside prior balance boundary | 60.4–60.6% | 58.6–59.0% |
| Boundary break without registered activity burst | 33.3–33.5% | 34.5% |
| Boundary break with registered activity burst | 6.0–6.1% | 6.5–6.9% |

Only about 39–41% closed beyond the full completed balance-episode boundary at
the candidate transition, and only about 8–9% had an activity burst whether or
not a boundary break occurred. Most state changes were consequently detected as
directional repricing within a broader persistent balance range, not as a
textbook range breakout with an activity explosion.

Prior boundary interaction did not produce a simple pressure signal. Median
upper and lower test counts were approximately symmetric, and the eventual
break side had more tests in only 26–30% of transitions. These are descriptive
associations, not proof that tests or bursts cause a transition.

## 7. When transitions occurred

Hourly candidate-start counts were normalized by balance exposure. The two
annual clock profiles were only weakly stable: rank correlation was
approximately 0.25 in London-local time and 0.10 in New-York-local time. A
candidate start is assigned retrospectively after the second qualifying window;
it is episode taxonomy, not a real-time signal timestamp.

There were two useful descriptive patterns:

- 09:00 London was elevated in both years at 46.50 and 47.04 transitions per
  100 balance-hours, but it was not the registered 08:00 London open and was not
  unique among high-rate hours.
- The late New York afternoon was consistently quieter, especially 18:00 local
  at 24.21 and 21.40 transitions per 100 balance-hours.

These patterns are hypotheses for future research, not tradable time filters.
Twenty-four hourly comparisons have already been inspected and no multiplicity
adjustment or untouched sample remains in Phase 7.

## 8. Did London or New York open trigger imbalance?

Among events whose last available confirmed state was balance, the 60-minute
transition probabilities were:

| Year | Session | Open | Fixed control | Matched control |
| --- | --- | ---: | ---: | ---: |
| 2024 | London | 33.5% | 36.7% | 35.1% |
| 2024 | New York | 33.3% | 39.0% | 35.4% |
| 2025 | London | 33.3% | 39.9% | 32.3% |
| 2025 | New York | 39.3% | 36.1% | 32.6% |

The sign was not stable. New York 2025 was 6.7 percentage points above its
matched control, but New York 2024 was 2.0 points below; London was also mixed.
The Wilson intervals overlapped, and results across the 15-, 30-, 60-, and
90-minute horizons did not show a consistent advantage across sessions and
controls. London at 90 minutes was slightly positive against both controls in
both years, but the effect was small and absent at its shorter horizons.

Therefore the result from earlier phases is now more precise:

```text
session opening -> higher movement/activity in some measurements
session opening -/-> reliably higher balance-to-imbalance transition probability
```

Opening volatility exists in the data, but it is not equivalent to this frozen
auction-state transition.

## 9. Phase-7 conclusion

Phase 7 did find a repeatable descriptive structure:

1. balance dominates the observable timeline and lasts much longer than
   imbalance;
2. imbalance usually rotates back into balance;
3. state and activity must remain separate; and
4. the episode-duration structure is stable across 2024 and 2025.

It did not identify a trade edge or a singular trigger. Neither balance age,
opening time, activity burst, nor repeated boundary tests independently explains
enough transitions to justify an entry rule from this evidence.

The next research phase, if pursued, should treat Phase 7 as a regime-labeling
foundation. A candidate predictor must be registered separately, tested against
the base transition rate, and validated on new forward data. Optimizing these
thresholds on 2024–2025 would turn the taxonomy into an in-sample fit and should
not be presented as validation.

## 10. Reproducible artifacts

```text
data/processed/reports/phase7/20240101_20260101_d7c65ef5/
├── run_manifest.json
├── data_quality.json
├── state_timeline.parquet
├── state_episodes.parquet
├── state_transitions.parquet
├── state_occupancy.csv
├── episode_statistics.csv
├── transition_matrix.csv
├── balance_hazard.csv
├── transitions_by_clock.csv
├── transition_signatures.csv
├── transition_antecedents.csv
├── opening_control_events.parquet
├── opening_control_statistics.csv
├── opening_control_differences.csv
├── figures/
└── report.md
```

The run processed 149,526 M5 timeline rows, 8,056 confirmed episodes, 7,925
adjacent-episode transitions, and 3,138 opening/control events. All registered
execution invariants passed.
