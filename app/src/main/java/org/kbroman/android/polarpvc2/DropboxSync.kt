package org.kbroman.android.polarpvc2

import android.content.Context
import android.util.Log
import com.dropbox.core.DbxRequestConfig
import com.dropbox.core.android.Auth
import com.dropbox.core.oauth.DbxCredential
import com.dropbox.core.v2.DbxClientV2

/**
 * Dropbox account linking and client construction.
 *
 * One-time OAuth PKCE flow (browser round-trip) yields a refresh token,
 * stored in shared prefs; DbxClientV2 then refreshes short-lived access
 * tokens automatically. The app key is supplied via dropbox.app.key in
 * local.properties (BuildConfig.DROPBOX_APP_KEY).
 */
object DropboxSync {
    private const val TAG = "PolarPVC2app_dropbox"
    private const val PREF_DROPBOX_CREDENTIAL = "PREF_DROPBOX_CREDENTIAL"

    private val requestConfig: DbxRequestConfig =
        DbxRequestConfig.newBuilder("PolarPVC2").build()

    fun isConfigured(): Boolean = BuildConfig.DROPBOX_APP_KEY != ""

    fun isLinked(context: Context): Boolean = readCredential(context) != null

    /** Launch the browser-based PKCE auth flow. */
    fun startAuth(context: Context) {
        Auth.startOAuth2PKCE(
            context, BuildConfig.DROPBOX_APP_KEY, requestConfig,
            // upload needs files.content.write; the pre-deletion remote
            // existence check (getMetadata) needs files.metadata.read
            listOf("files.content.write", "files.metadata.read")
        )
    }

    /**
     * Call from onResume after startAuth: persists the credential if the
     * flow just completed. Returns true if newly linked.
     */
    fun finishAuth(context: Context): Boolean {
        val credential = Auth.getDbxCredential() ?: return false
        prefs(context).edit()
            .putString(PREF_DROPBOX_CREDENTIAL, DbxCredential.Writer.writeToString(credential))
            .apply()
        Log.i(TAG, "Dropbox account linked")
        return true
    }

    /** Client with auto-refreshing credential, or null if not linked. */
    fun client(context: Context): DbxClientV2? {
        val credential = readCredential(context) ?: return null
        return DbxClientV2(requestConfig, credential)
    }

    private fun readCredential(context: Context): DbxCredential? {
        val serialized = prefs(context).getString(PREF_DROPBOX_CREDENTIAL, null) ?: return null
        return try {
            DbxCredential.Reader.readFully(serialized)
        } catch (ex: Exception) {
            Log.e(TAG, "Stored Dropbox credential unreadable: $ex")
            null
        }
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(RecordingService.PREFS_NAME, Context.MODE_PRIVATE)
}
