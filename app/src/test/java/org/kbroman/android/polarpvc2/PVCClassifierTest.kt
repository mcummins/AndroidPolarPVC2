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
}
