# GBPUSD Session Research

Reproducible research pipeline for testing whether GBPUSD movement around the
London and New York FX opens differs from matched non-opening periods.

The current implementation covers Phase 1 session-opening research, Phase 2
point-in-time VWAP/previous-value-state research, Phase 3 point-in-time
monetary-policy bias research, and the Phase 3B GBP-minus-USD relative
fundamental-strength extension. Phase 3C adds a zero-cost market-implied
surprise proxy from event-day two-year yield repricing and tests future
same-session opens. Phase 4 adds the first frozen, cost-aware P&L simulation:
opening-only previous-value reversion with 2024 development context and an
untouched 2025 validation sample.

## Requirements

- macOS or another Unix-like environment
- Python 3.12 or 3.13

On this machine, use `/opt/homebrew/bin/python3.12` rather than the Apple system
Python 3.9.

## Setup

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Validate the checked-in configuration:

```bash
.venv/bin/python -m gbpusd_research config-check
```

Print the normalized configuration:

```bash
.venv/bin/python -m gbpusd_research show-config
```

Download the free HistData tick archive for January 2024, then build one UTC
day of normalized ticks and M5 bars:

```bash
.venv/bin/python -m gbpusd_research download --date 2024-01-02
.venv/bin/python -m gbpusd_research build-m5 --date 2024-01-02
.venv/bin/python -m gbpusd_research tag-sessions --date 2024-01-02
```

Build and run the complete January smoke study from the cached monthly archive:

```bash
.venv/bin/python -m gbpusd_research build-range
.venv/bin/python -m gbpusd_research run-phase1
```

Run the revised 2024 development workflow:

```bash
.venv/bin/python -m gbpusd_research download-range \
  --research config/research_2024.yaml
.venv/bin/python -m gbpusd_research build-range \
  --research config/research_2024.yaml
.venv/bin/python -m gbpusd_research run-phase1 \
  --research config/research_2024.yaml
```

Rebuild the M5 files with exact tick-activity moments, then run Phase 2:

```bash
.venv/bin/python -m gbpusd_research build-range \
  --research config/research_2024.yaml --force
.venv/bin/python -m gbpusd_research run-phase2 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml
```

Run the Phase-3 policy-bias study from the matching Phase-2 artifact:

```bash
.venv/bin/python -m gbpusd_research run-phase3 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental config/fundamental_bias.yaml
```

Run the separately registered Phase-3B relative-strength extension:

```bash
.venv/bin/python -m gbpusd_research run-phase3b \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-strength config/fundamental_strength.yaml
```

Run the separately registered Phase-3C market-implied repricing study:

```bash
.venv/bin/python -m gbpusd_research run-phase3c \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental-repricing config/fundamental_repricing.yaml
```

Build the zero-cost 2025 validation data and run the frozen Phase-4 strategy:

```bash
.venv/bin/python -m gbpusd_research download-range \
  --research config/research_2025.yaml
.venv/bin/python -m gbpusd_research build-range \
  --research config/research_2025.yaml
.venv/bin/python -m gbpusd_research run-phase1 \
  --research config/research_2025.yaml
.venv/bin/python -m gbpusd_research run-phase2 \
  --research config/research_2025.yaml \
  --value-state config/value_state.yaml
.venv/bin/python -m gbpusd_research run-phase4 \
  --research config/research_2024.yaml \
  --validation-research config/research_2025.yaml \
  --value-state config/value_state.yaml \
  --opening-value config/opening_value_strategy.yaml
```

The range downloader retries failed requests and continues with later months.
Re-running it validates and reuses archives already present in the cache.

Run tests and static checks:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

## Configuration

- `config/research.yaml` contains instrument, source-data, quality, and study
  settings for the January 2024 smoke test.
- `config/research_2024.yaml` is the active development specification covering
  `[2024-01-01, 2025-01-01)`.
- `config/research_2025.yaml` is the untouched Phase-4 validation specification
  covering `[2025-01-01, 2026-01-01)`.
- `config/research_2023_2024.yaml` is retained for audit of the rejected
  two-year run; incomplete New York coverage makes 2023 unsuitable.
- `config/sessions.yaml` contains timezone-aware session and control settings.
- `config/value_state.yaml` freezes the Phase-2 VWAP, Volume Profile,
  classification, and development-gate definitions.
- `config/fundamental_bias.yaml` freezes the Phase-3 policy-bias lookback,
  analysis horizons, and development gate.
- `config/fundamental_strength.yaml` freezes the Phase-3B equal-weight primary
  score, impact-weighted sensitivity model, point-in-time data paths, and gate.
- `config/fundamental_repricing.yaml` freezes the Phase-3C event-day 2Y shock,
  five-observation signal lifetime, five-basis-point bias threshold,
  same-session horizons, cluster bootstrap, and development gate.
- `config/opening_value_strategy.yaml` freezes the Phase-4 entry deadline,
  bid/ask execution, stop/target, slippage stress, and validation gate.
- Fixed controls are registered at 04:00 London local time and 12:00 New York
  local time. Matched controls use deterministic sampling away from both opens.
- Date ranges use a half-open interval: `start` is included and `end` is
  excluded.
- All stored timestamps will be UTC. Session opens are defined in local civil
  time and converted with IANA timezone rules.
- HistData Generic ASCII timestamps are fixed EST (`UTC-05:00`) without DST;
  normalization uses that fixed offset before converting to UTC.
- HistData's volume field is not used. Tick count is the explicit V1 activity
  proxy for both VWAP and Volume Profile; it is not centralized traded volume.

To validate the active development configuration without downloading data:

```bash
.venv/bin/python -m gbpusd_research config-check \
  --research config/research_2024.yaml
```

## Research documentation

- `llm/PRD_GBPUSD_Session_Value_Fundamental_Research.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE1.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE2.md`
- `llm/PHASE2_RESULTS_2024.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE3.md`
- `llm/PHASE3_RESULTS_2024.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE3B.md`
- `llm/PHASE3B_RESULTS_2024.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE3C.md`
- `llm/PHASE3C_RESULTS_2024.md`
- `llm/PHASE3_WRAP_UP.md`
- `llm/TECHNICAL_PLAN_GBPUSD_PHASE4.md`
- `llm/PHASE4_RESULTS_2024_2025.md`
