package org.kbroman.android.polarpvc2

import com.polar.sdk.api.model.PolarEcgData
import org.kbroman.android.polarpvc2.ecg.EcgDetect
import org.kbroman.android.polarpvc2.ecg.EcgFeatures
import kotlin.math.max

/**
 * Live beat detection + PVC classification, aligned with the offline pipeline
 * (`analysis/polarpvc/`). This is a streaming wrapper around the pure
 * [EcgDetect] / [EcgFeatures] / [PVCClassifier] ports.
 *
 * Rolling horizon: each batch we re-run the whole-signal Pan–Tompkins detector
 * over a trailing window of the buffer and *finalize* a beat only once ~DELAY_S
 * of following signal exists, so its detection and features are stable. This
 * removes the old half-batch merge limit (which merged beats above ~160 bpm)
 * and lets the device reuse the validated offline detector/classifier. The cost
 * is that beat/PVC markers and stats lag the live trace edge by ~DELAY_S; the
 * ECG trace itself still scrolls in real time.
 */
class PeakDetection(var listener: EcgListener? = null) {

    companion object {
        private const val FS: Double = 130.0
        private const val N_ECG_VALS: Int = 130 * 60 * 5         // 5-min ring buffer
        private const val N_PEAKS_FOR_RR_AVE = 25
        private const val N_PEAKS_FOR_PVC_AVE = 100
        private const val WINDOW_S: Double = 15.0                // trailing detection window
        private const val DELAY_S: Double = 4.0                  // finalize beats this far from the edge
        private const val MAX_RR_SEC: Double = 60.0 / 35.0
        private const val GAP_FACTOR: Double = 3.0              // dropout = spacing jump this many samples
        const val TIMESTAMP_OFFSET: Long = 946684800000000000
        // for time offset, see https://github.com/polarofficial/polar-ble-sdk/blob/master/documentation/TimeSystemExplained.md
    }

    var ecgData: ECGdata = ECGdata(N_ECG_VALS)
    var pvcData: RunningAverage = RunningAverage(N_PEAKS_FOR_PVC_AVE)
    var rrData: RunningAverage = RunningAverage(N_PEAKS_FOR_RR_AVE)
    var totalBeats: Int = 0
    var totalPVCs: Int = 0

    private val cfg = PVCClassifier.ClassifierConfig()
    // global index of the first sample of the current gap-free segment
    private var segmentStartIndex: Int = 0
    private var lastTimestampNs: Long = -1L
    // epoch seconds of the most recently finalized beat (-1 = none yet)
    private var lastFinalizedTimeSec: Double = -1.0

    fun processData(polarEcgData: PolarEcgData) {
        val n = polarEcgData.samples.size
        val times = LongArray(n)
        val volts = DoubleArray(n)
        for (i in 0 until n) {
            val data = polarEcgData.samples[i]
            times[i] = data.timeStamp + TIMESTAMP_OFFSET
            volts[i] = data.voltage.toFloat() / 1000.0   // µV -> mV
        }
        ingestBatch(times, volts)
    }

    /** Core batch path (no Polar SDK types), shared by processData and tests. */
    internal fun ingestBatch(timesNs: LongArray, voltsMv: DoubleArray) {
        val gapThresholdNs = (GAP_FACTOR * 1e9 / FS).toLong()
        for (i in timesNs.indices) {
            val timestamp = timesNs[i]
            val voltage = voltsMv[i]
            // a jump in sample spacing is a dropout: start a new contiguous
            // segment so detection never spans the gap
            if (lastTimestampNs > 0 && timestamp - lastTimestampNs > gapThresholdNs) {
                segmentStartIndex = ecgData.maxIndex()
                lastFinalizedTimeSec = -1.0
            }
            lastTimestampNs = timestamp
            ecgData.add(timestamp, voltage)
            listener?.onEcgSample(timestamp / 1e9, voltage)
        }
        listener?.onEcgBatchDone()
        detectAndFinalize()
    }

    private fun detectAndFinalize() {
        val newest = ecgData.maxIndex() - 1
        val firstAvail = ecgData.maxIndex() - ecgData.size()
        if (newest < firstAvail) return
        val newestTimeSec = ecgData.time.get(newest) / 1e9

        // trailing window, clamped to the current segment and to the buffer
        val windowSpanStart = newestTimeSec - WINDOW_S
        var windowStart = max(segmentStartIndex, firstAvail)
        // advance windowStart to the first sample at/after windowSpanStart
        var g = windowStart
        while (g <= newest && ecgData.time.get(g) / 1e9 < windowSpanStart) g++
        windowStart = max(windowStart, g.coerceAtMost(newest))

        val len = newest - windowStart + 1
        if (len < (0.5 * FS).toInt()) return

        val mv = DoubleArray(len)
        val tSec = DoubleArray(len)
        for (i in 0 until len) {
            val gi = windowStart + i
            mv[i] = ecgData.volt.get(gi)
            tSec[i] = ecgData.time.get(gi) / 1e9
        }

        val localBeats = EcgDetect.detectBeats(mv, FS)
        if (localBeats.isEmpty()) return
        val feats = EcgFeatures.extractFeatures(mv, tSec, localBeats, FS)
        val labels = PVCClassifier.classifyBeats(feats, cfg)

        val cutoff = newestTimeSec - DELAY_S
        for (k in feats.indices) {
            val f = feats[k]
            val beatTime = f.t
            if (beatTime <= lastFinalizedTimeSec + 1e-9) continue   // already finalized
            if (beatTime > cutoff) break                            // too close to the edge yet
            finalizeBeat(f, labels[k], mv[f.index])                 // f.index is window-local
            lastFinalizedTimeSec = beatTime
        }
    }

    private fun finalizeBeat(f: EcgFeatures.BeatFeatures, label: PVCClassifier.BeatLabel, voltage: Double) {
        val beatTime = f.t

        // bad-signal beats: show the peak but exclude from the burden counts
        if (label.isArtifact) {
            listener?.onPeak(beatTime, voltage)
            return
        }

        totalBeats++
        if (label.isPvc) {
            totalPVCs++
            pvcData.add(1.0)
            pvcData.lastTime = beatTime
            listener?.onPvc(beatTime, voltage)
        } else {
            pvcData.add(0.0)
            pvcData.lastTime = beatTime
            val rr = f.rrBefore
            if (rr.isFinite() && rr in 0.0..MAX_RR_SEC) {
                rrData.add(rr)
                rrData.lastTime = beatTime
            }
        }
        listener?.onPeak(beatTime, voltage)
    }

    fun clear() {
        pvcData.clear()
        rrData.clear()
        ecgData = ECGdata(N_ECG_VALS)
        totalBeats = 0
        totalPVCs = 0
        segmentStartIndex = 0
        lastTimestampNs = -1L
        lastFinalizedTimeSec = -1.0
    }
}
