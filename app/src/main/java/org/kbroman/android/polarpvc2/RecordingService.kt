package org.kbroman.android.polarpvc2

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.polar.sdk.api.PolarBleApi
import com.polar.sdk.api.PolarBleApiCallback
import com.polar.sdk.api.PolarBleApiDefaultImpl
import com.polar.sdk.api.model.PolarDeviceInfo
import com.polar.sdk.api.model.PolarEcgData
import com.polar.sdk.api.model.PolarSensorSetting
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers
import io.reactivex.rxjava3.disposables.Disposable
import io.reactivex.rxjava3.schedulers.Schedulers
import java.time.Instant
import java.util.Calendar
import java.util.Date
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Foreground service that owns the BLE connection, peak detection, and
 * file writing, so that recording survives screen-off, Doze, and the
 * activity being destroyed. Auto-reconnects with exponential backoff
 * while connection is wanted, and resumes recording if the system
 * restarts the service (START_STICKY).
 */
class RecordingService : Service() {

    companion object {
        private const val TAG = "PolarPVC2app_service"

        const val ACTION_CONNECT = "org.kbroman.android.polarpvc2.CONNECT"
        const val ACTION_DISCONNECT = "org.kbroman.android.polarpvc2.DISCONNECT"
        const val ACTION_START_RECORDING = "org.kbroman.android.polarpvc2.START_RECORDING"
        const val ACTION_STOP_RECORDING = "org.kbroman.android.polarpvc2.STOP_RECORDING"

        const val PREFS_NAME = "polarpvc2"
        const val PREF_FILE_PATH = "PREF_FILE_PATH"
        const val PREF_DEVICE_ID = "PREF_DEVICE_ID"
        const val PREF_RECORDING = "PREF_RECORDING"
        // names of files currently open for writing; UploadWorker skips these
        const val PREF_OPEN_ECG_FILE = "PREF_OPEN_ECG_FILE"
        const val PREF_OPEN_EVENTS_FILE = "PREF_OPEN_EVENTS_FILE"
        const val DEFAULT_DEVICE_ID = "13DFA538"

        private const val NOTIFICATION_ID = 1
        private const val CHANNEL_ID = "recording"

        private const val INITIAL_RECONNECT_DELAY_MS = 2_000L
        private const val MAX_RECONNECT_DELAY_MS = 60_000L
        private const val STREAM_RESTART_DELAY_MS = 5_000L
        private const val NOTIFICATION_UPDATE_MS = 15_000L
        private const val WATCHDOG_INTERVAL_MS = 60_000L
    }

    private val binder = LocalBinder()
    inner class LocalBinder : Binder() {
        fun getService(): RecordingService = this@RecordingService
    }

    val pd: PeakDetection = PeakDetection()

    // HR and PVC% time-series for the session, kept here (survives the UI going
    // off-screen) so the activity can replay the trend plots when it returns.
    // Points mirror exactly what onStats pushes to the plotters.
    // raw per-batch trend points kept only to replay the plots after the UI was
    // backgrounded; ArrayDeque so capping is O(1). The plots bin these for
    // display, so this is just a rolling buffer of recent history.
    val hrHistory = ArrayDeque<DoubleArray>()     // [timeSec, bpm]
    val pvcHistory = ArrayDeque<DoubleArray>()    // [timeSec, pvcAvePct]
    private val MAX_TREND_POINTS = 20000  // ~3 h of replay history at ~2 points/s

    // most recent activity tag of the current recording session ("" if none);
    // kept here so the UI can show it and it survives the UI going off-screen
    var lastActivityTag: String = ""

    private lateinit var wd: WriteData
    private lateinit var eventLog: EventLog
    private val handler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null

    var deviceId: String = DEFAULT_DEVICE_ID
        private set
    var deviceConnected = false
        private set
    var isRecording = false
        private set
    var batteryLevel: Int = -1
        private set

    var shouldBeConnected = false
        private set
    private var bluetoothEnabled = false
    private var ecgDisposable: Disposable? = null
    private var reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS
    private var reconnectPending = false
    private var lastNotificationUpdate = 0L
    private var lastHrBpm: Double = -1.0
    private var lastPvcAve: Double = -1.0

    private val appVersion: String by lazy {
        try {
            packageManager.getPackageInfo(packageName, 0).versionName ?: "unknown"
        } catch (ex: Exception) {
            "unknown"
        }
    }

    // ECG batches are processed off the main thread (a single serial thread, so
    // batches stay ordered) to keep detection/classification/file-I/O from
    // blocking the UI and causing ANRs over long sessions.
    private val ecgExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    /** UI attaches/detaches here; null while in background. */
    var uiListener: EcgListener? = null
        set(value) {
            field = value
            // push current state so the UI can sync immediately (on main)
            value?.onConnectionChanged(deviceConnected, deviceId)
            value?.onRecordingChanged(isRecording)
            if (batteryLevel >= 0) value?.onBatteryLevel(batteryLevel)
        }

    // PeakDetection runs on the ECG background thread, so its callbacks are
    // marshalled to the main thread here before touching the UI (no-op when no
    // UI is bound). pd.listener is set to this once, in onCreate.
    private fun postToUi(action: (EcgListener) -> Unit) {
        if (uiListener == null) return
        handler.post { uiListener?.let(action) }
    }

    private val uiPoster = object : EcgListener {
        override fun onEcgSample(timeSec: Double, voltage: Double) = postToUi { it.onEcgSample(timeSec, voltage) }
        override fun onEcgBatchDone() = postToUi { it.onEcgBatchDone() }
        override fun onPeak(timeSec: Double, voltage: Double) = postToUi { it.onPeak(timeSec, voltage) }
        override fun onPeakReplaced(timeSec: Double, voltage: Double) = postToUi { it.onPeakReplaced(timeSec, voltage) }
        override fun onPvc(timeSec: Double, voltage: Double) = postToUi { it.onPvc(timeSec, voltage) }
        override fun onStats(hrBpm: Double, pvcAvePct: Double, pvcTotalPct: Double?, rrLastTimeSec: Double, pvcLastTimeSec: Double, enoughForPlot: Boolean) =
            postToUi { it.onStats(hrBpm, pvcAvePct, pvcTotalPct, rrLastTimeSec, pvcLastTimeSec, enoughForPlot) }
        override fun onConnectionChanged(connected: Boolean, deviceId: String) = postToUi { it.onConnectionChanged(connected, deviceId) }
        override fun onBatteryLevel(level: Int) = postToUi { it.onBatteryLevel(level) }
        override fun onRecordingChanged(recording: Boolean) = postToUi { it.onRecordingChanged(recording) }
    }

    private val api: PolarBleApi by lazy {
        PolarBleApiDefaultImpl.defaultImplementation(
            applicationContext,
            setOf(
                PolarBleApi.PolarBleSdkFeature.FEATURE_HR,
                PolarBleApi.PolarBleSdkFeature.FEATURE_POLAR_ONLINE_STREAMING,
                PolarBleApi.PolarBleSdkFeature.FEATURE_BATTERY_INFO,
                PolarBleApi.PolarBleSdkFeature.FEATURE_POLAR_DEVICE_TIME_SETUP,
                PolarBleApi.PolarBleSdkFeature.FEATURE_DEVICE_INFO)
        )
    }

    override fun onCreate() {
        super.onCreate()
        wd = WriteData(this)
        eventLog = EventLog(this)
        pd.listener = uiPoster  // PeakDetection runs off-main; deliver to UI via main
        createNotificationChannel()
        setupApiCallback()
        UploadWorker.schedulePeriodic(this)  // backstop for missed uploads
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // must go to foreground promptly after startForegroundService()
        startInForeground()

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        // don't clobber a live device id with the pref while connected/connecting
        if (!deviceConnected && !shouldBeConnected) {
            deviceId = prefs.getString(PREF_DEVICE_ID, DEFAULT_DEVICE_ID) ?: DEFAULT_DEVICE_ID
        }
        wd.deviceId = deviceId

        when (intent?.action) {
            ACTION_CONNECT -> connect()
            ACTION_DISCONNECT -> disconnect()
            ACTION_START_RECORDING -> startRecording()
            ACTION_STOP_RECORDING -> stopRecording()
            null -> {
                // service restarted by the system: resume if we were recording
                if (prefs.getBoolean(PREF_RECORDING, false) && currentFilePath() != "") {
                    Log.i(TAG, "Service restarted by system; resuming recording")
                    eventLog.open(currentFilePath())
                    eventLog.log("service_restarted", "resuming recording")
                    startRecording()
                } else {
                    stopSelfIfIdle()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Log.d(TAG, "Service destroyed")
        eventLog.log("service_destroyed")
        handler.removeCallbacksAndMessages(null)
        ecgDisposable?.dispose()
        ecgDisposable = null
        ecgExecutor.shutdown()
        wd.closeFile()
        eventLog.close()
        UploadWorker.enqueueNow(this)  // events file just closed; upload survives service death
        releaseWakeLock()
        getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
        api.shutDown()
        super.onDestroy()
    }

    // ---------- public control API (called from activity / actions) ----------

    fun connect() {
        if (shouldBeConnected) return
        shouldBeConnected = true
        reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS
        attemptConnect()
        handler.postDelayed(watchdog, WATCHDOG_INTERVAL_MS)
    }

    // safety net: the SDK can occasionally fail to deliver a disconnect
    // callback, leaving no scheduled reconnect; this catches that case
    private val watchdog = object : Runnable {
        override fun run() {
            if (!shouldBeConnected) return
            if (!deviceConnected && !reconnectPending) {
                eventLog.log("watchdog_reconnect")
                attemptConnect()
            }
            handler.postDelayed(this, WATCHDOG_INTERVAL_MS)
        }
    }

    fun disconnect() {
        shouldBeConnected = false
        reconnectPending = false
        handler.removeCallbacksAndMessages(null)
        if (isRecording) stopRecording()
        ecgDisposable?.dispose()
        ecgDisposable = null
        try {
            api.disconnectFromDevice(deviceId)
        } catch (ex: Exception) {
            Log.e(TAG, "disconnectFromDevice failed: $ex")
        }
        pd.clear()
        stopSelfIfIdle()
    }

    fun startRecording() {
        if (isRecording) return
        isRecording = true
        lastActivityTag = ""  // fresh session
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit().putBoolean(PREF_RECORDING, true).apply()
        acquireWakeLock()
        eventLog.open(currentFilePath())
        eventLog.log("recording_started", "device=$deviceId app_version=$appVersion")
        if (!shouldBeConnected) connect()
        uiListener?.onRecordingChanged(true)
        updateNotification(force = true)
    }

    fun stopRecording() {
        if (!isRecording) return
        isRecording = false
        lastActivityTag = ""  // no current session
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit().putBoolean(PREF_RECORDING, false).apply()
        wd.closeFile()
        wd.timeFileOpened = -1
        eventLog.log("recording_stopped")
        UploadWorker.enqueueNow(this)  // session boundary: upload what just closed
        releaseWakeLock()
        uiListener?.onRecordingChanged(false)
        updateNotification(force = true)
    }

    fun appForegrounded() {
        api.foregroundEntered()
    }

    /**
     * Record a user activity tag (work/gym/walk/…) into the session event log,
     * timestamped. Only the start of each activity is logged; the end is
     * inferred offline (next tag or HR change). Returns false if no session is
     * recording (the event file is only open while recording).
     */
    fun logActivity(label: String): Boolean {
        if (!eventLog.isOpen()) return false
        eventLog.log("activity", label)
        lastActivityTag = label
        return true
    }

    fun currentFilePath(): String {
        return getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_FILE_PATH, "") ?: ""
    }

    /** Immutable snapshot of the recent buffers for restoring the live plots. */
    class ReplaySnapshot(
        val ecgTimes: DoubleArray,
        val ecgVolts: DoubleArray,
        val beatTimes: DoubleArray,
        val beatVolts: DoubleArray,
        val beatIsPvc: BooleanArray,
        val hr: List<DoubleArray>,
        val pvc: List<DoubleArray>,
    )

    /**
     * Build a snapshot of the recent ECG / beat markers / trend history on the
     * ECG processing thread (so it is consistent with the writers, no locks),
     * then deliver it to [onMain] on the main thread for the UI to replay.
     */
    fun requestReplaySnapshot(maxEcgPoints: Int, onMain: (ReplaySnapshot) -> Unit) {
        try {
            ecgExecutor.execute {
                val data = pd.ecgData
                val newest = data.maxIndex() - 1
                val firstAvail = data.maxIndex() - data.size()
                val times: DoubleArray
                val volts: DoubleArray
                if (newest >= firstAvail && data.size() > 0) {
                    val start = maxOf(firstAvail, newest - maxEcgPoints + 1)
                    val n = newest - start + 1
                    times = DoubleArray(n)
                    volts = DoubleArray(n)
                    for (i in 0 until n) {
                        val g = start + i
                        times[i] = data.time.get(g) / 1e9
                        volts[i] = data.volt.get(g)
                    }
                } else {
                    times = DoubleArray(0); volts = DoubleArray(0)
                }
                val markers = pd.recentBeats
                val bt = DoubleArray(markers.size)
                val bv = DoubleArray(markers.size)
                val bp = BooleanArray(markers.size)
                for (i in markers.indices) {
                    bt[i] = markers[i].timeSec; bv[i] = markers[i].voltMv; bp[i] = markers[i].isPvc
                }
                val snap = ReplaySnapshot(times, volts, bt, bv, bp, ArrayList(hrHistory), ArrayList(pvcHistory))
                handler.post { onMain(snap) }
            }
        } catch (ex: Exception) {
            Log.w(TAG, "replay snapshot rejected: $ex")  // executor shutting down
        }
    }

    // ---------- BLE callbacks and reconnect ----------

    private fun setupApiCallback() {
        api.setPolarFilter(false)
        api.setApiCallback(object : PolarBleApiCallback() {
            override fun blePowerStateChanged(powered: Boolean) {
                Log.d(TAG, "BLE power: $powered")
                bluetoothEnabled = powered
                eventLog.log("ble_power", "$powered")
                if (powered && shouldBeConnected && !deviceConnected) {
                    scheduleReconnect("bluetooth back on")
                }
            }

            override fun deviceConnected(polarDeviceInfo: PolarDeviceInfo) {
                Log.d(TAG, "Connected: ${polarDeviceInfo.deviceId}")
                deviceId = polarDeviceInfo.deviceId
                wd.deviceId = deviceId
                getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .edit().putString(PREF_DEVICE_ID, deviceId).apply()
                deviceConnected = true
                reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS
                eventLog.log("connected", deviceId)
                uiPoster.onConnectionChanged(true, deviceId)
                updateNotification(force = true)
            }

            override fun deviceConnecting(polarDeviceInfo: PolarDeviceInfo) {
                Log.d(TAG, "Connecting: ${polarDeviceInfo.deviceId}")
            }

            override fun deviceDisconnected(polarDeviceInfo: PolarDeviceInfo) {
                Log.d(TAG, "Disconnected: ${polarDeviceInfo.deviceId}")
                deviceConnected = false
                eventLog.log("disconnected", polarDeviceInfo.deviceId)
                uiPoster.onConnectionChanged(false, deviceId)
                updateNotification(force = true)
                if (shouldBeConnected) {
                    scheduleReconnect("device disconnected")
                }
            }

            override fun disInformationReceived(identifier: String, uuid: UUID, value: String) {
                Log.i(TAG, "Dis Info uuid: $uuid value: $value")
            }

            override fun batteryLevelReceived(identifier: String, level: Int) {
                Log.d(TAG, "Battery Level: $level")
                batteryLevel = level
                uiPoster.onBatteryLevel(level)
                if (level <= 10) eventLog.log("sensor_battery_low", "$level")

                // also set the local time on the device
                val timeZone = TimeZone.getTimeZone("UTC")
                val calendar = Calendar.getInstance(timeZone)
                calendar.time = Date()
                api.setLocalTime(deviceId, calendar)
                    .observeOn(AndroidSchedulers.mainThread())
                    .subscribe(
                        { Log.d(TAG, "time ${calendar.time} set to device") },
                        { error: Throwable -> Log.e(TAG, "set time failed: $error") }
                    )
            }

            override fun bleSdkFeatureReady(identifier: String, feature: PolarBleApi.PolarBleSdkFeature) {
                Log.d(TAG, "feature ready $feature")
                when (feature) {
                    PolarBleApi.PolarBleSdkFeature.FEATURE_POLAR_ONLINE_STREAMING -> streamECG()
                    else -> {}
                }
            }
        })
    }

    private fun attemptConnect() {
        if (!shouldBeConnected || deviceConnected) return
        try {
            Log.d(TAG, "Attempting connection to $deviceId")
            api.connectToDevice(deviceId)
        } catch (ex: Exception) {
            Log.e(TAG, "connectToDevice failed: $ex")
            eventLog.log("connect_error", "$ex")
            scheduleReconnect("connect call failed")
        }
    }

    private fun scheduleReconnect(reason: String) {
        if (!shouldBeConnected || reconnectPending) return
        reconnectPending = true
        eventLog.log("reconnect_scheduled", "$reason; delay=${reconnectDelayMs}ms")
        handler.postDelayed({
            reconnectPending = false
            attemptConnect()
        }, reconnectDelayMs)
        reconnectDelayMs = min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS)
    }

    // ---------- streaming ----------

    private fun streamECG() {
        val isDisposed = ecgDisposable?.isDisposed ?: true
        if (!isDisposed) {
            ecgDisposable?.dispose()
            ecgDisposable = null
        }

        ecgDisposable = api.requestStreamSettings(deviceId, PolarBleApi.PolarDeviceDataType.ECG)
            .toFlowable()
            .flatMap { sensorSetting: PolarSensorSetting -> api.startEcgStreaming(deviceId, sensorSetting.maxSettings()) }
            .observeOn(Schedulers.from(ecgExecutor))  // process off the main thread
            .subscribe(
                { polarEcgData: PolarEcgData -> onEcgBatch(polarEcgData) },
                { error: Throwable ->
                    Log.e(TAG, "Ecg stream failed $error")
                    eventLog.log("stream_error", "$error")
                    ecgDisposable = null
                    // if still connected, restart the stream; if the device
                    // dropped, deviceDisconnected() handles the reconnect
                    handler.postDelayed({
                        if (deviceConnected && shouldBeConnected) {
                            eventLog.log("stream_restart")
                            streamECG()
                        }
                    }, STREAM_RESTART_DELAY_MS)
                },
                { Log.d(TAG, "Ecg stream complete") }
            )
    }

    private fun onEcgBatch(polarEcgData: PolarEcgData) {
        pd.processData(polarEcgData)

        if (isRecording) {
            val path = currentFilePath()
            if (path != "") {
                eventLog.open(path)  // no-op if already open; handles directory chosen after start
                val previousFileOpened = wd.timeFileOpened
                if (!wd.writeData(path, polarEcgData)) {
                    eventLog.log("write_error", "batch not written")
                } else if (wd.timeFileOpened != previousFileOpened && polarEcgData.samples.isNotEmpty()) {
                    // new hourly file: anchor the sensor clock (ECG timestamps)
                    // to the phone clock (event timestamps) for offline coverage
                    // accounting; the two can drift over weeks
                    val polarNs = polarEcgData.samples.first().timeStamp + PeakDetection.TIMESTAMP_OFFSET
                    eventLog.log(
                        "clock_sync",
                        "phone_ms=${Instant.now().toEpochMilli()} polar_ns=$polarNs file=${wd.lastFileName}"
                    )
                    // the previous hourly file just closed: upload it
                    if (previousFileOpened > 0) UploadWorker.enqueueNow(this)
                }
            }
        }

        if (pd.rrData.size() > 1) {
            val hrBpm: Double = 60.0 / pd.rrData.average()
            val pvcAve: Double = pd.pvcData.average() * 100.0
            val pvcTotalPct: Double? = if (pd.totalBeats > 0)
                pd.totalPVCs.toDouble() / pd.totalBeats * 100.0 else null

            lastHrBpm = hrBpm
            lastPvcAve = pvcAve

            // mirror the plotted trend points so the UI can replay them after
            // it was off-screen (these are populated whether or not the UI is bound)
            hrHistory.addLast(doubleArrayOf(pd.rrData.lastTime, hrBpm))
            pvcHistory.addLast(doubleArrayOf(pd.pvcData.lastTime, pvcAve))
            if (hrHistory.size > MAX_TREND_POINTS) hrHistory.removeFirst()
            if (pvcHistory.size > MAX_TREND_POINTS) pvcHistory.removeFirst()

            uiPoster.onStats(
                hrBpm, pvcAve, pvcTotalPct,
                pd.rrData.lastTime, pd.pvcData.lastTime,
                pd.rrData.size() > 10
            )
            updateNotification()
        }
    }

    // ---------- notification / foreground ----------

    private fun startInForeground() {
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            )
        } catch (ex: SecurityException) {
            // connectedDevice FGS requires BLUETOOTH_CONNECT granted on API 34+
            Log.e(TAG, "startForeground failed (missing permission?): $ex")
            stopSelf()
        }
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID, "ECG recording", NotificationManager.IMPORTANCE_LOW
        )
        channel.description = "Ongoing ECG recording status"
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val contentIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val status = when {
            !shouldBeConnected -> "Idle"
            !deviceConnected -> "Reconnecting to $deviceId..."
            isRecording && lastHrBpm > 0 ->
                "Recording  •  ${lastHrBpm.roundToInt()} bpm  •  ${lastPvcAve.roundToInt()}% PVC"
            isRecording -> "Recording"
            else -> "Connected to $deviceId"
        }

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("PolarPVC2")
            .setContentText(status)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun updateNotification(force: Boolean = false) {
        if (!shouldBeConnected && !isRecording) return  // not foreground; don't re-post
        val now = System.currentTimeMillis()
        if (!force && now - lastNotificationUpdate < NOTIFICATION_UPDATE_MS) return
        lastNotificationUpdate = now
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification())
    }

    private fun stopSelfIfIdle() {
        if (!shouldBeConnected && !isRecording) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
            stopSelf()
        }
    }

    // ---------- wake lock ----------

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PolarPVC2::recording")
        wakeLock?.setReferenceCounted(false)
        wakeLock?.acquire()
        Log.d(TAG, "Wake lock acquired")
    }

    private fun releaseWakeLock() {
        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
            Log.d(TAG, "Wake lock released")
        }
        wakeLock = null
    }
}
