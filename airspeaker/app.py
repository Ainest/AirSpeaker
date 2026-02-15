"""AirSpeaker menu bar application using rumps.

All UI updates are dispatched to the main thread via a queue + rumps.Timer,
because macOS/Cocoa requires menu manipulation on the main thread only.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

import rumps

from . import config
from .audio_streamer import AudioStreamer
from .cast_controller import CastController, CastDevice

logger = logging.getLogger(__name__)


class AirSpeakerApp(rumps.App):
    """macOS menu bar app for streaming audio to Chromecast devices."""

    def __init__(self) -> None:
        super().__init__(config.APP_NAME, title="🔊", quit_button=None)

        self.streamer = AudioStreamer()
        self.cast = CastController()
        self._streaming = False
        self._selected_uuid: str | None = None
        self._devices: list[CastDevice] = []

        # Queue for dispatching UI updates to the main thread
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        # Build initial menu
        self._devices_menu = rumps.MenuItem("デバイス一覧")
        self._stream_button = rumps.MenuItem(
            "ストリーミング開始", callback=self._toggle_streaming
        )
        self._status_item = rumps.MenuItem("状態: 待機中")
        self._status_item.set_callback(None)
        self._quit_item = rumps.MenuItem("終了", callback=self._on_quit)

        self.menu = [
            self._status_item,
            None,
            self._devices_menu,
            None,
            self._stream_button,
            None,
            self._quit_item,
        ]

        # Initial submenu
        self._devices_menu.add(rumps.MenuItem("検索中...", callback=None))
        self._devices_menu.add(None)
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

        # Timer that drains the UI queue on the main thread (every 0.3s)
        self._ui_timer = rumps.Timer(self._drain_ui_queue, 0.3)
        self._ui_timer.start()

        # Start initial discovery
        self._start_discovery()

    # ---- Main-thread dispatch ----

    def _run_on_main(self, fn: Callable[[], None]) -> None:
        """Schedule a callable to run on the main thread."""
        self._ui_queue.put(fn)

    def _drain_ui_queue(self, _: Any) -> None:
        """Called by rumps.Timer on the main thread; process pending UI work."""
        while not self._ui_queue.empty():
            try:
                fn = self._ui_queue.get_nowait()
                fn()
            except queue.Empty:
                break
            except Exception:
                logger.exception("Error in UI update")

    # ---- Device discovery ----

    def _start_discovery(self) -> None:
        self._update_device_menu_scanning()
        self.cast.discover(callback=self._on_devices_found)

    def _on_refresh_devices(self, sender: rumps.MenuItem) -> None:
        self._start_discovery()

    def _on_devices_found(self, devices: list[CastDevice]) -> None:
        """Called from discovery thread. Dispatch UI update to main thread."""
        self._devices = devices
        self._run_on_main(lambda: self._update_device_menu(devices))

    def _update_device_menu_scanning(self) -> None:
        self._devices_menu.clear()
        self._devices_menu.add(rumps.MenuItem("検索中...", callback=None))
        self._devices_menu.add(None)
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

    def _update_device_menu(self, devices: list[CastDevice]) -> None:
        self._devices_menu.clear()

        if not devices:
            self._devices_menu.add(
                rumps.MenuItem("デバイスが見つかりません", callback=None)
            )
        else:
            for dev in devices:
                label = dev.friendly_name
                if dev.model_name:
                    label += f" ({dev.model_name})"
                item = rumps.MenuItem(label, callback=self._on_device_selected)
                item.representedObject = dev.uuid
                if dev.uuid == self._selected_uuid:
                    item.state = True
                self._devices_menu.add(item)

        self._devices_menu.add(None)
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

    def _on_device_selected(self, sender: rumps.MenuItem) -> None:
        uuid = getattr(sender, "representedObject", None)
        if not uuid:
            return

        if uuid == self._selected_uuid:
            self._selected_uuid = None
            if self._streaming:
                threading.Thread(target=self._stop_streaming_bg, daemon=True).start()
        else:
            was_streaming = self._streaming
            if was_streaming:
                self._stop_streaming_sync()
            self._selected_uuid = uuid
            if was_streaming:
                self._start_streaming()

        self._update_device_menu(self._devices)

    # ---- Streaming control ----

    def _toggle_streaming(self, sender: rumps.MenuItem) -> None:
        if self._streaming:
            self._set_status("停止中...")
            threading.Thread(target=self._stop_streaming_bg, daemon=True).start()
        else:
            self._start_streaming()

    def _start_streaming(self) -> None:
        if not self._selected_uuid:
            rumps.alert(
                title=config.APP_NAME,
                message="先にデバイスを選択してください。",
            )
            return

        self._set_status("開始中...")
        self._stream_button.title = "接続中..."
        self._stream_button.set_callback(None)

        threading.Thread(target=self._start_streaming_bg, daemon=True).start()

    def _start_streaming_bg(self) -> None:
        """Background thread: start streamer + connect to Chromecast."""
        try:
            self.streamer.start()
            stream_url = self.streamer.stream_url
            success = self.cast.connect(self._selected_uuid, stream_url)

            if success:
                self._streaming = True
                device = self.cast.connected_device
                name = device.friendly_name if device else "Unknown"
                self._run_on_main(lambda: self._apply_streaming_started(name))
            else:
                self.streamer.stop()
                self._run_on_main(self._apply_streaming_failed)
        except Exception:
            logger.exception("Failed to start streaming")
            self.streamer.stop()
            self._run_on_main(self._apply_streaming_error)

    def _stop_streaming_bg(self) -> None:
        """Background thread: stop streaming."""
        self._stop_streaming_sync()
        self._run_on_main(self._apply_streaming_stopped)

    def _stop_streaming_sync(self) -> None:
        """Stop streaming (can be called from any thread, no UI touches)."""
        self._streaming = False
        self.cast.disconnect()
        self.streamer.stop()

    # ---- UI state updates (main thread only) ----

    def _apply_streaming_started(self, device_name: str) -> None:
        self._set_status(f"配信中: {device_name}")
        self._stream_button.title = "ストリーミング停止"
        self._stream_button.set_callback(self._toggle_streaming)
        self.title = "🔊▶"

    def _apply_streaming_stopped(self) -> None:
        self._set_status("待機中")
        self._stream_button.title = "ストリーミング開始"
        self._stream_button.set_callback(self._toggle_streaming)
        self.title = "🔊"

    def _apply_streaming_failed(self) -> None:
        self._set_status("接続失敗")
        self._stream_button.title = "ストリーミング開始"
        self._stream_button.set_callback(self._toggle_streaming)
        rumps.notification(
            title=config.APP_NAME,
            subtitle="接続エラー",
            message="Chromecastへの接続に失敗しました。",
        )

    def _apply_streaming_error(self) -> None:
        self._set_status("エラー")
        self._stream_button.title = "ストリーミング開始"
        self._stream_button.set_callback(self._toggle_streaming)

    def _set_status(self, text: str) -> None:
        self._status_item.title = f"状態: {text}"

    # ---- Cleanup ----

    def _on_quit(self, _: rumps.MenuItem) -> None:
        self._streaming = False
        self.cast.disconnect()
        self.streamer.stop()
        rumps.quit_application()
