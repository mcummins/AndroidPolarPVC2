package org.kbroman.android.polarpvc2.ecg

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * R-peak (beat) detection — a port of the offline `analysis/polarpvc/detect.py`
 * Pan–Tompkins detector, so the on-device live preview uses the same algorithm
 * as the validated offline pipeline.
 *
 * Everything here is pure (no Android dependencies) and operates on DoubleArray
 * exactly like the numpy code, so it is unit-testable on the JVM and can be
 * checked for parity against scipy (see EcgParityTest).
 *
 * The bandpass is a 2nd-order Butterworth band (4th-order overall) applied with
 * a zero-phase forward/backward pass (scipy `filtfilt`). scipy's filter
 * coefficients and `lfilter_zi` steady state are fixed for the H10's 130 Hz, so
 * they are hard-coded below rather than re-derived; this removes coefficient
 * drift as a parity risk and leaves only the (deterministic) filtering to match.
 */
object EcgDetect {
    // --- detection constants (mirror detect.py) ---
    private const val REFRACTORY_S = 0.20      // 300 bpm ceiling
    private const val INTEGRATION_S = 0.15     // QRS integration window
    private const val THRESHOLD_WINDOW_S = 2.5 // window for the local QRS-energy estimate
    // Signal-relative threshold (Pan–Tompkins style): a fraction of the local
    // QRS-energy level, estimated as a high percentile of the integrated energy.
    // A noise + K·MAD rule climbs above the QRS during exercise (in-band motion
    // raises the noise, QRS energy is only a few MADs over the median) and drops
    // most beats; tracking the QRS level keeps detection working to ~170 bpm.
    private const val THRESHOLD_PCTILE = 90.0
    private const val THRESHOLD_SIG_FRAC = 0.35
    private const val THRESHOLD_FLOOR_FRAC = 0.05
    private const val REFINE_WINDOW_S = 0.05

    /**
     * Round half-to-even, matching Python's built-in round() / numpy round used
     * throughout the offline pipeline (e.g. round(0.05*130)=round(6.5)=6, not 7).
     * Kotlin's roundToInt() rounds half up, which would silently shift window
     * sizes and break parity, so all `int(round(...))` ports use this.
     */
    fun pyRound(x: Double): Int = Math.rint(x).toInt()

    /** A fixed Butterworth band-pass (order 2 band) at fs = 130 Hz. */
    class Butter(val b: DoubleArray, val a: DoubleArray, val zi: DoubleArray)

    // scipy butter(2, [lo/nyq, hi/nyq], 'band') + lfilter_zi at fs = 130 Hz.
    val BP_5_15 = Butter(
        b = doubleArrayOf(0.04310673133430984, 0.0, -0.08621346266861968, 0.0, 0.04310673133430984),
        a = doubleArrayOf(1.0, -3.0394638411965014, 3.6933679440217566, -2.1372365629911654, 0.5053338800276346),
        zi = doubleArrayOf(-0.043106731334309194, -0.04310673133431116, 0.043106731334310894, 0.04310673133430951),
    )
    val BP_5_25 = Butter(
        b = doubleArrayOf(0.13652096268345593, 0.0, -0.27304192536691185, 0.0, 0.13652096268345593),
        a = doubleArrayOf(1.0, -2.297791578821558, 2.1348523016468457, -1.053912840883182, 0.2642724727864042),
        zi = doubleArrayOf(-0.13652096268345593, -0.1365209626834559, 0.1365209626834559, 0.13652096268345593),
    )
    val BP_0p5_25 = Butter(
        b = doubleArrayOf(0.18819974367748113, 0.0, -0.37639948735496226, 0.0, 0.18819974367748113),
        a = doubleArrayOf(1.0, -2.415103747516145, 2.065379390855655, -0.8583492004893978, 0.20852904276841883),
        zi = doubleArrayOf(-0.1881997436774202, -0.18819974367756737, 0.18819974367752074, 0.18819974367746842),
    )

    // ---------------------------------------------------------------------
    // Zero-phase IIR filtering (scipy filtfilt, method='pad', padtype='odd')
    // ---------------------------------------------------------------------

    /** scipy lfilter, Direct Form II transposed, with initial state z. */
    private fun lfilter(b: DoubleArray, a: DoubleArray, x: DoubleArray, zInit: DoubleArray): DoubleArray {
        val n = x.size
        val y = DoubleArray(n)
        val z = zInit.copyOf()
        for (i in 0 until n) {
            val xi = x[i]
            val yi = b[0] * xi + z[0]
            y[i] = yi
            z[0] = b[1] * xi + z[1] - a[1] * yi
            z[1] = b[2] * xi + z[2] - a[2] * yi
            z[2] = b[3] * xi + z[3] - a[3] * yi
            z[3] = b[4] * xi - a[4] * yi
        }
        return y
    }

    /** scipy odd_ext: odd (point-symmetric) extension of length `ext` each side. */
    private fun oddExt(x: DoubleArray, ext: Int): DoubleArray {
        val n = x.size
        val out = DoubleArray(n + 2 * ext)
        val x0 = x[0]
        val xL = x[n - 1]
        // left: 2*x[0] - x[ext], x[ext-1], ..., x[1]
        for (i in 0 until ext) {
            out[i] = 2.0 * x0 - x[ext - i]
        }
        for (i in 0 until n) {
            out[ext + i] = x[i]
        }
        // right: 2*x[-1] - x[-2], x[-3], ..., x[-(ext+1)]
        for (i in 0 until ext) {
            out[ext + n + i] = 2.0 * xL - x[n - 2 - i]
        }
        return out
    }

    /** Zero-phase filtering matching scipy.signal.filtfilt defaults. */
    fun filtfilt(f: Butter, x: DoubleArray): DoubleArray {
        val padlen = 3 * max(f.a.size, f.b.size) // = 15 for our order-2 band
        if (x.size <= padlen) {
            // too short to pad as scipy would; fall back to an unpadded pass
            val fwd = lfilter(f.b, f.a, x, scale(f.zi, x.first()))
            val rev = fwd.reversedArray()
            val back = lfilter(f.b, f.a, rev, scale(f.zi, rev.first()))
            return back.reversedArray()
        }
        val ext = oddExt(x, padlen)
        val fwd = lfilter(f.b, f.a, ext, scale(f.zi, ext.first()))
        val rev = fwd.reversedArray()
        val back = lfilter(f.b, f.a, rev, scale(f.zi, rev.first()))
        val y = back.reversedArray()
        return y.copyOfRange(padlen, padlen + x.size)
    }

    private fun scale(v: DoubleArray, s: Double): DoubleArray {
        val out = DoubleArray(v.size)
        for (i in v.indices) out[i] = v[i] * s
        return out
    }

    // ---------------------------------------------------------------------
    // Pan–Tompkins front end + adaptive threshold + peak picking
    // ---------------------------------------------------------------------

    /** np.gradient (central differences, one-sided at the ends). */
    internal fun gradient(x: DoubleArray): DoubleArray {
        val n = x.size
        val g = DoubleArray(n)
        if (n == 1) return g
        g[0] = x[1] - x[0]
        g[n - 1] = x[n - 1] - x[n - 2]
        for (i in 1 until n - 1) g[i] = (x[i + 1] - x[i - 1]) / 2.0
        return g
    }

    /** np.convolve(x, ones(win)/win, mode="same"): centred moving average. */
    internal fun movingAverageSame(x: DoubleArray, win: Int): DoubleArray {
        val n = x.size
        val w = max(1, win)
        val full = DoubleArray(n + w - 1)
        // full convolution with a box kernel of height 1/w
        // full[k] = sum_j x[j] * kernel[k-j], kernel[m] = 1/w for 0<=m<w
        // => full[k] = (1/w) * sum_{j: 0<=k-j<w} x[j]
        var running = 0.0
        // sliding window sum over x with window [k-w+1, k]
        for (k in 0 until n + w - 1) {
            if (k < n) running += x[k]
            val drop = k - w
            if (drop >= 0) running -= x[drop]
            full[k] = running / w
        }
        // "same" takes the middle n values, starting at (w-1)/2
        val start = (w - 1) / 2
        return full.copyOfRange(start, start + n)
    }

    private fun integratedEnergy(mv: DoubleArray, fs: Double): DoubleArray {
        val filtered = filtfilt(BP_5_15, mv)
        val deriv = gradient(filtered)
        val squared = DoubleArray(deriv.size) { deriv[it] * deriv[it] }
        val win = max(1, pyRound(INTEGRATION_S * fs))
        return movingAverageSame(squared, win)
    }

    /** scipy.ndimage.median_filter(x, size=win, mode="nearest"); win odd. */
    internal fun medianFilterNearest(x: DoubleArray, win: Int): DoubleArray {
        val n = x.size
        val w = if (win % 2 == 0) win + 1 else win
        val half = w / 2
        val out = DoubleArray(n)
        val window = DoubleArray(w)
        for (i in 0 until n) {
            for (j in 0 until w) {
                var idx = i - half + j
                if (idx < 0) idx = 0
                if (idx > n - 1) idx = n - 1
                window[j] = x[idx]
            }
            out[i] = medianOf(window)
        }
        return out
    }

    private fun medianOf(values: DoubleArray): Double {
        val s = values.copyOf()
        s.sort()
        val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2.0
    }

    /** np.percentile with linear interpolation (numpy default). */
    internal fun percentile(x: DoubleArray, p: Double): Double {
        if (x.isEmpty()) return 0.0
        val s = x.copyOf()
        s.sort()
        if (s.size == 1) return s[0]
        val rank = p / 100.0 * (s.size - 1)
        val lo = rank.toInt()
        val hi = min(lo + 1, s.size - 1)
        val frac = rank - lo
        return s[lo] + (s[hi] - s[lo]) * frac
    }

    /** scipy.ndimage.percentile_filter(mode="nearest"): per-window order statistic
     *  at rank int(pctile/100 * size), clamped to [0, size-1]. */
    internal fun percentileFilterNearest(x: DoubleArray, win: Int, pctile: Double): DoubleArray {
        val n = x.size
        val w = if (win % 2 == 0) win + 1 else win
        val half = w / 2
        val rank = min(w - 1, (pctile / 100.0 * w).toInt())
        val out = DoubleArray(n)
        val window = DoubleArray(w)
        for (i in 0 until n) {
            for (j in 0 until w) {
                var idx = i - half + j
                if (idx < 0) idx = 0
                if (idx > n - 1) idx = n - 1
                window[j] = x[idx]
            }
            val s = window.copyOf()
            s.sort()
            out[i] = s[rank]
        }
        return out
    }

    private fun adaptiveThreshold(energy: DoubleArray, fs: Double): DoubleArray {
        var win = pyRound(THRESHOLD_WINDOW_S * fs)
        win = win or 1 // odd
        if (energy.size % 2 == 1) win = min(win, energy.size) else win = min(win, energy.size - 1)
        win = max(win, 1)
        val signal = percentileFilterNearest(energy, win, THRESHOLD_PCTILE)
        val floor = THRESHOLD_FLOOR_FRAC * percentile(energy, 98.0)
        return DoubleArray(energy.size) { max(THRESHOLD_SIG_FRAC * signal[it], floor) }
    }

    // ---------------------------------------------------------------------
    // find_peaks (local maxima with plateau handling, height, distance)
    // ---------------------------------------------------------------------

    /** scipy._local_maxima_1d: indices of local maxima (plateau midpoints). */
    internal fun localMaxima(x: DoubleArray): IntArray {
        val n = x.size
        val peaks = ArrayList<Int>()
        var i = 1
        val iMax = n - 1
        while (i < iMax) {
            if (x[i - 1] < x[i]) {
                var iAhead = i + 1
                while (iAhead < iMax && x[iAhead] == x[i]) iAhead++
                if (x[iAhead] < x[i]) {
                    val leftEdge = i
                    val rightEdge = iAhead - 1
                    val mid = (leftEdge + rightEdge) / 2
                    peaks.add(mid)
                    i = iAhead
                    continue
                }
            }
            i++
        }
        return peaks.toIntArray()
    }

    /** scipy._select_by_peak_distance, applied after a height filter. */
    private fun selectByDistance(peaks: IntArray, priority: DoubleArray, distance: Int): IntArray {
        val n = peaks.size
        val keep = BooleanArray(n) { true }
        // process highest-priority peaks first
        val order = (0 until n).sortedBy { priority[it] }
        for (oi in (n - 1) downTo 0) {
            val j = order[oi]
            if (!keep[j]) continue
            var k = j - 1
            while (k >= 0 && peaks[j] - peaks[k] < distance) {
                keep[k] = false; k--
            }
            k = j + 1
            while (k < n && peaks[k] - peaks[j] < distance) {
                keep[k] = false; k++
            }
        }
        return (0 until n).filter { keep[it] }.map { peaks[it] }.toIntArray()
    }

    private fun findPeaks(energy: DoubleArray, height: DoubleArray, distance: Int): IntArray {
        val maxima = localMaxima(energy)
        val passing = maxima.filter { energy[it] >= height[it] }.toIntArray()
        if (passing.isEmpty()) return passing
        val priority = DoubleArray(passing.size) { energy[passing[it]] }
        return selectByDistance(passing, priority, max(1, distance))
    }

    // ---------------------------------------------------------------------
    // refine to local extremum of the band-removed signal
    // ---------------------------------------------------------------------

    private fun refine(mv: DoubleArray, fs: Double, peaks: IntArray): IntArray {
        if (peaks.isEmpty()) return peaks
        val w = max(1, pyRound(REFINE_WINDOW_S * fs))
        val baseWin = max(1, fs.toInt())
        val baseline = movingAverageSame(mv, baseWin)
        val detr = DoubleArray(mv.size) { mv[it] - baseline[it] }
        val refined = sortedSetOf<Int>()
        for (p in peaks) {
            val lo = max(0, p - w)
            val hi = min(mv.size, p + w + 1)
            var bestIdx = lo
            var bestVal = -1.0
            for (i in lo until hi) {
                val v = abs(detr[i])
                if (v > bestVal) { bestVal = v; bestIdx = i }
            }
            refined.add(bestIdx)
        }
        return refined.toIntArray()
    }

    /** Detect R-peak sample indices within a single contiguous array. */
    fun detectBeats(mv: DoubleArray, fs: Double): IntArray {
        val n = mv.size
        if (n < (0.5 * fs).toInt()) return IntArray(0)
        val energy = integratedEnergy(mv, fs)
        val refractory = max(1, pyRound(REFRACTORY_S * fs))
        val thr = adaptiveThreshold(energy, fs)
        val peaks = findPeaks(energy, thr, refractory)
        return refine(mv, fs, peaks)
    }
}
