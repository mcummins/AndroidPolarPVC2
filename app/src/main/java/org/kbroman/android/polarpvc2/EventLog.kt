package org.kbroman.android.polarpvc2

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.DocumentsContract
import android.util.Log
import java.io.FileWriter
import java.io.PrintWriter
import java.time.Instant
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Append-only CSV log of session events (connect, disconnect, reconnect
 * attempts, stream errors, file errors). This is what makes coverage
 * accounting possible offline: gaps in the ECG record can be attributed
 * to disconnections rather than silently shrinking the denominator.
 *
 * One file per service start, in the same SAF tree as the ECG data.
 */
class EventLog(private val context: Context) {
    private var filePointer: ParcelFileDescriptor? = null
    private var writer: PrintWriter? = null

    companion object {
        private const val TAG = "PolarPVC2app_events"
    }

    fun open(treeUriString: String) {
        if (writer != null || treeUriString == "") return
        try {
            val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss")
            val fileName = "events_${LocalDateTime.now().format(formatter)}.csv"

            val dirUri = Uri.parse(treeUriString)
            val documentId = DocumentsContract.getTreeDocumentId(dirUri)
            val docTreeUri = DocumentsContract.buildDocumentUriUsingTree(dirUri, documentId)
            val resolver = context.contentResolver
            val docUri = DocumentsContract.createDocument(resolver, docTreeUri, "text/csv", fileName)
            filePointer = resolver.openFileDescriptor(docUri!!, "w")
            writer = PrintWriter(FileWriter(filePointer!!.fileDescriptor))
            writer?.write("time,event,detail\n")
            writer?.flush()
            Log.d(TAG, "opened event log $docUri")
        } catch (ex: Exception) {
            Log.e(TAG, "Failed to open event log: $ex")
            writer = null
            filePointer = null
        }
    }

    fun log(event: String, detail: String = "") {
        Log.d(TAG, "$event $detail")
        try {
            writer?.write("${Instant.now()},${event},${detail}\n")
            writer?.flush()
        } catch (ex: Exception) {
            Log.e(TAG, "Failed to write event: $ex")
        }
    }

    fun close() {
        try {
            writer?.flush()
            writer?.close()
            filePointer?.close()
        } catch (ex: Exception) {
            Log.e(TAG, "Error closing event log: $ex")
        } finally {
            writer = null
            filePointer = null
        }
    }
}
