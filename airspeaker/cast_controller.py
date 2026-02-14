"""Chromecast device discovery and streaming control via pychromecast."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import pychromecast
import zeroconf as zc

from . import config

logger = logging.getLogger(__name__)


@dataclass
class CastDevice:
    """Lightweight representation of a discovered Cast device."""

    friendly_name: str
    model_name: str
    host: str
    uuid: str

    @classmethod
    def from_cast_info(cls, info: pychromecast.CastInfo) -> CastDevice:
        return cls(
            friendly_name=info.friendly_name or "Unknown",
            model_name=info.model_name or "",
            host=info.host or "",
            uuid=str(info.uuid),
        )


class CastController:
    """Discovers and controls Chromecast devices."""

    def __init__(self) -> None:
        self._browser: pychromecast.CastBrowser | None = None
        self._cast: pychromecast.Chromecast | None = None
        self._stream_url: str = ""
        self._connected_uuid: str | None = None
        self._reconnect_thread: threading.Thread | None = None
        self._should_reconnect = False
        self._discovered_devices: dict[str, CastDevice] = {}
        self._discovery_callback: Callable[[list[CastDevice]], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._cast is not None and self._connected_uuid is not None

    @property
    def connected_device(self) -> CastDevice | None:
        if self._connected_uuid:
            return self._discovered_devices.get(self._connected_uuid)
        return None

    # ---- Discovery ----

    def discover(
        self,
        callback: Callable[[list[CastDevice]], None] | None = None,
        timeout: float = config.CAST_DISCOVERY_TIMEOUT,
    ) -> list[CastDevice]:
        """Discover Chromecast devices on the network.

        If callback is provided, it will be called when discovery completes
        (runs in a background thread). Otherwise blocks and returns results.
        """
        if callback:
            self._discovery_callback = callback
            t = threading.Thread(target=self._discover_blocking, args=(timeout,), daemon=True)
            t.start()
            return []
        return self._discover_blocking(timeout)

    def _discover_blocking(self, timeout: float) -> list[CastDevice]:
        logger.info("Discovering Chromecast devices (timeout=%ds)...", timeout)
        try:
            zconf = zc.Zeroconf()
            listener = pychromecast.SimpleCastListener()
            browser = pychromecast.CastBrowser(listener, zconf)
            browser.start_discovery()
            time.sleep(timeout)
            browser.stop_discovery()

            self._discovered_devices.clear()
            for uuid, info in browser.devices.items():
                dev = CastDevice.from_cast_info(info)
                self._discovered_devices[dev.uuid] = dev

            zconf.close()

            devices = list(self._discovered_devices.values())
            logger.info("Found %d device(s): %s", len(devices),
                        [d.friendly_name for d in devices])

            if self._discovery_callback:
                self._discovery_callback(devices)

            return devices
        except Exception:
            logger.exception("Discovery failed")
            if self._discovery_callback:
                self._discovery_callback([])
            return []

    # ---- Connection ----

    def connect(self, uuid: str, stream_url: str) -> bool:
        """Connect to a Chromecast device and start playing the stream."""
        self.disconnect()

        device = self._discovered_devices.get(uuid)
        if not device:
            logger.error("Device %s not found in discovered list", uuid)
            return False

        self._stream_url = stream_url

        try:
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[device.friendly_name],
                discovery_timeout=config.CAST_DISCOVERY_TIMEOUT,
            )
            if not chromecasts:
                logger.error("Could not connect to %s", device.friendly_name)
                pychromecast.discovery.stop_discovery(browser)
                return False

            self._browser = browser
            self._cast = chromecasts[0]
            self._cast.wait()
            self._connected_uuid = uuid

            # Start playback
            self._play_stream()

            # Enable auto-reconnect
            self._should_reconnect = True
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop, daemon=True
            )
            self._reconnect_thread.start()

            logger.info("Connected to %s", device.friendly_name)
            return True

        except Exception:
            logger.exception("Failed to connect to %s", device.friendly_name)
            self._cleanup_cast()
            return False

    def disconnect(self) -> None:
        """Disconnect from the current Chromecast."""
        self._should_reconnect = False
        if self._cast:
            try:
                self._cast.media_controller.stop()
                self._cast.quit_app()
            except Exception:
                pass
        self._cleanup_cast()
        logger.info("Disconnected")

    def _cleanup_cast(self) -> None:
        if self._browser:
            try:
                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:
                pass
            self._browser = None
        if self._cast:
            try:
                self._cast.disconnect()
            except Exception:
                pass
            self._cast = None
        self._connected_uuid = None

    def _play_stream(self) -> None:
        """Tell the Chromecast to play our HTTP stream."""
        if not self._cast:
            return
        mc = self._cast.media_controller
        mc.play_media(
            self._stream_url,
            "audio/mpeg",
            stream_type="LIVE",
        )
        mc.block_until_active(timeout=10)
        logger.info("Streaming to %s: %s", self._cast.name, self._stream_url)

    # ---- Auto-reconnect ----

    def _reconnect_loop(self) -> None:
        """Monitor connection and reconnect if playback stops."""
        # Grace period: let Chromecast buffer and start playing
        grace_until = time.monotonic() + 15
        idle_count = 0

        while self._should_reconnect:
            time.sleep(config.CAST_RETRY_INTERVAL)
            if not self._should_reconnect:
                break
            if not self._cast:
                break

            # Skip checks during grace period
            if time.monotonic() < grace_until:
                continue

            try:
                mc = self._cast.media_controller
                status = mc.status
                if status and status.player_state in ("IDLE", "UNKNOWN"):
                    idle_count += 1
                    # Only restart after 2 consecutive IDLE checks
                    if idle_count >= 2:
                        logger.info("Playback stopped (state=%s), restarting stream...",
                                    status.player_state)
                        self._play_stream()
                        grace_until = time.monotonic() + 15
                        idle_count = 0
                else:
                    idle_count = 0
            except Exception:
                logger.warning("Reconnect check failed, will retry...")
