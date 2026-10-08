package lk.ac.mrt.sar.nativemodule

import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** File helpers for the offline map: resumable HTTP download and SHA-256 (ARCHITECTURE.md section 8). */
object Files {
    private const val BUFFER = 256 * 1024
    private const val PROGRESS_EVERY_MS = 300L

    fun path(uriOrPath: String): File = File(uriOrPath.removePrefix("file://"))

    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buf = ByteArray(BUFFER)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    /**
     * Download [url] to [dest], resuming from "<dest>.part" with an HTTP Range request
     * if an earlier attempt was interrupted. Calls [progress] with (written, total).
     * Returns the final size. The process network binding (WifiBinder) applies here too.
     */
    fun download(url: String, dest: File, headers: Map<String, String>, progress: (Long, Long) -> Unit): Long {
        dest.parentFile?.mkdirs()
        val part = File(dest.path + ".part")
        var have = if (part.exists()) part.length() else 0L

        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 30_000
            headers.forEach { (k, v) -> setRequestProperty(k, v) }
            if (have > 0) setRequestProperty("Range", "bytes=$have-")
        }
        try {
            val total: Long
            val append: Boolean
            when (val code = conn.responseCode) {
                206 -> {
                    append = true
                    total = conn.getHeaderField("Content-Range")?.substringAfter('/')?.toLongOrNull() ?: -1L
                }
                200 -> {
                    append = false
                    have = 0L
                    total = conn.contentLengthLong
                }
                416 -> {
                    // Nothing left to fetch: the .part file is already complete.
                    val size = conn.getHeaderField("Content-Range")?.substringAfter('/')?.toLongOrNull() ?: -1L
                    if (size > 0 && part.length() == size) return finish(part, dest)
                    part.delete()
                    throw IOException("Server rejected the resume range; restart the download")
                }
                else -> throw IOException("HTTP $code from $url")
            }
            var written = have
            var lastReport = 0L
            FileOutputStream(part, append).use { out ->
                conn.inputStream.use { input ->
                    val buf = ByteArray(BUFFER)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        written += n
                        val now = System.currentTimeMillis()
                        if (now - lastReport >= PROGRESS_EVERY_MS) {
                            lastReport = now
                            progress(written, total)
                        }
                    }
                }
            }
            progress(written, total)
            if (total > 0 && part.length() != total) {
                throw IOException("Download stopped at ${part.length()} of $total bytes")
            }
            return finish(part, dest)
        } finally {
            conn.disconnect()
        }
    }

    private fun finish(part: File, dest: File): Long {
        if (dest.exists()) dest.delete()
        if (!part.renameTo(dest)) throw IOException("Could not move ${part.name} into place")
        return dest.length()
    }
}
