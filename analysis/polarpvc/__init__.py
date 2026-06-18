"""Offline ECG analysis pipeline for PolarPVC2.

Reads the raw ECG CSVs the Android app writes to Dropbox and labels each
beat (normal vs PVC) as accurately as possible, for offline PVC-burden
measurement. The on-phone classifier is for live display only; this is
the validated path.
"""

from .io import EcgRecord, load_ecg_csv
from .detect import detect_beats
from .features import extract_features, BeatFeatures
from .classify import classify_beats, ClassifierConfig, CLASSIFIER_VERSION
from .pipeline import label_record, label_csv
from .windows import compute_windows, Window
from .report import build_report

__all__ = [
    "EcgRecord",
    "load_ecg_csv",
    "detect_beats",
    "extract_features",
    "BeatFeatures",
    "classify_beats",
    "ClassifierConfig",
    "CLASSIFIER_VERSION",
    "label_record",
    "label_csv",
    "compute_windows",
    "Window",
    "build_report",
]
