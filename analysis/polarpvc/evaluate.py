"""Scoring the labeler against ground truth.

Matches detected beats/PVCs to ground-truth beats/PVCs by time (within a
tolerance) and reports detection and classification metrics. Used by the
mock-data validation harness and the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_TOLERANCE_S = 0.10


@dataclass
class Metrics:
    n_truth: int
    n_pred: int
    tp: int
    fp: int
    fn: int

    @property
    def sensitivity(self) -> float:  # recall
        denom = self.tp + self.fn
        return self.tp / denom if denom else float("nan")

    @property
    def precision(self) -> float:  # ppv
        denom = self.tp + self.fp
        return self.tp / denom if denom else float("nan")

    @property
    def f1(self) -> float:
        p, r = self.precision, self.sensitivity
        if not (p and r) or np.isnan(p) or np.isnan(r) or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)

    def as_dict(self) -> dict:
        return {
            "n_truth": self.n_truth,
            "n_pred": self.n_pred,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "sensitivity": round(self.sensitivity, 4),
            "precision": round(self.precision, 4),
            "f1": round(self.f1, 4),
        }


def _match(truth_times: np.ndarray, pred_times: np.ndarray, tol: float):
    """Greedy nearest-time one-to-one matching within tol seconds."""
    truth_times = np.sort(np.asarray(truth_times, dtype=float))
    pred_times = np.sort(np.asarray(pred_times, dtype=float))
    used_pred = np.zeros(pred_times.size, dtype=bool)
    tp = 0
    for tt in truth_times:
        if pred_times.size == 0:
            break
        j = int(np.argmin(np.abs(pred_times - tt)))
        # find nearest unused within tolerance
        order = np.argsort(np.abs(pred_times - tt))
        for j in order:
            if abs(pred_times[j] - tt) > tol:
                break
            if not used_pred[j]:
                used_pred[j] = True
                tp += 1
                break
    fn = truth_times.size - tp
    fp = int((~used_pred).sum())
    return tp, fp, fn


def score_detection(
    truth_beat_times: np.ndarray,
    pred_beat_times: np.ndarray,
    tol: float = DEFAULT_TOLERANCE_S,
) -> Metrics:
    tp, fp, fn = _match(truth_beat_times, pred_beat_times, tol)
    return Metrics(
        n_truth=len(truth_beat_times),
        n_pred=len(pred_beat_times),
        tp=tp,
        fp=fp,
        fn=fn,
    )


def score_pvc(
    truth_pvc_times: np.ndarray,
    pred_pvc_times: np.ndarray,
    tol: float = DEFAULT_TOLERANCE_S,
) -> Metrics:
    tp, fp, fn = _match(truth_pvc_times, pred_pvc_times, tol)
    return Metrics(
        n_truth=len(truth_pvc_times),
        n_pred=len(pred_pvc_times),
        tp=tp,
        fp=fp,
        fn=fn,
    )
