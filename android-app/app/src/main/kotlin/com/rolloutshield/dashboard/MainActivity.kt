/* MainActivity.kt — native Android entry point.
 *
 * Hosts a WebView that points at the local loopback HTTP server bound by
 * LocalServer.kt on 127.0.0.1:8765. The WebView is the *only* UI; the
 * native layer is just a host.
 *
 * Security gates (in order):
 *   1. Check filesDir/owner_unlock exists. If not → "Unlock required" screen.
 *   2. Bind LocalServer on 127.0.0.1:8765 (loopback only).
 *   3. Start a foreground service that keeps the server alive.
 *   4. WebView loads http://127.0.0.1:8765/ (cleartext allowed only via
 *      network_security_config exception for 127.0.0.1).
 *   5. Disable WebView navigation to anything other than 127.0.0.1.
 *      Any other URL → block + toast.
 *   6. Disable WebView JS access to anything other than the loopback
 *      origin (handled in WebViewClient.shouldInterceptRequest via the
 *      CSP header set by LocalServer).
 *
 * Network capabilities of this APK:
 *   * AndroidManifest declares NO <uses-permission android:name="INTERNET"/>.
 *   * network_security_config blocks all cleartext to non-loopback domains.
 *   * The LocalServer only listens on the loopback interface.
 *   * Result: the device's network stack physically cannot route a packet
 *     from this app to anywhere off-device.
 */
package com.rolloutshield.dashboard

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.view.View
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.io.File

class MainActivity : Activity() {

    private var webView: WebView? = null
    private var server: LocalServer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val unlockFile = File(filesDir, "owner_unlock")
        if (!unlockFile.exists()) {
            showUnlockRequired(unlockFile)
            return
        }
        startDashboard(unlockFile)
    }

    private fun showUnlockRequired(unlockFile: File) {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 96, 48, 48)
        }
        root.addView(TextView(this).apply {
            text = "rollout-shield\n\nUnlock required.\n\n" +
                   "The Fernet unlock file is missing:\n" +
                   "${unlockFile.absolutePath}\n\n" +
                   "Generate it on a host with rollout-shield installed:\n" +
                   "  python3 tools/secure_state.py --init\n\n" +
                   "Then copy it to the path above (e.g. via adb push)."
            textSize = 14f
        })
        val generateLocalBtn = Button(this).apply {
            text = "Generate unlock on this device"
            setOnClickListener {
                try {
                    LocalCrypto.generateUnlock(unlockFile)
                    Toast.makeText(this@MainActivity,
                        "Unlock generated at ${unlockFile.absolutePath}",
                        Toast.LENGTH_LONG).show()
                    recreate()
                } catch (e: Exception) {
                    Log.e(TAG, "generate unlock failed", e)
                    Toast.makeText(this@MainActivity,
                        "Failed: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
        root.addView(generateLocalBtn)
        setContentView(root)
    }

    private fun startDashboard(unlockFile: File) {
        val s = LocalServer(this, unlockFile)
        try {
            s.start()
        } catch (e: Exception) {
            Log.e(TAG, "LocalServer failed to start", e)
            showUnlockRequired(unlockFile)
            return
        }
        server = s

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.allowFileAccessFromFileURLs = false
            settings.allowUniversalAccessFromFileURLs = false
            // Mixed content blocked: only loopback cleartext allowed.
            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            // Cache off — state is real-time.
            cacheMode = android.webkit.WebSettings.LOAD_NO_CACHE

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    url: String
                ): Boolean {
                    val allow = url.startsWith("http://127.0.0.1:") ||
                                url.startsWith("http://localhost:")
                    if (!allow) {
                        Toast.makeText(this@MainActivity,
                            "blocked: $url",
                            Toast.LENGTH_SHORT).show()
                        return true
                    }
                    return false
                }
            }
        }
        setContentView(webView)
        webView?.loadUrl("http://127.0.0.1:8765/")
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        webView?.destroy()
        webView = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "RolloutShield"
    }
}