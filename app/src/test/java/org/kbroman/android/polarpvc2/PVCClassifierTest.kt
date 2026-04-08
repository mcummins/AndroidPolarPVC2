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

    @Test
    fun looksLikePVC_rejectsEarlyNadirEvenWithHighStatistic() {
        assertFalse(PVCClassifier.looksLikePVC(2, 0.9474))
    }

    @Test
    fun looksLikePVC_rejectsBorderlineNadirWithoutCompensatoryPause() {
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 4,
                pvcTestStat = 0.9474,
                rrBeforeSec = 0.78,
                rrAfterSec = 0.84,
                baselineRrSec = 0.80
            )
        )
    }

    @Test
    fun looksLikePVC_acceptsBorderlineNadirWithCompensatoryPause() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 4,
                pvcTestStat = 0.9474,
                rrBeforeSec = 0.62,
                rrAfterSec = 0.99,
                baselineRrSec = 0.80
            )
        )
    }

    @Test
    fun looksLikePVC_acceptsEarlyNadirWithCompensatoryPause() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2,
                pvcTestStat = 0.9474,
                rrBeforeSec = 0.52,
                rrAfterSec = 0.79,
                baselineRrSec = 0.68
            )
        )
    }

    @Test
    fun looksLikePVC_rejectsWeakStatistic() {
        assertFalse(PVCClassifier.looksLikePVC(6, 0.6316))
    }

    @Test
    fun looksLikePVC_acceptsDelayedNadirWithStrongStatistic() {
        assertTrue(PVCClassifier.looksLikePVC(6, 0.9474))
    }

    // --- New tests for QRS width ---

    @Test
    fun looksLikePVC_acceptsWideQrsWithModerateStatistic() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2,
                pvcTestStat = 0.70,
                qrsWidth = 16
            )
        )
    }

    @Test
    fun looksLikePVC_rejectsNarrowQrsWithModerateStatistic() {
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 2,
                pvcTestStat = 0.70,
                qrsWidth = 10
            )
        )
    }

    // --- New tests for amplitude ---

    @Test
    fun looksLikePVC_acceptsAbnormalAmplitudeWithLateNadir() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 3,
                pvcTestStat = 0.70,
                amplitudeRatio = 0.60  // 40% lower than baseline
            )
        )
    }

    @Test
    fun looksLikePVC_acceptsHighAmplitudeWithLateNadir() {
        assertTrue(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 4,
                pvcTestStat = 0.70,
                amplitudeRatio = 1.45  // 45% higher than baseline
            )
        )
    }

    @Test
    fun looksLikePVC_rejectsNormalAmplitudeWithEarlyNadir() {
        assertFalse(
            PVCClassifier.looksLikePVC(
                minPeakIndex = 1,
                pvcTestStat = 0.70,
                amplitudeRatio = 1.05  // normal amplitude
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
}
