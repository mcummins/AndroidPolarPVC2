package org.kbroman.android.polarpvc2

import android.graphics.Color
import android.util.Log
import com.androidplot.util.PixelUtils
import com.androidplot.xy.BoundaryMode
import com.androidplot.xy.LineAndPointFormatter
import com.androidplot.xy.SimpleXYSeries
import com.androidplot.xy.StepMode
import com.androidplot.xy.XYPlot
import com.androidplot.xy.XYRegionFormatter
import com.androidplot.xy.XYSeriesFormatter
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max

class ECGplotter (private var mActivity: MainActivity?, private var Plot: XYPlot?) {
    private var nData: Int = 0
    var updatePlot = false
    companion object {
        private const val TAG = "PolarPVC2app_plot"
        private const val SEC_TO_PLOT: Double = 10.0   // Show this many seconds
        private const val N_TOTAL_POINTS: Int = 130*10   // corresponding number of ECG data points
        const val N_REPLAY_POINTS: Int = 130*10   // samples to replay when restoring the plot
        // initial y-range shown before data arrives; the axis then auto-fits to
        // the visible signal (updateRangeBoundaries)
        private const val ECG_Y_MIN_INIT: Double = -1.5
        private const val ECG_Y_MAX_INIT: Double = 1.5
        private const val Y_STEP: Double = 0.5            // axis step + bound quantization (mV)
        private const val Y_PAD_FRAC: Double = 0.15       // headroom above/below the visible peaks
        private const val Y_MIN_SPAN: Double = 1.5        // never zoom in tighter than this (mV)
    }

    // ECG
    private var formatterECG: XYSeriesFormatter<XYRegionFormatter>? = null
    var seriesECG: SimpleXYSeries? = null

    // Peaks
    private var formatterPeaks: XYSeriesFormatter<XYRegionFormatter>? = null
    var seriesPeaks: SimpleXYSeries? = null

    // PVC
    private var formatterPVC: XYSeriesFormatter<XYRegionFormatter>? = null
    var seriesPVC: SimpleXYSeries? = null


    init {
        nData = 0
        formatterECG = LineAndPointFormatter(Color.rgb(0x11 , 0x11, 0x11),  // black lines
            null, null, null)
        seriesECG = SimpleXYSeries("ECG")

        formatterPeaks = LineAndPointFormatter(null, Color.rgb(0xFF, 0x41, 0x36), // red points
            null, null)
        formatterPeaks!!.setLegendIconEnabled(false)
        (formatterPeaks as LineAndPointFormatter).getVertexPaint().setStrokeWidth(PixelUtils.dpToPix(7F))
        seriesPeaks = SimpleXYSeries("Peaks")

        formatterPVC = LineAndPointFormatter(null, Color.rgb(0x00, 0x74, 0xD9), // blue points
            null, null)
        formatterPVC!!.setLegendIconEnabled(false)
        (formatterPVC as LineAndPointFormatter).getVertexPaint().setStrokeWidth(PixelUtils.dpToPix(7F))
        seriesPVC = SimpleXYSeries("PVC")


        Plot!!.addSeries(seriesECG, formatterECG)
        Plot!!.addSeries(seriesPeaks, formatterPeaks)
        Plot!!.addSeries(seriesPVC, formatterPVC)
        setupPlot()
    }

    fun setupPlot() {
        try {
            // range (y-axis): start with a sane symmetric range, then auto-fit
            // to the visible signal once data flows (updateRangeBoundaries)
            Plot!!.setRangeBoundaries(ECG_Y_MIN_INIT, ECG_Y_MAX_INIT, BoundaryMode.FIXED)
            Plot!!.setRangeStep(StepMode.INCREMENT_BY_VAL, Y_STEP)
            Plot!!.setUserRangeOrigin(0.0)

            update()
        } catch (ex: Exception) {
            Log.e(TAG, "Problem setting up plot")
        }
    }

    fun getNewInstance(activity: MainActivity, plot: XYPlot): ECGplotter {
        val newPlotter = ECGplotter(activity, plot)
        newPlotter.Plot = plot
        newPlotter.mActivity = this.mActivity
        newPlotter.nData = this.nData

        newPlotter.formatterECG = this.formatterECG
        newPlotter.seriesECG = this.seriesECG

        newPlotter.formatterPeaks = this.formatterPeaks
        newPlotter.seriesPeaks = this.seriesPeaks

        newPlotter.formatterPVC = this.formatterPVC
        newPlotter.seriesPVC = this.seriesPVC


        try {
            newPlotter.Plot!!.addSeries(seriesECG, formatterECG)
            newPlotter.Plot!!.addSeries(seriesPeaks, formatterPeaks)
            newPlotter.Plot!!.addSeries(seriesPVC, formatterPVC)
            newPlotter.setupPlot()
        } catch (ex: Exception) {
            Log.e(TAG, "trouble setting up new plot")
        }

        return newPlotter
    }



    fun addValues(time: Double?, volt: Double?) {
        if (time != null && volt != null) {
            if (seriesECG!!.size() >= N_TOTAL_POINTS) {
                seriesECG!!.removeFirst()
            }
            seriesECG!!.addLast(time, volt)
            nData++
        }

        update()
    }

    fun addPeakValue(time: Double?, volt: Double?) {
        removeOutOfRangeValues()
        seriesPeaks!!.addLast(time, volt)
        update()
    }

    fun replaceLastPeakValue(time: Double?, volt: Double?) {
        removeOutOfRangeValues()
        seriesPeaks!!.removeLast()
        seriesPeaks!!.addLast(time, volt)

        update()
    }

    fun addPVCValue(time: Double?, volt: Double?) {
        removeOutOfRangeValues()
        seriesPVC!!.addLast(time, volt)
        update()
    }

    fun removeOutOfRangeValues() {
        var xMin: Double = seriesECG!!.getxVals().getLast().toDouble() - SEC_TO_PLOT

        // Remove old values if needed
        while (seriesPeaks!!.size() > 0 && seriesPeaks!!.getxVals().getFirst().toDouble() < xMin) {
            seriesPeaks!!.removeFirst()
        }

        while (seriesPVC!!.size() > 0 && seriesPVC!!.getxVals().getFirst().toDouble() < xMin) {
            seriesPVC!!.removeFirst()
        }
    }

    fun updateDomainBoundaries() {
        val xMax: Double = seriesECG!!.getxVals().getLast().toDouble()
        val xMin: Double = xMax - SEC_TO_PLOT

        Plot!!.setDomainBoundaries(xMin, xMax, BoundaryMode.FIXED)
        Plot!!.setDomainStep(StepMode.INCREMENT_BY_VAL, 1.0)
    }

    /**
     * Auto-fit the y-axis to the peaks currently on screen, so neither polarity
     * is clipped — the QRS is negative-dominant on some electrode placements, so
     * a fixed range cut off the downward peaks (and their markers). Bounds are
     * padded and quantized to [Y_STEP] so the axis stays steady beat-to-beat and
     * rescales only when the signal amplitude actually changes.
     */
    fun updateRangeBoundaries() {
        val ys = seriesECG!!.getyVals() ?: return
        if (ys.isEmpty()) return
        var lo = Double.POSITIVE_INFINITY
        var hi = Double.NEGATIVE_INFINITY
        for (n in ys) {
            val v = n?.toDouble() ?: continue
            if (!v.isFinite()) continue
            if (v < lo) lo = v
            if (v > hi) hi = v
        }
        if (lo > hi) return
        val pad = max(Y_STEP, (hi - lo) * Y_PAD_FRAC)
        var rngMin = floor((lo - pad) / Y_STEP) * Y_STEP
        var rngMax = ceil((hi + pad) / Y_STEP) * Y_STEP
        if (rngMax - rngMin < Y_MIN_SPAN) {
            val c = (rngMax + rngMin) / 2.0
            rngMin = floor((c - Y_MIN_SPAN / 2.0) / Y_STEP) * Y_STEP
            rngMax = ceil((c + Y_MIN_SPAN / 2.0) / Y_STEP) * Y_STEP
        }
        Plot!!.setRangeBoundaries(rngMin, rngMax, BoundaryMode.FIXED)
    }

    fun update() {
        if (updatePlot) {
            updateDomainBoundaries()
            updateRangeBoundaries()
            mActivity!!.runOnUiThread { Plot!!.redraw() }
            updatePlot = false
        }
    }

    fun clear() {
        nData = 0
        seriesECG!!.clear()
        seriesPeaks!!.clear()
        seriesPVC!!.clear()
        update()
    }
}
