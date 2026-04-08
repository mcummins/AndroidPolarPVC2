package org.kbroman.android.polarpvc2

internal object PVCClassifier {
    const val MIN_POST_R_NADIR_INDEX = 5
    const val TEST_STAT_THRESHOLD = 0.65
    const val STRONG_TEST_STAT_THRESHOLD = 0.78
    const val PREMATURE_RR_RATIO = 0.90
    const val POST_RR_RATIO = 1.05
    const val COMPENSATORY_SUM_LOWER_RATIO = 1.80
    const val COMPENSATORY_SUM_UPPER_RATIO = 2.20
    const val QRS_WIDTH_PVC_THRESHOLD = 14  // samples at 130 Hz (~108 ms; normal QRS < 100 ms)
    const val AMPLITUDE_DEVIATION_THRESHOLD = 0.30  // 30% deviation from baseline amplitude

    fun looksLikePVC(
        minPeakIndex: Int,
        pvcTestStat: Double,
        rrBeforeSec: Double? = null,
        rrAfterSec: Double? = null,
        baselineRrSec: Double? = null,
        qrsWidth: Int = 0,
        amplitudeRatio: Double? = null
    ): Boolean {
        if (minPeakIndex < 0) return false
        if (pvcTestStat <= TEST_STAT_THRESHOLD) return false

        // Compensatory pause is strong evidence on its own
        if (hasCompensatoryPause(rrBeforeSec, rrAfterSec, baselineRrSec)) return true

        // Wide QRS is strong evidence of ventricular origin
        if (qrsWidth >= QRS_WIDTH_PVC_THRESHOLD) return true

        // Abnormal amplitude combined with late nadir
        if (hasAbnormalAmplitude(amplitudeRatio) && minPeakIndex >= MIN_POST_R_NADIR_INDEX - 2) return true

        // Original nadir check requires stronger morphology signal
        if (pvcTestStat > STRONG_TEST_STAT_THRESHOLD && minPeakIndex >= MIN_POST_R_NADIR_INDEX) return true

        return false
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

    internal fun hasAbnormalAmplitude(amplitudeRatio: Double?): Boolean {
        if (amplitudeRatio == null) return false
        return kotlin.math.abs(1.0 - amplitudeRatio) > AMPLITUDE_DEVIATION_THRESHOLD
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
