package org.kbroman.android.polarpvc2

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.ActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.androidplot.xy.XYPlot
import org.kbroman.android.polarpvc2.databinding.ActivityMainBinding
import kotlin.math.pow
import kotlin.math.round

class MainActivity : AppCompatActivity(), EcgListener {
    private lateinit var binding: ActivityMainBinding
    private var ECGplot: XYPlot? = null
    private var HRplot: XYPlot? = null
    private var PVCplot: XYPlot? = null

    var ecgPlotter: ECGplotter? = null
    var hrPlotter: HRplotter? = null
    var pvcPlotter: PVCplotter? = null

    private var service: RecordingService? = null
    private var bound = false
    private var suppressSwitchCallbacks = false
    private var pendingRecordStart = false
    private var awaitingDropboxAuth = false

    companion object {
        private const val TAG = "PolarPVC2app_main"
        private const val PERMISSION_REQUEST_CODE = 1
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as RecordingService.LocalBinder).getService()
            service?.uiListener = this@MainActivity
            service?.appForegrounded()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        binding = ActivityMainBinding.inflate(layoutInflater)
        val view = binding.root

        setContentView(view)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) // keep screen on while viewing

        ECGplot = findViewById(R.id.ecgplot)
        HRplot = findViewById(R.id.hrplot)
        PVCplot = findViewById(R.id.pvcplot)

        migrateOldPreferences()

        binding.connectSwitch.setOnCheckedChangeListener { _, isChecked ->
            if (suppressSwitchCallbacks) return@setOnCheckedChangeListener
            if (isChecked) {
                Log.d(TAG, "Opening connection")
                binding.deviceTextView.text = "Connecting..."
                binding.batteryTextView.text = "Battery level..."
                sendServiceAction(RecordingService.ACTION_CONNECT)
            } else {
                Log.d(TAG, "Closing connection")
                sendServiceAction(RecordingService.ACTION_DISCONNECT)
                binding.deviceTextView.text = ""
                binding.batteryTextView.text = ""
            }
        }

        binding.recordSwitch.setOnCheckedChangeListener { _, isChecked ->
            if (suppressSwitchCallbacks) return@setOnCheckedChangeListener
            if (isChecked) {
                Log.d(TAG, "Starting recording")

                if (!binding.connectSwitch.isChecked) {
                    binding.connectSwitch.isChecked = true // triggers ACTION_CONNECT
                }

                requestBatteryOptimizationExemption()

                val prefs = getSharedPreferences(RecordingService.PREFS_NAME, MODE_PRIVATE)
                val filePath = prefs.getString(RecordingService.PREF_FILE_PATH, "") ?: ""
                if (filePath == "") {
                    // defer the actual start until a directory has been chosen
                    pendingRecordStart = true
                    chooseDataDirectory()
                } else {
                    sendServiceAction(RecordingService.ACTION_START_RECORDING)
                }
            } else {
                Log.d(TAG, "Stopping recording")
                pendingRecordStart = false
                sendServiceAction(RecordingService.ACTION_STOP_RECORDING)
            }
        }

        binding.dropboxTextView.setOnClickListener {
            when {
                !DropboxSync.isConfigured() ->
                    showToast("No Dropbox app key; set dropbox.app.key in local.properties and rebuild")
                !DropboxSync.isLinked(this) -> {
                    awaitingDropboxAuth = true
                    DropboxSync.startAuth(this)
                }
                else -> {
                    showToast("Dropbox linked; uploading pending files")
                    UploadWorker.enqueueNow(this)
                }
            }
        }
        updateDropboxLabel()

        requestNeededPermissions()
    }

    private fun updateDropboxLabel() {
        binding.dropboxTextView.text = if (DropboxSync.isLinked(this))
            getString(R.string.dropbox_linked_text) else getString(R.string.dropbox_link_text)
    }

    private fun requestNeededPermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            perms.add(Manifest.permission.BLUETOOTH_SCAN)
            perms.add(Manifest.permission.BLUETOOTH_CONNECT)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            perms.add(Manifest.permission.ACCESS_FINE_LOCATION)
        } else {
            perms.add(Manifest.permission.ACCESS_COARSE_LOCATION)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        requestPermissions(perms.toTypedArray(), PERMISSION_REQUEST_CODE)
    }

    // recording for weeks requires exemption from battery optimization,
    // otherwise Doze will eventually kill the BLE connection
    private fun requestBatteryOptimizationExemption() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                val intent = Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")
                )
                startActivity(intent)
            } catch (ex: Exception) {
                Log.e(TAG, "Battery optimization exemption request failed: $ex")
            }
        }
    }

    // file path used to be stored in activity-private prefs; move it to
    // shared prefs so the service can read it
    private fun migrateOldPreferences() {
        val newPrefs = getSharedPreferences(RecordingService.PREFS_NAME, MODE_PRIVATE)
        if (newPrefs.getString(RecordingService.PREF_FILE_PATH, "") == "") {
            val oldPath = getPreferences(MODE_PRIVATE).getString("PREF_FILE_PATH", "") ?: ""
            if (oldPath != "") {
                newPrefs.edit().putString(RecordingService.PREF_FILE_PATH, oldPath).apply()
                Log.d(TAG, "Migrated file path pref")
            }
        }
    }

    private fun sendServiceAction(action: String) {
        // a connectedDevice foreground service may not start without
        // BLUETOOTH_CONNECT on API 31+; avoid crashing the service
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
        ) {
            showToast("Bluetooth permission needed first")
            requestNeededPermissions()
            return
        }
        val intent = Intent(this, RecordingService::class.java)
        intent.action = action
        ContextCompat.startForegroundService(this, intent)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            for (index in 0..grantResults.lastIndex) {
                if (grantResults[index] == PackageManager.PERMISSION_DENIED) {
                    Log.w(TAG, "Permission denied: ${permissions[index]}")
                    showToast("Missing permission: ${permissions[index]}")
                    return
                }
            }
            Log.d(TAG, "Needed permissions are granted")
        }
    }

    override fun onStart() {
        super.onStart()
        bindService(
            Intent(this, RecordingService::class.java),
            serviceConnection, Context.BIND_AUTO_CREATE
        )
        bound = true
    }

    override fun onStop() {
        super.onStop()
        service?.uiListener = null
        if (bound) {
            unbindService(serviceConnection)
            bound = false
        }
        // NOTE: the service keeps recording in the background
    }

    public override fun onResume() {
        super.onResume()
        service?.appForegrounded()

        // complete a pending Dropbox auth; checked whenever unlinked (not
        // just when awaiting) because the activity may have been recreated
        // during the browser round-trip
        if (!DropboxSync.isLinked(this) && DropboxSync.finishAuth(this)) {
            showToast("Dropbox linked")
            UploadWorker.enqueueNow(this)
            updateDropboxLabel()
        } else if (awaitingDropboxAuth) {
            showToast("Dropbox linking not completed")
        }
        awaitingDropboxAuth = false

        if (ecgPlotter == null) {
            ECGplot!!.post({
                ecgPlotter = ECGplotter(this, ECGplot) })
        }
        if (hrPlotter == null) {
            HRplot!!.post({
                hrPlotter = HRplotter(this, HRplot) })
        }
        if (pvcPlotter == null) {
            PVCplot!!.post({
                pvcPlotter = PVCplotter(this, PVCplot) })
        }
    }

    private fun showToast(message: String) {
        val toast = Toast.makeText(applicationContext, message, Toast.LENGTH_LONG)
        toast.show()
    }

    // ---------- EcgListener (called by the service on the main thread) ----------

    override fun onEcgSample(timeSec: Double, voltage: Double) {
        ecgPlotter?.addValues(timeSec, voltage)
    }

    override fun onEcgBatchDone() {
        ecgPlotter?.updatePlot = true
        ecgPlotter?.update()
    }

    override fun onPeak(timeSec: Double, voltage: Double) {
        ecgPlotter?.addPeakValue(timeSec, voltage)
    }

    override fun onPeakReplaced(timeSec: Double, voltage: Double) {
        ecgPlotter?.replaceLastPeakValue(timeSec, voltage)
    }

    override fun onPvc(timeSec: Double, voltage: Double) {
        ecgPlotter?.addPVCValue(timeSec, voltage)
    }

    override fun onStats(
        hrBpm: Double,
        pvcAvePct: Double,
        pvcTotalPct: Double?,
        rrLastTimeSec: Double,
        pvcLastTimeSec: Double,
        enoughForPlot: Boolean
    ) {
        binding.pvcTextView.text = "${Math.round(pvcAvePct)}% pvc"
        if (pvcTotalPct != null) {
            binding.pvcTotalTextView.text = "(${Math.round(pvcTotalPct)}% total)"
        }
        binding.hrTextView.text = "${Math.round(hrBpm)} bpm"

        if (enoughForPlot) {
            hrPlotter?.addValues(rrLastTimeSec, hrBpm)
            pvcPlotter?.addValues(pvcLastTimeSec, pvcAvePct)
        }
    }

    override fun onConnectionChanged(connected: Boolean, deviceId: String) {
        suppressSwitchCallbacks = true
        binding.connectSwitch.isChecked = connected || (service?.shouldBeConnected ?: false)
        suppressSwitchCallbacks = false

        binding.deviceTextView.text = when {
            connected -> deviceId
            service?.shouldBeConnected == true -> "Reconnecting..."
            else -> ""
        }
        if (!connected) binding.batteryTextView.text = ""
    }

    override fun onBatteryLevel(level: Int) {
        binding.batteryTextView.text = "Battery level $level"
    }

    override fun onRecordingChanged(recording: Boolean) {
        suppressSwitchCallbacks = true
        binding.recordSwitch.isChecked = recording
        suppressSwitchCallbacks = false
    }

    // ---------- output directory selection ----------

    private val openDirectoryLauncher = registerForActivityResult<Intent, ActivityResult>(
        ActivityResultContracts.StartActivityForResult()
    ) { result: ActivityResult ->
        var savedPath = false
        try {
            if (result.resultCode == RESULT_OK) {
                // Get Uri from Storage Access Framework.
                val uri = result.data!!.data
                Log.d(TAG, "got a filePath: $uri")

                val editor = getSharedPreferences(RecordingService.PREFS_NAME, MODE_PRIVATE).edit()
                if (uri == null) {
                    editor.putString(RecordingService.PREF_FILE_PATH, null)
                    editor.apply()
                }
                try {
                    this.contentResolver.takePersistableUriPermission(
                        uri!!,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION or
                                Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    )
                    editor.putString(RecordingService.PREF_FILE_PATH, uri.toString())
                    editor.apply()
                    savedPath = true
                } catch (ex: Exception) {
                    Log.d(TAG, "Failed to save persistent uri permission")
                }
            }
        } catch (ex: Exception) {
            Log.e(TAG, "Error in openDirectory")
        }

        if (pendingRecordStart) {
            pendingRecordStart = false
            if (savedPath) {
                sendServiceAction(RecordingService.ACTION_START_RECORDING)
            } else {
                // picker cancelled/failed: don't pretend to record
                showToast("No folder chosen; recording not started")
                suppressSwitchCallbacks = true
                binding.recordSwitch.isChecked = false
                suppressSwitchCallbacks = false
            }
        }
    }

    private fun chooseDataDirectory() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        intent.addFlags(
            Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        )
        openDirectoryLauncher.launch(intent)
    }
}

fun myround(value: Double, digits: Int): String {
    val tens: Double = if(digits < 0) 10.0.pow(-digits) else 10.0.pow(digits)

    if(digits == 0) {
        return round(value).toInt().toString()
    } else if(digits < 0) {
        return (round(value/tens) *tens).toInt().toString()
    } else {
        return (round(value*tens) /tens).toString()
    }
}
