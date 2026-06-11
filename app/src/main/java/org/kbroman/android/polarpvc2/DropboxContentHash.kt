package org.kbroman.android.polarpvc2

import java.io.File
import java.security.MessageDigest

/**
 * Dropbox content_hash: SHA-256 of each 4MB block, then SHA-256 of the
 * concatenated block digests, hex-encoded. Comparing this against the
 * hash Dropbox returns after upload proves the remote bytes match the
 * local ones before we allow local deletion.
 * https://www.dropbox.com/developers/reference/content-hash
 */
object DropboxContentHash {
    private const val BLOCK_SIZE = 4 * 1024 * 1024

    fun ofFile(file: File): String {
        val overall = MessageDigest.getInstance("SHA-256")
        val block = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            var bytesInBlock = 0
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                var off = 0
                while (off < n) {
                    val take = minOf(n - off, BLOCK_SIZE - bytesInBlock)
                    block.update(buf, off, take)
                    bytesInBlock += take
                    off += take
                    if (bytesInBlock == BLOCK_SIZE) {
                        overall.update(block.digest())  // digest() also resets
                        bytesInBlock = 0
                    }
                }
            }
            if (bytesInBlock > 0) overall.update(block.digest())
        }
        return overall.digest().joinToString("") { "%02x".format(it) }
    }
}
