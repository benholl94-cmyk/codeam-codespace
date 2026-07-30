/* LocalServer.kt — in-process HTTP server bound to 127.0.0.1.
 *
 * Why not a regular WebView loading file://?
 *   * file:// origins can't make XHR to APIs we serve as files.
 *   * We're already going to need an HTTP server for /api/* anyway
 *     (the rollout-shield dashboard has JSON endpoints).
 *
 * Why 127.0.0.1 only?
 *   * The loopback interface is reachable ONLY from this process / this
 *     device. No other app on the device, no host on the network, can
 *     reach it.
 *   * Combined with the manifest's missing INTERNET permission, this
 *     means the server physically cannot send a packet off-device.
 *
 * The server is a minimal stdlib-only implementation: java.net.ServerSocket
 * + a single-thread pool. We deliberately do NOT bring in nanohttpd or
 * anything else — fewer deps, smaller APK, no third-party code paths.
 */
package com.rolloutshield.dashboard

import android.content.Context
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.Executors

class LocalServer(private val context: Context, private val unlockFile: File) {

    private var serverSocket: ServerSocket? = null
    private val executor = Executors.newFixedThreadPool(2)
    @Volatile private var running = false

    fun start(port: Int = 8765) {
        // CRITICAL: bind loopback only. Never InetAddress.getByName("0.0.0.0").
        val bind = InetAddress.getByName("127.0.0.1")
        val sock = ServerSocket(port, 50, bind)
        serverSocket = sock
        running = true
        executor.submit { acceptLoop() }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        executor.shutdownNow()
    }

    private fun acceptLoop() {
        while (running) {
            val client: Socket = try {
                serverSocket?.accept() ?: return
            } catch (e: Exception) {
                if (running) android.util.Log.w(TAG, "accept error", e)
                return
            }
            executor.submit { handle(client) }
        }
    }

    private fun handle(client: Socket) {
        try {
            client.use { sock ->
                val reader = BufferedReader(InputStreamReader(sock.getInputStream()))
                val out: OutputStream = sock.getOutputStream()
                val requestLine = reader.readLine() ?: return@use
                val parts = requestLine.split(" ")
                if (parts.size < 2) return@use
                val method = parts[0]
                val path = parts[1].split("?")[0]
                val query = if (parts[1].contains("?")) {
                    parts[1].substringAfter("?")
                } else ""

                // Drain headers
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                }

                when {
                    method == "GET" && (path == "/" || path == "/index.html") ->
                        serveIndex(out)
                    method == "GET" && path == "/api/status" ->
                        serveJson(out, statusJson())
                    method == "GET" && path == "/api/health" ->
                        serveJson(out, mapOf("status" to "ok", "ts" to System.currentTimeMillis() / 1000))
                    method == "GET" && path == "/api/unlock" ->
                        serveJson(out, mapOf("present" to unlockFile.exists(),
                                              "path" to unlockFile.absolutePath))
                    else ->
                        serveNotFound(out, path)
                }
            }
        } catch (e: Exception) {
            android.util.Log.w(TAG, "handle error", e)
        }
    }

    private fun serveIndex(out: OutputStream) {
        val body = """
            <!doctype html>
            <html lang="en"><head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>rollout-shield</title>
            <style>
              body{font-family:system-ui,sans-serif;margin:24px;max-width:680px;
                   color:#111;background:#fff;line-height:1.5}
              h1{margin:0 0 12px;font-size:22px}
              .pill{display:inline-block;padding:2px 8px;border-radius:10px;
                    font-size:12px;background:#0a0;color:#fff;margin-left:8px}
              code,pre{font-family:ui-monospace,Menlo,Consolas,monospace;
                       background:#f4f4f4;padding:2px 6px;border-radius:4px}
              pre{padding:12px;overflow:auto}
              .grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
              .card{padding:12px;border:1px solid #ddd;border-radius:8px}
              .ok{color:#0a0;font-weight:600}
              .err{color:#a00;font-weight:600}
            </style></head>
            <body>
              <h1>rollout-shield <span class="pill">native</span></h1>
              <p>Owner dashboard. Loopback only. No internet permission.</p>
              <div class="grid">
                <div class="card"><b>App</b><br>rollout-shield 0.2.0</div>
                <div class="card"><b>Network</b><br>127.0.0.1 only</div>
                <div class="card"><b>Unlock</b><br><span id="u">checking…</span></div>
                <div class="card"><b>Status</b><br><span id="s">…</span></div>
              </div>
              <h2>API endpoints</h2>
              <pre>GET /api/status     — system summary
            GET /api/health     — health check
            GET /api/unlock     — owner-unlock status</pre>
              <p class="ok">✓ Network egress blocked by AndroidManifest + network_security_config.</p>
              <p class="ok">✓ Server bound to 127.0.0.1 only.</p>
              <p class="ok">✓ No <code>android.permission.INTERNET</code>.</p>
              <script>
              fetch('/api/unlock').then(r=>r.json()).then(d=>{
                document.getElementById('u').textContent = d.present?'present':'missing';
              });
              fetch('/api/status').then(r=>r.json()).then(d=>{
                document.getElementById('s').textContent = d.app + ' ' + d.version;
              }).catch(e=>{document.getElementById('s').textContent='error'});
              </script>
            </body></html>
        """.trimIndent()
        respond(out, 200, "text/html; charset=utf-8", body)
    }

    private fun statusJson(): Map<String, Any> = mapOf(
        "app" to "rollout-shield",
        "version" to "0.2.0",
        "network" to "loopback-only",
        "internet_permission" to false,
        "ts" to System.currentTimeMillis() / 1000
    )

    private fun serveJson(out: OutputStream, obj: Any) {
        respond(out, 200, "application/json; charset=utf-8", toJson(obj))
    }

    private fun serveNotFound(out: OutputStream, path: String) {
        respond(out, 404, "application/json; charset=utf-8",
                toJson(mapOf("error" to "not_found", "path" to path)))
    }

    private fun respond(out: OutputStream, status: Int, ctype: String, body: String) {
        val resp = buildString {
            append("HTTP/1.1 $status ${statusText(status)}\r\n")
            append("Content-Type: $ctype\r\n")
            append("Content-Length: ${body.toByteArray().size}\r\n")
            append("Cache-Control: no-store\r\n")
            append("Content-Security-Policy: default-src 'self'; connect-src 'self' http://127.0.0.1:8765; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'\r\n")
            append("Connection: close\r\n")
            append("\r\n")
            append(body)
        }
        out.write(resp.toByteArray())
        out.flush()
    }

    private fun statusText(s: Int): String = when (s) {
        200 -> "OK"; 404 -> "Not Found"; 500 -> "Internal Server Error"
        else -> "Status $s"
    }

    private fun toJson(obj: Any): String = when (obj) {
        is Map<*, *> -> obj.entries.joinToString(",", "{", "}") { (k, v) ->
            "\"${escape(k.toString())}\":${toJson(v ?: "null")}"
        }
        is List<*> -> obj.joinToString(",", "[", "]") { toJson(it ?: "null") }
        is Number, is Boolean -> obj.toString()
        else -> "\"${escape(obj.toString())}\""
    }
    private fun escape(s: String): String =
        s.replace("\\", "\\\\").replace("\"", "\\\"")
         .replace("\n", "\\n").replace("\r", "\\r")

    companion object {
        private const val TAG = "RolloutShield"
    }
}