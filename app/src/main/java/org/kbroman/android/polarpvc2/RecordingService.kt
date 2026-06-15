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
import java.time.Instant
import java.util.Calendar
import java.util.Date
import java.util.TimeZone
import java.util.UUID
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
    val hrHistory = ArrayList<DoubleArray>()      // [timeSec, bpm]
    val pvcHistory = ArrayList<DoubleArray>()     // [timeSec, pvcAvePct]
    private val MAX_TREND_POINTS = 150 * 60 * 25  // matches the plotters' cap

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

    /** UI attaches/detaches here; null while in background. */
    var uiListener: EcgListener? = null
        set(value) {
            field = value
            pd.listener = value
            // push current state so the UI can sync immediately
            value?.onConnectionChanged(deviceConnected, deviceId)
            value?.onRecordingChanged(isRecording)
            if (batteryLevel >= 0) value?.onBatteryLevel(batteryLevel)
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
        return true
    }

    fun currentFilePath(): String {
        return getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_FILE_PATH, "") ?: ""
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
                uiListener?.onConnectionChanged(true, deviceId)
                updateNotification(force = true)
            }

            override fun deviceConnecting(polarDeviceInfo: PolarDeviceInfo) {
                Log.d(TAG, "Connecting: ${polarDeviceInfo.deviceId}")
            }

            override fun deviceDisconnected(polarDeviceInfo: PolarDeviceInfo) {
                Log.d(TAG, "Disconnected: ${polarDeviceInfo.deviceId}")
                deviceConnected = false
                eventLog.log("disconnected", polarDeviceInfo.deviceId)
                uiListener?.onConnectionChanged(false, deviceId)
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
                uiListener?.onBatteryLevel(level)
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
            .observeOn(AndroidSchedulers.mainThread())
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
            hrHistory.add(doubleArrayOf(pd.rrData.lastTime, hrBpm))
            pvcHistory.add(doubleArrayOf(pd.pvcData.lastTime, pvcAve))
            if (hrHistory.size > MAX_TREND_POINTS) hrHistory.removeAt(0)
            if (pvcHistory.size > MAX_TREND_POINTS) pvcHistory.removeAt(0)

            uiListener?.onStats(
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
