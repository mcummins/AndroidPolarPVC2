package org.kbroman.android.polarpvc2

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.kbroman.android.polarpvc2.ecg.EcgFeatures.BeatFeatures

/**
 * Unit tests for the rewritten classifier (mirrors the offline classify.py
 * decision rule + tuned thresholds). End-to-end agreement with the Python
 * pipeline is covered by EcgParityTest; these pin the individual rule branches.
 */
class PVCClassifierTest {

    private fun beat(
        corr: Double,
        prematurity: Double = Double.NaN,
        compensatory: Double = Double.NaN,
        widthMs: Double = 100.0,
        ampMv: Double = 1.0,
        noise: Double = 1.0,
    ) = BeatFeatures(
        index = 0, t = 0.0, rrBefore = Double.NaN, rrAfter = Double.NaN,
        baselineRr = Double.NaN, prematurity = prematurity, compensatory = compensatory,
        qrsWidthMs = widthMs, amplitudeMv = ampMv, polarity = 1,
        templateCorr = corr, noiseRatio = noise,
    )

    private fun isPvc(f: BeatFeatures) = PVCClassifier.classifyBeat(f).isPvc
    private fun isArtifact(f: BeatFeatures) = PVCClassifier.classifyBeat(f).isArtifact

    // --- the main rule: aberrant morphology + abnormal timing ---

    @Test fun aberrantAndPremature_isPvc() {
        assertTrue(isPvc(beat(corr = 0.1, prematurity = 0.6)))
    }

    @Test fun aberrantButNormalTiming_notPvc() {
        // low corr but coupling normal and not strong enough to stand alone
        assertFalse(isPvc(beat(corr = 0.7, prematurity = 1.0)))
    }

    @Test fun normalMorphologyButPremature_notPvc() {
        // premature sinus / APC: good correlation -> not a PVC
        assertFalse(isPvc(beat(corr = 0.97, prematurity = 0.6)))
    }

    @Test fun aberrantWithCompensatoryPause_isPvc() {
        assertTrue(isPvc(beat(corr = 0.5, prematurity = 0.7, compensatory = 2.0)))
    }

    // --- strong (morphology-alone) path ---

    @Test fun stronglyAberrant_standsAlone() {
        // very low corr, coupling near-normal (interpolated PVC)
        assertTrue(isPvc(beat(corr = -0.6, prematurity = 0.95)))
    }

    @Test fun stronglyAberrantButLateBeat_notPvc() {
        // grossly aberrant but arriving after a gap -> noise, not a PVC
        assertFalse(isPvc(beat(corr = -0.3, prematurity = 2.1)))
    }

    // --- signal-quality vetoes -> artifact (excluded) ---

    @Test fun grossAmplitude_isArtifact() {
        val f = beat(corr = -0.5, prematurity = 0.6, ampMv = 13.0)
        assertTrue(isArtifact(f)); assertFalse(isPvc(f))
    }

    @Test fun saturatedWidth_isArtifact() {
        val f = beat(corr = -0.5, prematurity = 0.6, widthMs = 200.0)
        assertTrue(isArtifact(f)); assertFalse(isPvc(f))
    }

    @Test fun degenerateNarrowWidth_isArtifact() {
        val f = beat(corr = -0.5, prematurity = 0.6, widthMs = 0.0)
        assertTrue(isArtifact(f)); assertFalse(isPvc(f))
    }

    @Test fun noisyStretch_isArtifact() {
        val f = beat(corr = -0.5, prematurity = 0.6, noise = 5.0)
        assertTrue(isArtifact(f)); assertFalse(isPvc(f))
    }

    @Test fun widePvcWithinBounds_notArtifact() {
        // a genuinely wide PVC (140 ms) must NOT be vetoed by the width gate
        val f = beat(corr = 0.1, prematurity = 0.6, widthMs = 140.0)
        assertFalse(isArtifact(f)); assertTrue(isPvc(f))
    }

    // --- reasons string ---

    @Test fun reasons_listFiredCriteria() {
        val lab = PVCClassifier.classifyBeat(beat(corr = 0.1, prematurity = 0.6, widthMs = 120.0))
        assertTrue(lab.reasons.contains("aberrant"))
        assertTrue(lab.reasons.contains("premature"))
        assertTrue(lab.reasons.contains("wide"))
    }

    @Test fun classifyBeats_mapsAll() {
        val out = PVCClassifier.classifyBeats(listOf(beat(0.1, 0.6), beat(0.99, 1.0)))
        assertEquals(2, out.size)
        assertTrue(out[0].isPvc)
        assertFalse(out[1].isPvc)
    }
}
