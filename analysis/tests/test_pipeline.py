"""End-to-end tests for the offline PVC labeler against synthetic truth.

Run with the analysis venv:
    .venv/bin/python -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from polarpvc import label_record, label_csv, load_ecg_csv
from polarpvc.io import EcgRecord, split_segments
from polarpvc import mockdata
from polarpvc.evaluate import score_detection, score_pvc
from polarpvc.windows import (
    classify_rhythm, compute_windows,
    STATE_BIGEMINY, STATE_TRIGEMINY, STATE_NORMAL,
)


def _mock_record(**kw):
    rec = mockdata.generate(**kw)
    ecg = EcgRecord(t=rec.t, mv=rec.mv, fs=rec.fs)
    ecg.segments = split_segments(rec.t, rec.mv, rec.fs)
    return rec, ecg


class TestDetection(unittest.TestCase):
    def test_beat_detection_recall(self):
        rec, ecg = _mock_record(duration_s=180, mean_hr=70, pvc_burden=0.1, seed=2)
        result = label_record(ecg)
        m = score_detection(rec.beat_times, ecg.t[result.beats])
        self.assertGreaterEqual(m.sensitivity, 0.98)
        self.assertGreaterEqual(m.precision, 0.98)

    def test_motion_artifact_does_not_kill_detection(self):
        # A large motion-artifact spike (~13 mV, as seen in a real field
        # recording) must not poison the adaptive threshold and suppress
        # detection for the rest of the record. Regression for the global-EWMA
        # threshold that left only the first ~12 min of a 43-min recording
        # detected.
        rec, ecg = _mock_record(duration_s=300, mean_hr=70, pvc_burden=0.1, seed=5)
        i = int(100 * ecg.fs)
        ecg.mv[i : i + 20] += np.hanning(20) * 13.0
        ecg.segments = split_segments(ecg.t, ecg.mv, ecg.fs)
        result = label_record(ecg)
        m = score_detection(rec.beat_times, ecg.t[result.beats])
        self.assertGreaterEqual(m.sensitivity, 0.98)
        # detection must continue well past the artifact, not stop at it
        last_beat_s = ecg.t[result.beats[-1]] - ecg.t[0]
        self.assertGreater(last_beat_s, 290.0)

    def test_artifact_not_labelled_pvc(self):
        # the artifact deflection itself must not be counted as a PVC
        rec, ecg = _mock_record(duration_s=120, mean_hr=70, pvc_burden=0.0, seed=11)
        i = int(60 * ecg.fs)
        ecg.mv[i : i + 20] += np.hanning(20) * 13.0
        ecg.segments = split_segments(ecg.t, ecg.mv, ecg.fs)
        result = label_record(ecg)
        self.assertEqual(sum(1 for l in result.labels if l.is_pvc), 0)


class TestPvcClassification(unittest.TestCase):
    def test_clean_signal_perfect(self):
        rec, ecg = _mock_record(duration_s=300, mean_hr=65, pvc_burden=0.08, seed=3)
        result = label_record(ecg)
        pred = np.array([l.t for l in result.labels if l.is_pvc])
        m = score_pvc(rec.pvc_times, pred)
        self.assertEqual(m.fn, 0)
        self.assertEqual(m.fp, 0)

    def test_noisy_signal_within_burden_budget(self):
        rec, ecg = _mock_record(
            duration_s=300, mean_hr=85, pvc_burden=0.12, seed=7,
            noise_mv=0.10, baseline_wander_mv=0.20,
        )
        result = label_record(ecg)
        true_burden = 100.0 * len(rec.pvc_times) / len(rec.beat_times)
        # the project's accuracy goal is +/-1 percentage point
        self.assertLess(abs(result.burden_pct - true_burden), 1.0)

    def test_no_pvc_recording_has_no_false_positives(self):
        rec, ecg = _mock_record(duration_s=180, mean_hr=60, pvc_burden=0.0, seed=8)
        result = label_record(ecg)
        self.assertEqual(result.n_pvc, 0)


class TestIoFormat(unittest.TestCase):
    def test_roundtrip_app_csv_format(self):
        rec = mockdata.generate(duration_s=60, mean_hr=70, pvc_burden=0.1, seed=4)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ecg_mock.csv")
            mockdata.write_ecg_csv(rec, path)
            loaded = load_ecg_csv(path)
            self.assertEqual(loaded.metadata.get("ecg_units"), "uV")
            self.assertEqual(loaded.fs, 130.0)
            # microvolt integers -> millivolt floats, within rounding
            self.assertLess(abs(loaded.mv.max() - rec.mv.max()), 0.01)

    def test_gap_splits_into_segments(self):
        # two 10 s blocks separated by a 5 s dropout
        fs = 130.0
        t1 = np.arange(0, 10, 1 / fs)
        t2 = np.arange(15, 25, 1 / fs)
        t = np.concatenate([t1, t2])
        mv = np.zeros_like(t)
        segs = split_segments(t, mv, fs)
        self.assertEqual(len(segs), 2)


class TestRhythm(unittest.TestCase):
    def test_bigeminy_run_detected(self):
        # N P N P ... -> bigeminy run spans from the first PVC to the last,
        # so the leading normal (index 0) is not part of the run
        seq = [i % 2 == 1 for i in range(40)]
        states = classify_rhythm(seq)
        self.assertEqual(states.count(STATE_BIGEMINY), 39)
        self.assertEqual(states[0], STATE_NORMAL)

    def test_trigeminy_run_detected(self):
        # N N P repeated -> trigeminy
        seq = [(i % 3 == 2) for i in range(45)]
        states = classify_rhythm(seq)
        self.assertGreater(states.count(STATE_TRIGEMINY), 30)

    def test_isolated_pvcs_not_called_bigeminy(self):
        seq = [False] * 50
        seq[10] = seq[30] = True  # two isolated PVCs, far apart
        states = classify_rhythm(seq)
        self.assertEqual(states.count(STATE_BIGEMINY), 0)
        self.assertEqual(states.count(STATE_TRIGEMINY), 0)


class TestWindows(unittest.TestCase):
    def test_window_burden_and_hr(self):
        rec, ecg = _mock_record(duration_s=300, mean_hr=60, pvc_burden=0.10, seed=2)
        result = label_record(ecg)
        wins = compute_windows(result.features, result.labels, window_s=30.0)
        self.assertGreater(len(wins), 0)
        # window-weighted burden should match the overall burden closely
        nb = sum(w.n_beats for w in wins)
        npv = sum(w.n_pvc for w in wins)
        overall = 100.0 * npv / nb
        self.assertLess(abs(overall - result.burden_pct), 2.0)
        for w in wins:
            self.assertTrue(40 < w.mean_hr < 120)
            self.assertTrue(0 <= w.hour_of_day < 24)


if __name__ == "__main__":
    unittest.main()
