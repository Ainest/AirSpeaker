"""AirSpeaker menu bar application using rumps."""

from __future__ import annotations

import logging
import threading

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

        # Build initial menu
        self._devices_menu = rumps.MenuItem("デバイス一覧")
        self._stream_button = rumps.MenuItem(
            "ストリーミング開始", callback=self._toggle_streaming
        )
        self._status_item = rumps.MenuItem("状態: 待機中")
        self._status_item.set_callback(None)

        self._quit_button = rumps.MenuItem("終了", callback=self._on_quit)

        self.menu = [
            self._status_item,
            None,  # separator
            self._devices_menu,
            None,
            self._stream_button,
            None,
            self._quit_button,
        ]

        # Add initial "scanning" item and refresh button
        self._devices_menu.add(rumps.MenuItem("検索中...", callback=None))
        self._devices_menu.add(None)  # separator
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

        # Start initial discovery in background
        self._start_discovery()

    # ---- Device discovery ----

    def _start_discovery(self) -> None:
        """Kick off device discovery in background."""
        # Update menu to show scanning
        self._update_device_menu_scanning()
        self.cast.discover(callback=self._on_devices_found)

    def _on_refresh_devices(self, sender: rumps.MenuItem) -> None:
        self._start_discovery()

    def _on_devices_found(self, devices: list[CastDevice]) -> None:
        """Called from discovery thread when devices are found."""
        self._devices = devices
        # rumps UI updates must happen; rumps is thread-safe for menu updates
        self._update_device_menu(devices)

    def _update_device_menu_scanning(self) -> None:
        """Show 'scanning...' state in device submenu."""
        self._devices_menu.clear()
        self._devices_menu.add(rumps.MenuItem("検索中...", callback=None))
        self._devices_menu.add(None)
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

    def _update_device_menu(self, devices: list[CastDevice]) -> None:
        """Rebuild the device submenu with discovered devices."""
        self._devices_menu.clear()

        if not devices:
            self._devices_menu.add(rumps.MenuItem("デバイスが見つかりません", callback=None))
        else:
            for dev in devices:
                label = f"{dev.friendly_name}"
                if dev.model_name:
                    label += f" ({dev.model_name})"
                item = rumps.MenuItem(label, callback=self._on_device_selected)
                # Store uuid in the menu item's key for later retrieval
                item.representedObject = dev.uuid
                # Mark currently selected device
                if dev.uuid == self._selected_uuid:
                    item.state = True
                self._devices_menu.add(item)

        self._devices_menu.add(None)
        self._devices_menu.add(
            rumps.MenuItem("デバイスを再検索", callback=self._on_refresh_devices)
        )

    def _on_device_selected(self, sender: rumps.MenuItem) -> None:
        """Handle device selection from menu."""
        uuid = getattr(sender, "representedObject", None)
        if not uuid:
            return

        # Toggle selection
        if uuid == self._selected_uuid:
            # Deselect
            self._selected_uuid = None
            if self._streaming:
                self._stop_streaming()
        else:
            # Select new device
            was_streaming = self._streaming
            if was_streaming:
                self._stop_streaming()
            self._selected_uuid = uuid
            if was_streaming:
                self._start_streaming()

        # Refresh checkmarks
        self._update_device_menu(self._devices)

    # ---- Streaming control ----

    def _toggle_streaming(self, sender: rumps.MenuItem) -> None:
        if self._streaming:
            self._stop_streaming()
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
        self._stream_button.title = "停止中..."
        self._stream_button.set_callback(None)

        # Run connection in background to avoid blocking UI
        threading.Thread(target=self._start_streaming_bg, daemon=True).start()

    def _start_streaming_bg(self) -> None:
        try:
            # Start audio capture + HTTP server
            self.streamer.start()
            stream_url = self.streamer.stream_url

            # Connect to Chromecast and start playback
            success = self.cast.connect(self._selected_uuid, stream_url)

            if success:
                self._streaming = True
                device = self.cast.connected_device
                name = device.friendly_name if device else "Unknown"
                self._set_status(f"配信中: {name}")
                self._stream_button.title = "ストリーミング停止"
                self._stream_button.set_callback(self._toggle_streaming)
                self.title = "🔊▶"
            else:
                self.streamer.stop()
                self._set_status("接続失敗")
                self._stream_button.title = "ストリーミング開始"
                self._stream_button.set_callback(self._toggle_streaming)
                rumps.notification(
                    title=config.APP_NAME,
                    subtitle="接続エラー",
                    message="Chromecastへの接続に失敗しました。",
                )
        except Exception as e:
            logger.exception("Failed to start streaming")
            self.streamer.stop()
            self._set_status("エラー")
            self._stream_button.title = "ストリーミング開始"
            self._stream_button.set_callback(self._toggle_streaming)

    def _stop_streaming(self) -> None:
        self._streaming = False
        self.cast.disconnect()
        self.streamer.stop()
        self._set_status("待機中")
        self._stream_button.title = "ストリーミング開始"
        self._stream_button.set_callback(self._toggle_streaming)
        self.title = "🔊"

    def _set_status(self, text: str) -> None:
        self._status_item.title = f"状態: {text}"

    # ---- Cleanup ----

    def _on_quit(self, _: rumps.MenuItem) -> None:
        self._stop_streaming()
        rumps.quit_application()
