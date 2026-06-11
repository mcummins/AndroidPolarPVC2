package org.kbroman.android.polarpvc2

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.DocumentsContract
import android.util.Log
import com.polar.sdk.api.model.PolarEcgData
import java.io.FileWriter
import java.io.PrintWriter
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class WriteData(private val context: Context) {
    var timeFileOpened: Long = -1
    var lastFileName: String = ""
        private set
    /** set by the service on connect; recorded in each file's metadata line */
    var deviceId: String = ""
    private var filePointer: ParcelFileDescriptor? = null
    private var fileWriter: PrintWriter? = null
    private var lastOpenAttempt: Long = -1

    private val appVersion: String by lazy {
        try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "unknown"
        } catch (ex: Exception) {
            "unknown"
        }
    }

    companion object {
        private const val TAG = "PolarPVC2app_write"
        private const val HOUR_IN_MILLI = 1000*60*60
        private const val MIN_REOPEN_INTERVAL_MILLI = 30_000L  // avoid file-creation storm on persistent failure
        // H10 ECG stream rate; PeakDetection's buffer sizes assume the same value
        const val SAMPLE_RATE_HZ = 130
        // UTC filenames: local-time names are ambiguous across the DST fall-back hour
        val FILE_TIME_FORMATTER: DateTimeFormatter =
            DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss").withZone(ZoneOffset.UTC)
    }

    /** Returns true if the batch was written successfully. */
    fun writeData(filePath: String, polarEcgData: PolarEcgData): Boolean
    {
        if(filePath == "") return false  // don't yet have a directory

        val currentTimeStamp: Long = Instant.now().toEpochMilli()
        val timeSinceOpen = currentTimeStamp - timeFileOpened
        if(timeFileOpened < 0 || timeSinceOpen > HOUR_IN_MILLI || fileWriter == null) {
            // rate-limit reopen attempts so a persistent failure doesn't
            // create a new (empty) document every batch
            if (fileWriter == null && lastOpenAttempt > 0 &&
                currentTimeStamp - lastOpenAttempt < MIN_REOPEN_INTERVAL_MILLI) {
                return false
            }
            closeFile()
            lastOpenAttempt = currentTimeStamp
            try {
                openFile(filePath)
            } catch (ex: Exception) {
                Log.e(TAG, "Failed to open data file: $ex")
                timeFileOpened = -1
                return false
            }
        }

        // write data to the file: raw integer microvolts, lossless as delivered
        // by the sensor (unit conversion belongs in the analysis pipeline)
        for (data in polarEcgData.samples) {
            val timestamp = data.timeStamp + PeakDetection.TIMESTAMP_OFFSET

            fileWriter?.write("${timestamp},${data.voltage}\n")
        }

        // flush each batch (~2 KB/s) so at most one batch is lost on crash
        fileWriter?.flush()

        // PrintWriter swallows IOExceptions; checkError() is the only signal.
        if (fileWriter?.checkError() == true) {
            Log.e(TAG, "Write error on data file; will reopen on next batch")
            closeFile()
            timeFileOpened = -1  // force reopen on next batch
            return false
        }
        return true
    }

    private fun openFile(filePath: String)
    {
        Log.d(TAG, "Opening file")

        val fileName = getFileName()

        val dirUri = Uri.parse(filePath)
        val documentId = DocumentsContract.getTreeDocumentId(dirUri)
        val docTreeUri = DocumentsContract.buildDocumentUriUsingTree(dirUri, documentId)
        val resolver = context.contentResolver
        val docUri = DocumentsContract.createDocument(resolver, docTreeUri, "text/csv", fileName)
        filePointer = resolver.openFileDescriptor(docUri!!, "w")
        val writer = FileWriter(filePointer!!.fileDescriptor)
        fileWriter = PrintWriter(writer)

        timeFileOpened = Instant.now().toEpochMilli()
        lastFileName = fileName
        Log.d(TAG, "opened file $docUri")
        fileWriter?.write("# device=${deviceId} app_version=${appVersion} sample_rate_hz=${SAMPLE_RATE_HZ} ecg_units=uV\n")
        fileWriter?.write("time,ecg_uV\n")
    }

    private fun getFileName(): String
    {
        val currentTime = FILE_TIME_FORMATTER.format(Instant.now())

        return "ecg_${currentTime}Z.csv"
    }

    fun closeFile() {
        try {
            if(fileWriter != null) {
                Log.d(TAG, "Flushing and closing file")
                fileWriter!!.flush()
                fileWriter!!.close()
            }
            if(filePointer != null) {
                filePointer!!.close()
            }
        } catch (ex: Exception) {
            Log.e(TAG, "Error closing file: $ex")
        } finally {
            fileWriter = null
            filePointer = null
        }
    }
}
