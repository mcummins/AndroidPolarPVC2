package org.kbroman.android.polarpvc2

internal object PVCClassifier {
    const val MIN_POST_R_NADIR_INDEX = 5
    const val TEST_STAT_THRESHOLD = 0.78
    const val PREMATURE_RR_RATIO = 0.90
    const val POST_RR_RATIO = 1.05
    const val COMPENSATORY_SUM_LOWER_RATIO = 1.80
    const val COMPENSATORY_SUM_UPPER_RATIO = 2.20

    fun looksLikePVC(
        minPeakIndex: Int,
        pvcTestStat: Double,
        rrBeforeSec: Double? = null,
        rrAfterSec: Double? = null,
        baselineRrSec: Double? = null
    ): Boolean {
        if (minPeakIndex < 0) return false
        if (pvcTestStat <= TEST_STAT_THRESHOLD) return false
        if (hasCompensatoryPause(rrBeforeSec, rrAfterSec, baselineRrSec)) return true
        return minPeakIndex >= MIN_POST_R_NADIR_INDEX
    }

    fun calcTestStat(values: List<Double>): Double {
        if (values.isEmpty()) return 0.0

        var minValue = values[0]
        var maxValue = values[0]

        for (value in values) {
            if (value < minValue) minValue = value
            if (value > maxValue) maxValue = value
        }

        val midRange = (maxValue + minValue) / 2.0
        var countBelowMidRange = 0

        for (value in values) {
            if (value < midRange) countBelowMidRange++
        }

        return countBelowMidRange.toDouble() / values.size.toDouble()
    }

    private fun hasCompensatoryPause(
        rrBeforeSec: Double?,
        rrAfterSec: Double?,
        baselineRrSec: Double?
    ): Boolean {
        if (rrBeforeSec == null || rrAfterSec == null || baselineRrSec == null) return false
        if (baselineRrSec <= 0.0) return false

        val isPremature = rrBeforeSec < baselineRrSec * PREMATURE_RR_RATIO
        val hasPauseAfter = rrAfterSec > baselineRrSec * POST_RR_RATIO
        val combinedRatio = (rrBeforeSec + rrAfterSec) / baselineRrSec
        val isCompensatoryWindow =
            combinedRatio >= COMPENSATORY_SUM_LOWER_RATIO &&
                combinedRatio <= COMPENSATORY_SUM_UPPER_RATIO

        return isPremature && hasPauseAfter && isCompensatoryWindow
    }
}
