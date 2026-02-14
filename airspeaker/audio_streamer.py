"""Audio capture via ffmpeg and HTTP streaming server.

Flow: BlackHole → ffmpeg (MP3 encode) → stdout pipe → HTTP server → Chromecast
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared MP3 stream broadcaster
# ---------------------------------------------------------------------------

class StreamBroadcaster:
    """Distributes MP3 chunks from ffmpeg to multiple HTTP clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, list[bytes]] = {}
        self._next_id = 0

    def register(self) -> int:
        """Register a new client, returns client id."""
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._clients[cid] = []
            return cid

    def unregister(self, cid: int) -> None:
        with self._lock:
            self._clients.pop(cid, None)

    def push(self, data: bytes) -> None:
        """Push an MP3 chunk to all registered clients."""
        with self._lock:
            for buf in self._clients.values():
                buf.append(data)

    def pull(self, cid: int, timeout: float = 1.0) -> bytes | None:
        """Pull pending data for a client (blocking poll)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                buf = self._clients.get(cid)
                if buf is None:
                    return None  # client gone
                if buf:
                    data = b"".join(buf)
                    buf.clear()
                    return data
            time.sleep(0.02)
        return b""  # timeout, return empty to keep connection alive


# ---------------------------------------------------------------------------
# HTTP request handler for /stream
# ---------------------------------------------------------------------------

class _StreamHandler(BaseHTTPRequestHandler):
    """Serves the live MP3 stream."""

    broadcaster: StreamBroadcaster  # set by AudioStreamer

    def do_GET(self) -> None:
        if self.path != config.STREAM_ENDPOINT:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("icy-name", "AirSpeaker")
        self.end_headers()

        cid = self.broadcaster.register()
        logger.info("HTTP client connected (id=%d)", cid)

        try:
            while True:
                chunk = self.broadcaster.pull(cid, timeout=2.0)
                if chunk is None:
                    break  # client was unregistered
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.broadcaster.unregister(cid)
            logger.info("HTTP client disconnected (id=%d)", cid)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Suppress default stderr logging
        pass


# ---------------------------------------------------------------------------
# Main AudioStreamer class
# ---------------------------------------------------------------------------

class AudioStreamer:
    """Manages ffmpeg capture and HTTP streaming server."""

    def __init__(self, device: str = config.BLACKHOLE_DEVICE) -> None:
        self.device = device
        self.broadcaster = StreamBroadcaster()
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._reader_thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._running = False

    @property
    def stream_url(self) -> str:
        ip = get_lan_ip()
        return f"http://{ip}:{config.STREAM_PORT}{config.STREAM_ENDPOINT}"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_http_server()
        self._start_ffmpeg()
        logger.info("AudioStreamer started → %s", self.stream_url)

    def stop(self) -> None:
        self._running = False
        self._stop_ffmpeg()
        self._stop_http_server()
        logger.info("AudioStreamer stopped")

    # -- ffmpeg management --

    def _build_ffmpeg_cmd(self) -> list[str]:
        cmd = []
        for part in config.FFMPEG_CMD_TEMPLATE:
            cmd.append(part.replace("{device}", self.device))
        return cmd

    def _start_ffmpeg(self) -> None:
        cmd = self._build_ffmpeg_cmd()
        logger.debug("Starting ffmpeg: %s", " ".join(cmd))
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader_thread = threading.Thread(
            target=self._read_ffmpeg_output, daemon=True
        )
        self._reader_thread.start()

    def _stop_ffmpeg(self) -> None:
        proc = self._ffmpeg_proc
        if proc is None:
            return
        self._ffmpeg_proc = None
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _read_ffmpeg_output(self) -> None:
        """Read MP3 data from ffmpeg stdout and push to broadcaster."""
        proc = self._ffmpeg_proc
        if proc is None or proc.stdout is None:
            return
        try:
            while self._running and proc.poll() is None:
                data = proc.stdout.read(4096)
                if not data:
                    break
                self.broadcaster.push(data)
        except Exception:
            logger.exception("ffmpeg reader error")
        finally:
            if self._running:
                logger.warning("ffmpeg process ended unexpectedly")

    # -- HTTP server management --

    def _start_http_server(self) -> None:
        handler = type(
            "Handler",
            (_StreamHandler,),
            {"broadcaster": self.broadcaster},
        )
        server = ThreadingHTTPServer(
            ("0.0.0.0", config.STREAM_PORT), handler
        )
        server.allow_reuse_address = True
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._http_server = server
        self._server_thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        )
        self._server_thread.start()
        logger.info("HTTP server listening on port %d", config.STREAM_PORT)

    def _stop_http_server(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            try:
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_lan_ip() -> str:
    """Get the LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def list_avfoundation_audio_devices() -> list[str]:
    """List available AVFoundation audio input devices via ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # ffmpeg outputs device list to stderr
        lines = result.stderr.splitlines()
        devices = []
        in_audio = False
        for line in lines:
            if "AVFoundation audio devices:" in line:
                in_audio = True
                continue
            if in_audio:
                if line.strip().startswith("[AVFoundation"):
                    # Extract device name: [AVFoundation ...] [0] DeviceName
                    parts = line.split("]")
                    if len(parts) >= 3:
                        name = parts[-1].strip()
                        devices.append(name)
                else:
                    break
        return devices
    except Exception:
        return []
