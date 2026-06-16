package org.kbroman.android.polarpvc2.ecg

import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Per-beat feature extraction — a port of `analysis/polarpvc/features.py`.
 *
 * The discriminating features (validated offline against hand-labelled real
 * data) are the correlation of each beat's QRS-T morphology to a template of
 * the patient's own normal beats, the prematurity of the coupling interval, and
 * a local high-frequency noise ratio for signal-quality gating. QRS width is
 * computed but only informational (130 Hz makes it coarse).
 *
 * Pure / JVM-testable; mirrors the numpy code so the Kotlin and Python labels
 * stay in parity (see EcgParityTest).
 */
object EcgFeatures {
    private const val MORPH_PRE_S = 0.12
    private const val MORPH_POST_S = 0.20
    // template correlation is matched over +/- this many samples of fiducial
    // shift, so a biphasic QRS (R and S nearly equal) whose detected fiducial
    // jitters between the R and S peaks still matches its own normal template
    // instead of anti-correlating and being flagged as a PVC.
    private const val MORPH_MAX_LAG = 5
    private const val QRS_HALF_S = 0.10
    private const val QRS_ENVELOPE_FRAC = 0.15
    private const val BASELINE_RR_BEATS = 8
    private const val NOISE_HALF_S = 0.20

    data class BeatFeatures(
        val index: Int,
        val t: Double,
        val rrBefore: Double,   // NaN if unknown
        val rrAfter: Double,    // NaN if unknown
        val baselineRr: Double, // NaN if unknown
        val prematurity: Double,
        val compensatory: Double,
        val qrsWidthMs: Double,
        val amplitudeMv: Double,
        val polarity: Int,
        val templateCorr: Double,
        val noiseRatio: Double,
    )

    private fun energyEnvelope(mv: DoubleArray, fs: Double): DoubleArray {
        val filt = EcgDetect.filtfilt(EcgDetect.BP_5_25, mv)
        val sq = DoubleArray(filt.size) { filt[it] * filt[it] }
        val win = max(1, EcgDetect.pyRound(0.05 * fs))
        return EcgDetect.movingAverageSame(sq, win)
    }

    private fun qrsWidthMs(envelope: DoubleArray, rIdx: Int, fs: Double): Double {
        val half = max(1, EcgDetect.pyRound(QRS_HALF_S * fs))
        val lo = max(0, rIdx - half)
        val hi = min(envelope.size, rIdx + half + 1)
        if (hi <= lo) return 0.0
        var peak = envelope[lo]
        for (i in lo until hi) if (envelope[i] > peak) peak = envelope[i]
        if (peak <= 0.0) return 0.0
        val thr = peak * QRS_ENVELOPE_FRAC
        val size = hi - lo
        val rLocal = rIdx - lo
        var left = rLocal
        while (left > 0 && envelope[lo + left] > thr) left--
        var right = rLocal
        while (right < size - 1 && envelope[lo + right] > thr) right++
        return (right - left).toDouble() / fs * 1000.0
    }

    private fun baselineMv(mv: DoubleArray, fs: Double, rIdx: Int): Double {
        val a = max(0, rIdx - EcgDetect.pyRound(0.12 * fs))
        val b = max(0, rIdx - EcgDetect.pyRound(0.06 * fs))
        if (b <= a) return 0.0
        return median(mv, a, b)
    }

    private fun rollingBaselineRr(rr: DoubleArray): DoubleArray {
        val n = rr.size
        val out = DoubleArray(n) { Double.NaN }
        val k = BASELINE_RR_BEATS
        val buf = ArrayList<Double>(2 * k + 1)
        for (i in 0 until n) {
            val lo = max(0, i - k)
            val hi = min(n, i + k + 1)
            buf.clear()
            for (j in lo until hi) if (rr[j].isFinite()) buf.add(rr[j])
            if (buf.isNotEmpty()) out[i] = medianOfList(buf)
        }
        return out
    }

    private fun noiseRatio(mv: DoubleArray, fs: Double, beats: IntArray): DoubleArray {
        val n = beats.size
        if (n == 0) return DoubleArray(0)
        val bp = EcgDetect.filtfilt(EcgDetect.BP_0p5_25, mv)
        val hf = DoubleArray(mv.size) { mv[it] - bp[it] }
        val half = max(1, EcgDetect.pyRound(NOISE_HALF_S * fs))
        val rms = DoubleArray(n)
        for (i in 0 until n) {
            val r = beats[i]
            val lo = max(0, r - half)
            val hi = min(hf.size, r + half + 1)
            if (hi > lo) {
                var s = 0.0
                for (j in lo until hi) s += hf[j] * hf[j]
                rms[i] = sqrt(s / (hi - lo))
            } else {
                rms[i] = 0.0
            }
        }
        val positive = rms.filter { it > 0.0 }
        var base = if (positive.isNotEmpty()) medianOfList(ArrayList(positive)) else 1.0
        if (base <= 0.0) base = 1.0
        return DoubleArray(n) { rms[it] / base }
    }

    fun extractFeatures(mv: DoubleArray, t: DoubleArray, beats: IntArray, fs: Double): List<BeatFeatures> {
        val n = beats.size
        if (n == 0) return emptyList()

        val pre = EcgDetect.pyRound(MORPH_PRE_S * fs)
        val post = EcgDetect.pyRound(MORPH_POST_S * fs)
        val winLen = pre + post + 1
        val envelope = energyEnvelope(mv, fs)

        // RR intervals (seconds)
        val times = DoubleArray(n) { t[beats[it]] }
        val rrBefore = DoubleArray(n) { Double.NaN }
        val rrAfter = DoubleArray(n) { Double.NaN }
        for (i in 1 until n) rrBefore[i] = times[i] - times[i - 1]
        for (i in 0 until n - 1) rrAfter[i] = times[i + 1] - times[i]
        val baselineRr = rollingBaselineRr(rrBefore)

        val width = DoubleArray(n)
        val amp = DoubleArray(n)
        val polarity = IntArray(n) { 1 }
        val baseArr = DoubleArray(n)
        val windows = Array(n) { DoubleArray(winLen) }
        for (i in 0 until n) {
            val r = beats[i]
            val base = baselineMv(mv, fs, r)
            baseArr[i] = base
            val lo = r - pre
            val hi = r + post + 1
            if (lo < 0 || hi > mv.size) {
                val vlo = max(0, lo)
                val vhi = min(mv.size, hi)
                var k = 0
                for (j in vlo until vhi) { windows[i][k] = mv[j] - base; k++ }
            } else {
                for (j in 0 until winLen) windows[i][j] = mv[lo + j] - base
            }
            amp[i] = mv[r] - base
            polarity[i] = if (amp[i] >= 0.0) 1 else -1
            width[i] = qrsWidthMs(envelope, r, fs)
        }

        val prematurity = DoubleArray(n) { rrBefore[it] / baselineRr[it] }

        // median of positive widths
        val posW = width.filter { it > 0.0 }
        val medianWidth = if (posW.isNotEmpty()) medianOfList(ArrayList(posW)) else 0.0

        // template from non-premature, modest-width beats of the recording's
        // DOMINANT QRS polarity (not assumed-upright), so an inverted normal
        // morphology builds a correct template instead of flagging every beat.
        val basePool = BooleanArray(n) {
            width[it] <= medianWidth * 1.3 + 1e-9 &&
                !(prematurity[it] < 0.85) // NaN-safe
        }
        val anyPool = basePool.any { it }
        var pos = 0; var neg = 0
        for (i in 0 until n) {
            if ((if (anyPool) basePool[i] else true)) {
                if (polarity[i] > 0) pos++ else neg++
            }
        }
        val dominant = if (pos >= neg) 1 else -1
        val eligible = BooleanArray(n) { basePool[it] && polarity[it] == dominant }

        val normWindows = normalizeRows(windows)
        var template = buildTemplate(normWindows, eligible, winLen)
        template = unit(centerInPlace(template))

        // correlation matched over a small fiducial shift (see MORPH_MAX_LAG)
        val corr = correlateLagged(mv, beats, baseArr, pre, post, template)

        val noise = noiseRatio(mv, fs, beats)

        val out = ArrayList<BeatFeatures>(n)
        for (i in 0 until n) {
            val comp = if (rrBefore[i].isFinite() && rrAfter[i].isFinite() && baselineRr[i].isFinite())
                (rrBefore[i] + rrAfter[i]) / baselineRr[i] else Double.NaN
            out.add(
                BeatFeatures(
                    index = beats[i],
                    t = times[i],
                    rrBefore = rrBefore[i],
                    rrAfter = rrAfter[i],
                    baselineRr = baselineRr[i],
                    prematurity = if (prematurity[i].isFinite()) prematurity[i] else Double.NaN,
                    compensatory = comp,
                    qrsWidthMs = width[i],
                    amplitudeMv = amp[i],
                    polarity = polarity[i],
                    templateCorr = corr[i],
                    noiseRatio = noise[i],
                )
            )
        }
        return out
    }

    /** Per-beat correlation to the template, maximised over a small fiducial
     *  shift; mirrors features.py _correlate_lagged for cross-language parity. */
    private fun correlateLagged(
        mv: DoubleArray, beats: IntArray, baseArr: DoubleArray,
        pre: Int, post: Int, template: DoubleArray,
    ): DoubleArray {
        val n = beats.size
        val winLen = pre + post + 1
        val corr = DoubleArray(n)
        val seg = DoubleArray(winLen)
        for (i in 0 until n) {
            val r = beats[i]
            val b = baseArr[i]
            var best = -2.0
            for (d in -MORPH_MAX_LAG..MORPH_MAX_LAG) {
                val lo = r - pre + d
                val hi = r + post + 1 + d
                if (lo < 0 || hi > mv.size) {
                    for (j in 0 until winLen) seg[j] = 0.0
                    val vlo = max(0, lo)
                    val vhi = min(mv.size, hi)
                    var k = 0
                    for (j in vlo until vhi) { seg[k] = mv[j] - b; k++ }
                } else {
                    for (j in 0 until winLen) seg[j] = mv[lo + j] - b
                }
                var mean = 0.0
                for (v in seg) mean += v
                mean /= winLen
                var norm = 0.0
                for (j in 0 until winLen) { seg[j] -= mean; norm += seg[j] * seg[j] }
                norm = sqrt(norm)
                var c = 0.0
                if (norm > 0.0) {
                    for (j in 0 until winLen) c += (seg[j] / norm) * template[j]
                }
                if (c > best) best = c
            }
            corr[i] = best
        }
        return corr
    }

    // --- linear-algebra helpers (mirror features.py) ---

    private fun normalizeRows(w: Array<DoubleArray>): Array<DoubleArray> {
        return Array(w.size) { i ->
            val row = w[i]
            var mean = 0.0
            for (v in row) mean += v
            mean /= row.size
            val centered = DoubleArray(row.size) { row[it] - mean }
            var norm = 0.0
            for (v in centered) norm += v * v
            norm = sqrt(norm)
            if (norm == 0.0) norm = 1.0
            DoubleArray(row.size) { centered[it] / norm }
        }
    }

    private fun buildTemplate(windows: Array<DoubleArray>, eligible: BooleanArray, winLen: Int): DoubleArray {
        var selIdx = (windows.indices).filter { eligible[it] }
        if (selIdx.size < 5) selIdx = windows.indices.toList()
        // per-column median over selected rows
        val template = DoubleArray(winLen)
        val col = DoubleArray(selIdx.size)
        for (j in 0 until winLen) {
            for (k in selIdx.indices) col[k] = windows[selIdx[k]][j]
            template[j] = medianOfCopy(col)
        }
        return template
    }

    private fun centerInPlace(v: DoubleArray): DoubleArray {
        var mean = 0.0
        for (x in v) mean += x
        mean /= v.size
        return DoubleArray(v.size) { v[it] - mean }
    }

    private fun unit(v: DoubleArray): DoubleArray {
        var norm = 0.0
        for (x in v) norm += x * x
        norm = sqrt(norm)
        return if (norm > 0.0) DoubleArray(v.size) { v[it] / norm } else v
    }

    private fun median(x: DoubleArray, from: Int, to: Int): Double {
        val s = x.copyOfRange(from, to)
        s.sort()
        val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2.0
    }

    private fun medianOfCopy(x: DoubleArray): Double {
        val s = x.copyOf()
        s.sort()
        val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2.0
    }

    private fun medianOfList(list: ArrayList<Double>): Double {
        list.sort()
        val m = list.size / 2
        return if (list.size % 2 == 1) list[m] else (list[m - 1] + list[m]) / 2.0
    }
}
