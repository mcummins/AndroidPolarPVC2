# PolarPVC2 — project context

DIY Holter monitor: Android app streams ECG from a Polar H10 chest strap,
detects PVCs in real time, logs raw ECG to hourly CSVs in the `Holter`
folder on the device (via SAF). Owner is wearing the strap and capturing
live data; the phone is connected to this machine via adb.

## Goal

Collect Holter-like data over several weeks; measure PVC burden to ~±1
percentage point; identify triggers (exercise-associated or not) and
day-to-day burden variability. Eventually: remote logging + remote analysis.

## Architecture (after foreground-service refactor, commit a37f452)

- `RecordingService.kt` — foreground service (connectedDevice type) owning
  PolarBleApi, PeakDetection, WriteData. Auto-reconnect with exponential
  backoff (2s→60s) + 60s watchdog; START_STICKY resume; partial wake lock;
  status notification. Prefs in shared file `polarpvc2` (PREF_FILE_PATH,
  PREF_DEVICE_ID, PREF_RECORDING).
- `MainActivity.kt` — bound UI only; implements `EcgListener` (service→UI
  callbacks, main thread); plotters (androidplot) stay here. Requests
  POST_NOTIFICATIONS + battery-optimization exemption.
- `PeakDetection.kt` — R-peak detection (smoothed squared diffs); calls
  nullable `EcgListener`, not the activity. PVC heuristics in
  `PVCClassifier.kt` (evidence scoring; unit tests in PVCClassifierTest).
- `WriteData.kt` — hourly `ecg_*.csv` (UTC `Z` filenames; `#` metadata
  line with device/app_version/sample_rate; nanosecond timestamp, integer
  µV); flushes every batch; checkError() detection; 30s reopen rate
  limit.
- `EventLog.kt` — per-session `events_*.csv` (connect/disconnect/reconnect/
  stream+write errors; quoted detail field) for offline coverage
  accounting. `clock_sync` event at each hourly file open anchors the H10
  sensor clock (ECG timestamps) to the phone clock (event timestamps).

Branch: `codex-layout-and-pvc-fixes`; remote `myfork` = mcummins fork,
`origin` = kbroman upstream. Build:
`ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew installDebug`.
Deployed and working on the phone as of 2026-06-11.

## Decisions made

- Remote storage target: **Dropbox** (project folder already syncs).
  Uploader should keep local files as source of truth, upload closed
  hourly files via WorkManager (gzip ~10x; ~340MB/day raw CSV).
- Burden measurement to ±1pp should be computed **offline** from raw ECG
  with a validated pipeline; on-phone classifier is for live display only.
  Burden error budget = coverage + classifier error; events CSV supplies
  the coverage denominator.

## Planned next (in rough priority order)

1. Beat/feature logging: second CSV per hour with beat timestamp, RR,
   classification, feature values (testStat, qrsWidth, ampRatio) — ground
   truth material for validating/refining the classifier.
2. Accelerometer streaming from the H10 (200Hz, or per-minute activity
   summaries) for exercise-trigger analysis; optional event-marker button
   (caffeine, lying down, etc.).
3. Dropbox uploader (WorkManager, gzip, retry; swappable backend).
4. Offline analysis pipeline (Python): beat detection + morphology-based
   PVC classification, coverage stats, hourly/daily burden reports;
   validate against hand-annotated sample.

## Known issues / notes

- `find_peaks` detects max one peak per half-batch (~0.28s) — may merge
  beats above ~160bpm (exercise); offline reanalysis mitigates.
- Polar H10 device ID default hardcoded `13DFA538`; actual ID persisted to
  prefs on connect.
- Each reinstall briefly interrupts capture; service auto-resumes.
