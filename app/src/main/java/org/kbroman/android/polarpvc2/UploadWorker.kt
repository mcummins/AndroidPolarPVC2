package org.kbroman.android.polarpvc2

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.dropbox.core.InvalidAccessTokenException
import com.dropbox.core.NetworkIOException
import com.dropbox.core.v2.DbxClientV2
import com.dropbox.core.v2.files.FileMetadata
import com.dropbox.core.v2.files.GetMetadataErrorException
import com.dropbox.core.v2.files.WriteMode
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.util.Date
import java.util.concurrent.TimeUnit
import java.util.zip.GZIPOutputStream

/**
 * Uploads closed data files (ecg_*.csv, events_*.csv) from the SAF data
 * folder to Dropbox, gzipped, then deletes local copies once they are
 * verified remote and older than RETENTION_MS.
 *
 * A file counts as uploaded only if the content_hash Dropbox reports
 * matches one computed locally, so deletion never trusts an unverified
 * transfer; right before deleting, the remote file's existence is checked
 * again (if it vanished, it is re-uploaded instead). Verified names are
 * remembered in prefs so each file uploads once. Files currently open for
 * writing (recorded in prefs by WriteData/EventLog) are skipped — they'll
 * be picked up after rotation or recording stop.
 *
 * Enqueued on every hourly file rotation and on recording stop, plus a
 * periodic backstop; WorkManager's network constraint and backoff handle
 * offline stretches.
 */
class UploadWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    companion object {
        private const val TAG = "PolarPVC2app_upload"
        private const val PREF_UPLOADED_FILES = "PREF_UPLOADED_FILES"
        private const val UNIQUE_NOW = "dropbox-upload"
        private const val UNIQUE_PERIODIC = "dropbox-upload-periodic"
        private const val MAX_ATTEMPTS = 8  // give up until next trigger (~exp backoff to hours)
        // keep local copies this long after verified upload (~1GB buffer
        // at ~340MB/day) before deleting to free phone storage
        private const val RETENTION_MS = 3L * 24 * 60 * 60 * 1000

        private val networkConstraint = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        /** Upload pending files as soon as the network allows. */
        fun enqueueNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<UploadWorker>()
                .setConstraints(networkConstraint)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            // APPEND_OR_REPLACE: if a run is in flight, chain another after it
            // so a file closed mid-run isn't missed until the next trigger
            WorkManager.getInstance(context).enqueueUniqueWork(
                UNIQUE_NOW, ExistingWorkPolicy.APPEND_OR_REPLACE, request
            )
        }

        /** Backstop for anything missed (crashes, long offline periods). */
        fun schedulePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<UploadWorker>(6, TimeUnit.HOURS)
                .setConstraints(networkConstraint)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                UNIQUE_PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request
            )
        }
    }

    override fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(
            RecordingService.PREFS_NAME, Context.MODE_PRIVATE
        )
        val treePath = prefs.getString(RecordingService.PREF_FILE_PATH, "") ?: ""
        if (treePath == "") return Result.success()  // no data folder chosen yet

        val client = DropboxSync.client(applicationContext)
            ?: return Result.success()  // Dropbox not linked; nothing to do

        val openFiles = setOf(
            prefs.getString(RecordingService.PREF_OPEN_ECG_FILE, "") ?: "",
            prefs.getString(RecordingService.PREF_OPEN_EVENTS_FILE, "") ?: ""
        )
        val uploaded = prefs.getStringSet(PREF_UPLOADED_FILES, emptySet())!!.toMutableSet()

        val localFiles = listDataFiles(treePath)
        if (localFiles.isEmpty()) return Result.success()

        // drop registry entries for files no longer present locally,
        // so the pref set doesn't grow without bound
        val localNames = localFiles.map { it.name }.toSet()
        uploaded.retainAll(localNames)

        var retryNeeded = false
        for (file in localFiles.sortedBy { it.name }) {
            if (file.name in uploaded || file.name in openFiles) continue
            try {
                upload(file)
                uploaded.add(file.name)
                Log.i(TAG, "Uploaded ${file.name}")
            } catch (ex: InvalidAccessTokenException) {
                Log.e(TAG, "Dropbox auth invalid; relink needed: $ex")
                prefs.edit().putStringSet(PREF_UPLOADED_FILES, uploaded).apply()
                return Result.failure()
            } catch (ex: NetworkIOException) {
                Log.w(TAG, "Network error uploading ${file.name}: $ex")
                retryNeeded = true
                break  // no point trying the rest right now
            } catch (ex: Exception) {
                // unexpected (corrupt file, Dropbox API error): skip this
                // file, try the others, and retry the run later
                Log.e(TAG, "Failed to upload ${file.name}: $ex")
                retryNeeded = true
            }
        }

        // free phone storage: delete local files verified on Dropbox and
        // older than the retention window
        val now = System.currentTimeMillis()
        for (file in localFiles) {
            if (file.name !in uploaded || file.name in openFiles) continue
            if (now - file.lastModified < RETENTION_MS) continue
            when (remoteFileExists(client, file.name)) {
                true -> if (deleteLocal(file)) uploaded.remove(file.name)
                false -> {
                    // remote copy vanished since upload: put it back in the
                    // queue rather than deleting the only copy
                    Log.w(TAG, "${file.name} missing on Dropbox; will re-upload")
                    uploaded.remove(file.name)
                    retryNeeded = true
                }
                null -> {}  // couldn't check (network?); leave for next run
            }
        }

        prefs.edit().putStringSet(PREF_UPLOADED_FILES, uploaded).apply()

        return when {
            !retryNeeded -> Result.success()
            runAttemptCount < MAX_ATTEMPTS -> Result.retry()
            else -> {
                Log.e(TAG, "Giving up after $runAttemptCount attempts; next trigger will retry")
                Result.success()
            }
        }
    }

    private class DataFile(val name: String, val uri: Uri, val lastModified: Long)

    private fun listDataFiles(treePath: String): List<DataFile> {
        val treeUri = Uri.parse(treePath)
        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(
            treeUri, DocumentsContract.getTreeDocumentId(treeUri)
        )
        val files = mutableListOf<DataFile>()
        try {
            applicationContext.contentResolver.query(
                childrenUri,
                arrayOf(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                    DocumentsContract.Document.COLUMN_LAST_MODIFIED
                ),
                null, null, null
            )?.use { cursor ->
                while (cursor.moveToNext()) {
                    val docId = cursor.getString(0)
                    val name = cursor.getString(1) ?: continue
                    val isData = (name.startsWith("ecg_") || name.startsWith("events_")) &&
                            name.endsWith(".csv")
                    if (!isData) continue
                    files.add(
                        DataFile(
                            name,
                            DocumentsContract.buildDocumentUriUsingTree(treeUri, docId),
                            cursor.getLong(2)
                        )
                    )
                }
            }
        } catch (ex: Exception) {
            Log.e(TAG, "Failed to list data folder: $ex")
        }
        return files
    }

    private fun upload(file: DataFile) {
        val client = DropboxSync.client(applicationContext)!!
        val gzFile = File(applicationContext.cacheDir, "${file.name}.gz")
        try {
            applicationContext.contentResolver.openInputStream(file.uri)!!.use { input ->
                GZIPOutputStream(gzFile.outputStream()).use { gz -> input.copyTo(gz) }
            }
            val localHash = DropboxContentHash.ofFile(gzFile)
            val metadata = FileInputStream(gzFile).use { input ->
                // OVERWRITE makes re-upload after a lost "uploaded" mark harmless
                client.files().uploadBuilder("/${file.name}.gz")
                    .withMode(WriteMode.OVERWRITE)
                    .withClientModified(Date(file.lastModified))
                    .uploadAndFinish(input)
            }
            // only a hash-verified upload may ever justify local deletion
            if (metadata.contentHash != localHash) {
                throw IOException("content hash mismatch after upload of ${file.name}")
            }
        } finally {
            gzFile.delete()
        }
    }

    /** True/false if Dropbox answered, null if the check itself failed. */
    private fun remoteFileExists(client: DbxClientV2, name: String): Boolean? {
        return try {
            client.files().getMetadata("/$name.gz") is FileMetadata
        } catch (ex: GetMetadataErrorException) {
            if (ex.errorValue.isPath && ex.errorValue.pathValue.isNotFound) false else null
        } catch (ex: Exception) {
            Log.w(TAG, "Couldn't check remote copy of $name: $ex")
            null
        }
    }

    private fun deleteLocal(file: DataFile): Boolean {
        return try {
            DocumentsContract.deleteDocument(applicationContext.contentResolver, file.uri)
            Log.i(TAG, "Deleted local ${file.name} (verified on Dropbox, retention passed)")
            true
        } catch (ex: Exception) {
            Log.e(TAG, "Failed to delete local ${file.name}: $ex")
            false
        }
    }
}
