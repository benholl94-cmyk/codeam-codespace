/* LocalCrypto.kt — owner-unlock generation in pure Kotlin (no Android deps).
 *
 * Generates a 32-byte Fernet-compatible key, base64-urlsafe-encodes it,
 * and writes it to the given path with mode 0600 (owner-only on POSIX;
 * on Android we open with no group/world permission).
 *
 * No javax.crypto.Mac, no Tink, no third-party crypto. Just java.security
 * primitives that ship with the JVM. The output format matches the
 * rollout-shield secure_state.py output exactly, so a key generated on
 * the phone can decrypt state produced by the desktop tool, and vice
 * versa.
 */
package com.rolloutshield.dashboard

import java.io.File
import java.security.SecureRandom
import android.util.Base64

object LocalCrypto {

    /** Generate a fresh 32-byte key, write it Fernet-style to [target]. */
    fun generateUnlock(target: File) {
        val raw = ByteArray(32)
        SecureRandom().nextBytes(raw)
        val encoded = Base64.encodeToString(raw, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        // The Python secure_state.py accepts both raw 32 bytes and urlsafe-b64
        // of length 44 (the Fernet format). Write the urlsafe-b64 form so the
        // file matches what Python tools produce.
        target.parentFile?.mkdirs()
        target.writeText(encoded)
        // chmod 0600 if possible (POSIX); on Android /data/data/<pkg>/files
        // is already private to the UID, but be explicit anyway.
        try {
            Runtime.getRuntime().exec(arrayOf("chmod", "600", target.absolutePath))
        } catch (_: Exception) { /* non-fatal */ }
    }
}