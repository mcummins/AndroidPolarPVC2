# PolarPVC2 offline analysis

Python pipeline that reads the raw ECG CSVs the Android app writes (and
syncs to Dropbox) and labels each beat as normal or PVC. This is the
**validated** PVC path; the on-phone classifier is for live display only.
Burden to ~±1 percentage point is measured here, offline.

Two stages so far:
- **PVC labeling** — ECG CSV → a per-beat table with timing/morphology
  features and a PVC label.
- **Exploration report** — pools labeled beats into 30-second windows and
  builds a self-contained interactive HTML page (burden vs heart rate,
  time of day, day, and rhythm-state clustering).

## Setup

```bash
cd analysis
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

Label a recording (one `ecg_*.csv` from the app):

```bash
.venv/bin/python scripts/label_pvcs.py path/to/ecg_2026-06-11_193000Z.csv -o beats.csv
```

`beats.csv` has one row per detected beat:

| column | meaning |
|---|---|
| `sample_index`, `time_s` | beat location (sample, epoch seconds) |
| `rr_before_s`, `rr_after_s`, `baseline_rr_s` | RR timing and local baseline |
| `prematurity` | `rr_before / baseline_rr` (<1 = early) |
| `compensatory` | `(rr_before + rr_after) / baseline_rr` (~2 = full pause) |
| `qrs_width_ms` | QRS duration from the energy envelope |
| `amplitude_mv`, `polarity` | R-peak deflection and sign |
| `template_corr` | correlation to the patient's normal beat (low = aberrant) |
| `is_pvc`, `reasons` | label and which criteria fired |

## How labeling works

1. **Load** (`io.py`) — parse the app format (ns timestamps, integer µV),
   split into gap-free segments at dropouts.
2. **Detect** (`detect.py`) — Pan–Tompkins R-peak detection over the whole
   signal (no half-batch limit, so closely spaced/PVC beats resolve),
   per segment.
3. **Features** (`features.py`) — RR timing vs a robust local baseline; QRS
   width; amplitude/polarity; and correlation to a template built from the
   patient's own normal beats.
4. **Classify** (`classify.py`) — a beat is a PVC if it has an aberrant
   complex (wide **and** poor template correlation) together with abnormal
   timing (premature **or** compensatory pause); an extremely aberrant
   complex passes on morphology alone. Thresholds live in
   `ClassifierConfig`.

## Validation with synthetic data

No real recording is needed to develop or regression-test the labeler.
`mockdata.py` generates realistic ECG with planted PVCs (premature, wide,
aberrant, compensatory pause) plus noise and baseline wander, with
ground-truth labels.

```bash
# generate a mock ecg_*.csv + ground-truth file
.venv/bin/python scripts/make_mock.py --out-dir /tmp/mock --minutes 10 --burden 0.08

# score the labeler across heart rates, burdens, and noise levels
.venv/bin/python scripts/evaluate_mock.py

# unit tests
.venv/bin/python -m unittest discover -s tests
```

`evaluate_mock.py` reports beat-detection and PVC sensitivity/precision/F1
and burden error per scenario. On the current synthetic suite the labeler
is at or near 1.0 on clean signals and stays within the ±1pp burden budget
under 4× noise. These numbers validate the algorithm and plumbing on
idealized signals — real ECG will be noisier, so the thresholds in
`ClassifierConfig` should be re-tuned against a hand-annotated real sample
once one is available.

## Exploration report

Build an interactive HTML report from one or more recordings:

```bash
.venv/bin/python scripts/make_report.py 'path/to/ecg_*.csv' -o pvc_report.html
open pvc_report.html
```

The page is self-contained (data embedded, charts drawn with vanilla JS on
canvas — no internet needed, so it works from a Dropbox folder). Charts,
each with a per-window-dots / binned toggle where noted:

1. **Burden vs heart rate** — exercise association (kbroman saw ~10%→25%
   above 80 bpm).
2. **Burden vs time of day** — diurnal pattern, windows pooled across days.
3. **Burden by day** — day-to-day variability.
4. **Rhythm states over time** — fraction of beats in sustained bigeminy /
   trigeminy runs vs normal, per hour (PVCs cluster).

Burden is computed only over recorded windows (gaps from disconnections
are excluded, not counted as zero burden). Times are local.

To see it working before real data exists, generate a synthetic multi-day
recording (HR-dependent burden, exercise bouts, bigeminy/trigeminy bouts)
and report:

```bash
.venv/bin/python scripts/make_demo.py --days 3 --out-dir /tmp/pvcdemo
open /tmp/pvcdemo/pvc_report.html
```
