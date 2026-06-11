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
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

class WriteData(private val context: Context) {
    var timeFileOpened: Long = -1
    private var filePointer: ParcelFileDescriptor? = null
    private var fileWriter: PrintWriter? = null
    private var lastOpenAttempt: Long = -1

    companion object {
        private const val TAG = "PolarPVC2app_write"
        private const val HOUR_IN_MILLI = 1000*60*60
        private const val MIN_REOPEN_INTERVAL_MILLI = 30_000L  // avoid file-creation storm on persistent failure
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

        // write data to the file
        for (data in polarEcgData.samples) {
            val voltage: Double = (data.voltage.toFloat() / 1000.0)
            val timestamp = data.timeStamp + PeakDetection.TIMESTAMP_OFFSET

            fileWriter?.write("${timestamp},${voltage}\n")
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
        Log.d(TAG, "opened file $docUri")
        fileWriter?.write("time,ecg\n")
    }

    private fun getFileName(): String
    {
        val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss")
        val currentTime = LocalDateTime.now().format(formatter)

        return "ecg_${currentTime}.csv"
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
