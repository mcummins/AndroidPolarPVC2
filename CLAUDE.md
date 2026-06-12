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
- `DropboxSync.kt` / `UploadWorker.kt` — remote logging. One-time OAuth
  PKCE link ("Link Dropbox" in UI; refresh token in prefs). WorkManager
  worker gzips closed csv files to the app's Dropbox folder
  (`/Apps/<app>/`), skipping files recorded as open in prefs
  (PREF_OPEN_ECG_FILE / PREF_OPEN_EVENTS_FILE); uploaded names tracked in
  prefs, WriteMode.OVERWRITE for idempotency. Upload verified by
  content_hash comparison (`DropboxContentHash.kt`); local files deleted
  3 days after verified upload (RETENTION_MS), re-checking remote
  existence just before deletion (re-uploads if remote copy vanished).
  Triggers: hourly rotation, recording stop, service destroy, 6h periodic
  backstop; network constraint + exponential backoff handles offline.
  App key in `local.properties` (`dropbox.app.key`, not committed) →
  BuildConfig + manifest placeholder for the auth redirect scheme.

Branch: `codex-layout-and-pvc-fixes`; remote `myfork` = mcummins fork,
`origin` = kbroman upstream. Build:
`ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew installDebug`.
Deployed and working on the phone as of 2026-06-11.

## Decisions made

- Remote storage target: **Dropbox** (project folder already syncs).
  Closed hourly files upload via WorkManager (gzip ~10x; ~340MB/day raw
  CSV). Dropbox is the long-term store: local copies are kept only 3
  days after hash-verified upload (phone storage), then deleted.
- Burden measurement to ±1pp should be computed **offline** from raw ECG
  with a validated pipeline; on-phone classifier is for live display only.
  Burden error budget = coverage + classifier error; events CSV supplies
  the coverage denominator.

## Planned next (in rough priority order)

0. TOMORROW (needs real data + sensor): capture a real recording,
   hand-annotate a sample, re-tune `ClassifierConfig` offline. Then port
   the offline labeler to the phone for accurate live stats — whole-signal
   Pan–Tompkins on a 3–5s-delayed sliding buffer (fixes the half-batch
   merge limit) + running normal-beat template correlation (the key
   discriminating feature, self-adapts to electrode drift). Reuse the
   offline feature defs + tuned thresholds; add a mock-data parity test so
   Kotlin and Python labels can't silently drift. Offline pipeline stays
   the validated burden source; phone number is a live preview. ~1 day.
1. Beat/feature logging: second CSV per hour with beat timestamp, RR,
   classification, feature values (testStat, qrsWidth, ampRatio) — ground
   truth material for validating/refining the classifier.
2. Accelerometer streaming from the H10 (200Hz, or per-minute activity
   summaries) for exercise-trigger analysis; optional event-marker button
   (caffeine, lying down, etc.).
3. Offline analysis pipeline (Python): PVC labeling done (see below);
   still to do — coverage stats (from events CSV) and hourly/daily burden
   reports, and re-tuning ClassifierConfig against a hand-annotated real
   sample once one exists.

## Offline analysis (`analysis/`)

Python package `polarpvc` (numpy/scipy; venv in `analysis/.venv`,
gitignored). Stage 1 = PVC labeling, done and validated:
- `io.py` loads the app CSV (ns→s, µV→mV), splits at dropout gaps into
  segments. `detect.py` = whole-signal Pan–Tompkins R-peak detection per
  segment (no half-batch limit). `features.py` = RR timing vs robust local
  baseline, QRS width (energy envelope), amplitude/polarity, and
  correlation to a template of the patient's own normal beats.
  `classify.py` = PVC if aberrant complex (wide AND low template corr) AND
  abnormal timing (premature OR compensatory), or strongly aberrant alone;
  thresholds in `ClassifierConfig`. `pipeline.py` writes a per-beat CSV.
- `mockdata.py` generates synthetic ECG (app format) with ground-truth
  PVC labels for validation without real data. `scripts/`: `make_mock.py`,
  `label_pvcs.py`, `evaluate_mock.py`. Tests: `analysis/tests/` (unittest).
- Validation: ~1.0 sensitivity/precision on clean synthetic signals,
  within ±1pp burden under 4× noise. Re-tune `ClassifierConfig` against a
  hand-annotated real sample before trusting on real ECG.

Stage 2 = exploration report: `windows.py` pools labeled beats into 30s
windows (burden, mean HR, time-of-day, date, rhythm-state fractions) and
detects bigeminy/trigeminy runs; `report.py` writes a self-contained
interactive HTML page (no CDN — embedded JSON + canvas JS, opens offline
from Dropbox) with burden-vs-HR, vs-time-of-day, vs-day (each with a
per-window-dots / binned toggle), and a rhythm-states-over-time chart.
`scripts/make_report.py` (real files/globs/dirs) and `scripts/make_demo.py`
(synthetic multi-day demo via `mockdata.generate_session`/`diurnal_blocks`,
with HR-dependent burden + bigeminy bouts). Burden uses recorded windows
only (gaps excluded, not zero-filled); times local.

## Known issues / notes

- `find_peaks` detects max one peak per half-batch (~0.28s) — may merge
  beats above ~160bpm (exercise); offline reanalysis mitigates (the
  offline detector has no such limit).
- Polar H10 device ID default hardcoded `13DFA538`; actual ID persisted to
  prefs on connect.
- Each reinstall briefly interrupts capture; service auto-resumes.
- Dropbox setup (one-time): create a scoped app (app-folder access) at
  console.dropbox.com, enable `files.content.write` + `files.metadata.read`
  on its Permissions tab, put its key in `local.properties` as
  `dropbox.app.key=...`, rebuild/install, tap "Link Dropbox" in the app.
  Uploads land in `/Apps/<app name>/`. Done 2026-06-12; account linked.
- If "Link Dropbox" fails with a generic "Couldn't connect to Dropbox"
  dialog: that's the installed Dropbox app's app-to-app auth swallowing
  the real error (e.g. scope not enabled in the console). Temporarily
  disable the Dropbox app (`adb shell pm disable-user --user 0
  com.dropbox.android`, re-enable with `pm enable`) to force the browser
  flow, which shows the actual error. The SDK has no flag to skip
  app-to-app auth.
