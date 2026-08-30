# Phase 9 — Preparing Exness GBPUSD Tick Data

Do not add raw tick files to Git. `data/raw/` and generated `data/processed/`
outputs are already ignored.

## Preferred route: exact connected MT5 feed

1. Open the Exness MT5 terminal and log into the Raw Spread account.
2. Open a GBPUSD chart and note whether its exact Market Watch name is
   `GBPUSD` or `GBPUSD-r`.
3. In MT5, choose **File → Open Data Folder**.
4. Copy `tools/mt5/ExportExnessInfoTicks.mq5` from this repository into
   `MQL5/Scripts/` in the MT5 data folder.
5. Open MetaEditor, compile the script, return to MT5, and run it on the GBPUSD
   chart with Algo Trading enabled.
6. Keep the frozen inputs:

   ```text
   InpStartUtc  = 2024.01.01 00:00:00
   InpEndUtc    = 2026.08.01 00:00:00
   InpChunkDays = 1
   ```

7. Wait for `EXPORT_COMPLETE` in the Experts log. Any `EXPORT_FAILED` or
   `EXPORT_ABORTED` message means the dataset is incomplete and must not be
   used.
8. Copy every generated `phase9_GBPUSD*_YYYY-MM.csv` from `MQL5/Files/` into:

   ```text
   data/raw/exness/
   ```

The exporter requests Bid/Ask information ticks in daily chunks and writes one
CSV per month. It keeps memory bounded and records `flags`, `last`, `volume`, and
`volume_real` for audit. Phase 9 uses each row only as one quote update.

MetaTrader may need time to synchronize old ticks from the broker. If a history
timeout occurs, leave the terminal connected and rerun. The importer will refuse
overlapping/out-of-order sources rather than silently combining them.

## Fallback route: Exness Personal Area archive

If the connected MT5 server does not expose the full range:

1. Log into Exness Personal Area.
2. Open the top-right `?` menu, then **Tools & Services → Tick History**.
3. Select the Raw Spread account type/feed and GBPUSD.
4. Download annual 2024 and 2025 archives.
5. Download monthly January through July 2026 archives.
6. Put the original ZIP files, without extracting them, into
   `data/raw/exness/`.

This fallback is broker-specific but not exact-account-specific. Exness states
that its published Raw Spread tick history is drawn from MT4 Real 9 and that
minor cross-server latency differences can occur. The final report will disclose
that limitation.

## Validate and build

From the repository root:

```bash
.venv/bin/python -m gbpusd_research inspect-exness
.venv/bin/python -m gbpusd_research build-exness-m5
```

The build streams directly from CSV/ZIP input, derives quote-direction counts,
and writes compact monthly M5 Parquet files under `data/processed/exness/`.
Large ZIP contents do not need to be extracted.

Before modeling, verify that the build manifest reports:

- first timestamp no later than `2024-01-01`;
- last timestamp at or after the end of `2026-07-31` UTC;
- zero crossed quotes;
- all 31 expected calendar-month outputs; and
- no incomplete or overlapping archive error.
