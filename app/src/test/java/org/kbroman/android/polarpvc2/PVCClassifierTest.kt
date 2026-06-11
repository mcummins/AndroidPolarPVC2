package org.kbroman.android.polarpvc2

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PVCClassifierTest {
    @Test
    fun calcTestStat_returnsZeroForEmptyInput() {
        assertEquals(0.0, PVCClassifier.calcTestStat(emptyList()), 0.0)
    }

    @Test
    fun calcTestStat_usesMidRangeThreshold() {
        val stat = PVCClassifier.calcTestStat(listOf(1.0, 0.5, -0.5, -1.0))
        assertEquals(0.5, stat, 0.0001)
    }

    // --- Single signal is never enough ---

    @Test
    fun rejectsSingleSignal_morphologyOnly() {
        // test stat > 0.65 but nothing else (nadir=2, no RR, no width, no amp)
        assertFalse(PVCClassifier.looksLikePVC(2, 0.9474))
    }

    @Test
    fun rejectsSingleSignal_amplitudeOnly() {
        // abnormal amplitude but test stat low, nadir early, no RR
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.50, amplitudeRatio = 0.60
            )
        )
    }

    @Test
    fun rejectsSingleSignal_prematureOnly() {
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.50,
                rrBeforeSec = 0.60, baselineRrSec = 0.80
            )
        )
    }

    @Test
    fun rejectsWeakStatistic() {
        // stat 0.63 < threshold 0.65, only 1 signal (nadir=6) -> rejected
        assertFalse(PVCClassifier.looksLikePVC(6, 0.6316))
    }

    // --- Two signals are enough ---

    @Test
    fun acceptsMorphologyPlusLateNadir() {
        // test stat > 0.65 + nadir >= 5 = 2 signals
        assertTrue(PVCClassifier.looksLikePVC(6, 0.9474))
    }

    @Test
    fun rejectsAbnormalAmplitudeWithOnlyPrematureTiming() {
        // amplitude + premature timing is only 1 signal now (premature not standalone)
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.50,
                rrBeforeSec = 0.60, baselineRrSec = 0.80,
                amplitudeRatio = 0.60
            )
        )
    }

    @Test
    fun acceptsAbnormalAmplitudePlusMorphology() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.70, amplitudeRatio = 0.60
            )
        )
    }

    @Test
    fun acceptsWideQrsPlusMorphology() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.70, qrsWidth = 16
            )
        )
    }

    @Test
    fun acceptsAbnormalAmplitudePlusWideQrs() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.50,
                amplitudeRatio = 0.60, qrsWidth = 16
            )
        )
    }

    // --- Compensatory pause counts as 2, enough on its own ---

    @Test
    fun acceptsCompensatoryPauseAlone() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2, pvcTestStat = 0.50,
                rrBeforeSec = 0.52, rrAfterSec = 0.79, baselineRrSec = 0.68
            )
        )
    }

    @Test
    fun acceptsCompensatoryPauseWithBorderlineNadir() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 4, pvcTestStat = 0.9474,
                rrBeforeSec = 0.62, rrAfterSec = 0.99, baselineRrSec = 0.80
            )
        )
    }

    // --- Negative index always rejected ---

    @Test
    fun rejectsNegativeNadirIndex() {
        assertFalse(PVCClassifier.looksLikePVC(-1, 0.95, qrsWidth = 20, amplitudeRatio = 0.50))
    }

    // --- Normal amplitude near baseline is not flagged ---

    @Test
    fun rejectsNormalAmplitudeWithEarlyNadir() {
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 1, pvcTestStat = 0.70, amplitudeRatio = 1.05
            )
        )
    }

    // --- hasAbnormalAmplitude ---

    @Test
    fun hasAbnormalAmplitude_detectsLowAmplitude() {
        assertTrue(PVCClassifier.hasAbnormalAmplitude(0.60))
    }

    @Test
    fun hasAbnormalAmplitude_detectsHighAmplitude() {
        assertTrue(PVCClassifier.hasAbnormalAmplitude(1.50))
    }

    @Test
    fun hasAbnormalAmplitude_rejectsNormalAmplitude() {
        assertFalse(PVCClassifier.hasAbnormalAmplitude(1.10))
    }

    @Test
    fun hasAbnormalAmplitude_rejectsNull() {
        assertFalse(PVCClassifier.hasAbnormalAmplitude(null))
    }

    // --- isPremature ---

    @Test
    fun isPremature_detectsShortRR() {
        assertTrue(PVCClassifier.isPremature(0.60, 0.80))
    }

    @Test
    fun isPremature_rejectsNormalRR() {
        assertFalse(PVCClassifier.isPremature(0.78, 0.80))
    }

    @Test
    fun isPremature_rejectsNull() {
        assertFalse(PVCClassifier.isPremature(null, 0.80))
    }
}
