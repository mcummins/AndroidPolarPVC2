package org.kbroman.android.polarpvc2

/**
 * Callbacks from the recording engine (RecordingService / PeakDetection)
 * to the UI. All methods are invoked on the main thread. The UI may be
 * absent (screen off, activity destroyed), in which case no listener is
 * attached and recording continues regardless.
 */
interface EcgListener {
    fun onEcgSample(timeSec: Double, voltage: Double)
    fun onEcgBatchDone()
    fun onPeak(timeSec: Double, voltage: Double)
    fun onPeakReplaced(timeSec: Double, voltage: Double)
    fun onPvc(timeSec: Double, voltage: Double)

    /** Summary stats after each batch; pvcTotalPct is null until beats have accumulated. */
    fun onStats(
        hrBpm: Double,
        pvcAvePct: Double,
        pvcTotalPct: Double?,
        rrLastTimeSec: Double,
        pvcLastTimeSec: Double,
        enoughForPlot: Boolean
    )

    fun onConnectionChanged(connected: Boolean, deviceId: String)
    fun onBatteryLevel(level: Int)
    fun onRecordingChanged(recording: Boolean)
}
