"""Guard the cross-language parity fixture against silent Python-side drift.

The fixture in app/src/test/resources/parity/ is the contract the Kotlin
on-device port (EcgParityTest) is checked against. If the offline detector or
classifier changes, the fixture must be regenerated deliberately
(scripts/make_parity_fixture.py) and both sides re-tested. This test fails if
the checked-in labels no longer match what the current Python pipeline produces
for the checked-in ECG, so drift can't slip in unnoticed.

Run with the analysis venv:
    .venv/bin/python -m unittest discover -s tests
"""

import csv
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polarpvc import load_ecg_csv
from polarpvc.pipeline import label_record

_FIXTURE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "src", "test", "resources", "parity",
)


class TestParityFixture(unittest.TestCase):
    def test_fixture_matches_current_pipeline(self):
        ecg_path = os.path.join(_FIXTURE_DIR, "ecg_mock.csv")
        labels_path = os.path.join(_FIXTURE_DIR, "labels.csv")
        if not os.path.exists(ecg_path):
            self.skipTest("parity fixture not generated")

        record = load_ecg_csv(ecg_path)
        result = label_record(record)

        with open(labels_path) as fh:
            expected = list(csv.DictReader(fh))

        self.assertEqual(
            len(expected), len(result.labels),
            "beat count changed; regenerate the fixture",
        )
        for exp, f, lab in zip(expected, result.features, result.labels):
            self.assertEqual(int(exp["sample_index"]), f.index)
            self.assertEqual(int(exp["is_pvc"]), int(lab.is_pvc))
            is_art = 1 if "artifact" in lab.reasons.split("|") else 0
            self.assertEqual(int(exp["is_artifact"]), is_art)
            self.assertAlmostEqual(float(exp["template_corr"]), f.template_corr, places=4)
            self.assertAlmostEqual(float(exp["noise_ratio"]), f.noise_ratio, places=4)


if __name__ == "__main__":
    unittest.main()
