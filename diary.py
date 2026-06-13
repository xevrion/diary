#!/usr/bin/env python3
"""diary - a minimal daily video/audio diary recorder.

Records from the webcam (video mode) or microphone (audio mode),
saves to ~/diary/YYYY-MM-DD_HH-MM.mp4 and immediately uploads the
result to YouTube as a private video.
"""

import json
import math
import os
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")

from gi.repository import Gtk, Gdk, Gst, GstApp, GLib, GObject, Gio

Gst.init(None)

DIARY_DIR = os.path.expanduser("~/diary")
CONFIG_PATH = os.path.join(DIARY_DIR, "config.json")
VIDEO_DEVICE = "/dev/video0"

WINDOW_WIDTH = 600
WINDOW_HEIGHT = 500


def ensure_diary_dir():
    os.makedirs(DIARY_DIR, exist_ok=True)


def ensure_config():
    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "client_secrets_path": None,
            "token_path": None,
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(default_config, f, indent=4)
        print(f"Created default config at {CONFIG_PATH}")
        print(
            "Place your Google OAuth client_secrets.json at "
            "~/diary/client_secrets.json before recording, "
            "or set 'client_secrets_path' in config.json."
        )


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], check=False)
    except FileNotFoundError:
        pass


def has_audio_input():
    """Check whether a usable audio input device is available."""
    src = Gst.ElementFactory.make("autoaudiosrc", "probe-audio-src")
    if src is None:
        return False
    try:
        src.set_state(Gst.State.READY)
        ret, _state, _pending = src.get_state(Gst.SECOND)
        return ret == Gst.StateChangeReturn.SUCCESS
    except Exception:
        return False
    finally:
        src.set_state(Gst.State.NULL)


def list_video_devices():
    """Return a list of (display_name, /dev/videoN path) for cameras."""
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Video/Source", None)
    monitor.start()
    devices = []
    try:
        for dev in monitor.get_devices():
            props = dev.get_properties()
            path = props.get_value("api.v4l2.path") if props else None
            if not path:
                continue
            devices.append((dev.get_display_name(), path))
    finally:
        monitor.stop()
    return devices


def list_audio_input_devices():
    """Return a list of (display_name, pulse_node_name) for microphones."""
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Audio/Source", None)
    monitor.start()
    devices = []
    try:
        for dev in monitor.get_devices():
            props = dev.get_properties()
            node_name = props.get_value("node.name") if props else None
            if not node_name:
                continue
            devices.append((dev.get_display_name(), node_name))
    finally:
        monitor.stop()
    return devices


class DiaryWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="diary")

        self.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.set_resizable(False)

        # Force dark theme
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        # State
        self.mode = "video"  # "video" or "audio"
        self.recording = False
        self.pipeline = None
        self.preview_pipeline = None
        self.timer_seconds = 0
        self.timer_source_id = None
        self.current_output_path = None
        self.video_device_ok = os.path.exists(VIDEO_DEVICE)
        self.audio_device_ok = has_audio_input()
        self.level_amplitude = 0.0
        self.waveform_history = [0.0] * 120

        # Device enumeration
        self.video_devices = list_video_devices()
        self.audio_devices = list_audio_input_devices()
        self.selected_video_device = (
            self.video_devices[0][1] if self.video_devices else VIDEO_DEVICE
        )
        self.selected_audio_device = (
            self.audio_devices[0][1] if self.audio_devices else None
        )

        self._build_ui()
        self._apply_css()

        if not self.video_device_ok:
            self._show_error_dialog(
                "No webcam found",
                f"{VIDEO_DEVICE} was not found. Video mode has been disabled.",
            )
            self.mode = "audio"

        if not self.audio_device_ok:
            self._show_error_dialog(
                "No microphone found",
                "No audio input device was found. Audio mode has been disabled.",
            )
            if not self.video_device_ok:
                # Neither device available; leave UI in place but recording
                # will fail loudly if attempted.
                pass
            else:
                self.mode = "video"

        self.video_mode_btn.set_sensitive(self.video_device_ok)
        self.audio_mode_btn.set_sensitive(self.audio_device_ok)

        self._update_mode_ui()

        if self.mode == "video" and self.video_device_ok:
            self._start_preview()

        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, *args):
        if self.recording:
            self._stop_recording()
        self._stop_preview()
        return False

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        # Header
        header = Gtk.Label(label="diary")
        header.set_halign(Gtk.Align.START)
        header.add_css_class("diary-header")
        root.append(header)

        # Main area (stack between video preview and waveform)
        self.main_stack = Gtk.Stack()
        self.main_stack.set_vexpand(True)
        self.main_stack.set_hexpand(True)
        self.main_stack.set_margin_top(10)
        self.main_stack.set_margin_bottom(10)
        root.append(self.main_stack)

        # Video preview widget
        self.preview_picture = Gtk.Picture()
        self.preview_picture.set_can_shrink(True)
        self.preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        video_frame = Gtk.Box()
        video_frame.add_css_class("main-area")
        video_frame.append(self.preview_picture)
        self.preview_picture.set_hexpand(True)
        self.preview_picture.set_vexpand(True)
        self.main_stack.add_named(video_frame, "video")

        # Audio waveform area
        self.waveform_area = Gtk.DrawingArea()
        self.waveform_area.set_draw_func(self._draw_waveform)
        self.waveform_area.add_css_class("main-area")
        self.main_stack.add_named(self.waveform_area, "audio")

        # Bottom bar
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bottom_bar.set_margin_top(4)
        root.append(bottom_bar)

        # Left: mode toggle
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        mode_box.set_hexpand(True)
        mode_box.set_halign(Gtk.Align.START)
        mode_box.set_valign(Gtk.Align.CENTER)

        self.video_mode_btn = Gtk.Button(label="video")
        self.video_mode_btn.add_css_class("mode-button")
        self.video_mode_btn.add_css_class("flat")
        self.video_mode_btn.connect("clicked", self._on_mode_clicked, "video")
        mode_box.append(self.video_mode_btn)

        self.audio_mode_btn = Gtk.Button(label="audio")
        self.audio_mode_btn.add_css_class("mode-button")
        self.audio_mode_btn.add_css_class("flat")
        self.audio_mode_btn.connect("clicked", self._on_mode_clicked, "audio")
        mode_box.append(self.audio_mode_btn)

        # Device selector dropdowns (one shown per mode)
        self.device_stack = Gtk.Stack()
        self.device_stack.set_valign(Gtk.Align.CENTER)

        video_names = [name for name, _path in self.video_devices] or ["no camera"]
        self.video_device_model = Gtk.StringList.new(video_names)
        self.video_device_dropdown = Gtk.DropDown(model=self.video_device_model)
        self.video_device_dropdown.add_css_class("device-dropdown")
        self.video_device_dropdown.set_sensitive(bool(self.video_devices))
        self.video_device_dropdown.connect(
            "notify::selected", self._on_video_device_changed
        )
        self.device_stack.add_named(self.video_device_dropdown, "video")

        audio_names = [name for name, _node in self.audio_devices] or ["no microphone"]
        self.audio_device_model = Gtk.StringList.new(audio_names)
        self.audio_device_dropdown = Gtk.DropDown(model=self.audio_device_model)
        self.audio_device_dropdown.add_css_class("device-dropdown")
        self.audio_device_dropdown.set_sensitive(bool(self.audio_devices))
        self.audio_device_dropdown.connect(
            "notify::selected", self._on_audio_device_changed
        )
        self.device_stack.add_named(self.audio_device_dropdown, "audio")

        mode_box.append(self.device_stack)

        bottom_bar.append(mode_box)

        # Center: record button
        center_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        center_box.set_hexpand(True)
        center_box.set_halign(Gtk.Align.CENTER)
        center_box.set_valign(Gtk.Align.CENTER)

        self.record_button = Gtk.Button()
        self.record_button.add_css_class("record-button")
        self.record_button.add_css_class("flat")
        self.record_button.set_valign(Gtk.Align.CENTER)
        self.record_icon = Gtk.DrawingArea()
        self.record_icon.set_content_width(36)
        self.record_icon.set_content_height(36)
        self.record_icon.set_draw_func(self._draw_record_icon)
        self.record_button.set_child(self.record_icon)
        self.record_button.connect("clicked", self._on_record_clicked)
        center_box.append(self.record_button)

        bottom_bar.append(center_box)

        # Right: timer + status
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right_box.set_hexpand(True)
        right_box.set_halign(Gtk.Align.END)
        right_box.set_valign(Gtk.Align.CENTER)

        self.timer_label = Gtk.Label(label="")
        self.timer_label.add_css_class("timer-label")
        self.timer_label.set_halign(Gtk.Align.END)
        right_box.append(self.timer_label)

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("status-label")
        self.status_label.set_halign(Gtk.Align.END)
        right_box.append(self.status_label)

        bottom_bar.append(right_box)

    def _apply_css(self):
        css = b"""
        .diary-header {
            font-size: 13px;
            color: #8a8a8a;
            letter-spacing: 1px;
            margin-bottom: 2px;
        }

        .main-area {
            background-color: #161616;
            border-radius: 6px;
        }

        .mode-button {
            font-size: 12px;
            color: #777777;
            padding: 2px 4px;
            min-height: 0px;
            background: none;
            box-shadow: none;
            border: none;
            outline: none;
        }

        .mode-button:focus {
            outline: none;
            box-shadow: none;
        }

        .mode-button.active {
            color: #e6e6e6;
            text-decoration: underline;
        }

        .device-dropdown {
            font-size: 11px;
            color: #777777;
            background: none;
            box-shadow: none;
            border: none;
            outline: none;
            padding: 2px 4px;
            min-height: 0px;
        }

        .device-dropdown:focus {
            outline: none;
            box-shadow: none;
        }

        .device-dropdown > button {
            background: none;
            box-shadow: none;
            border: none;
            outline: none;
            padding: 2px 4px;
            min-height: 0px;
        }

        .record-button {
            padding: 0px;
            min-width: 0px;
            min-height: 0px;
            background: none;
            box-shadow: none;
            border: none;
            outline: none;
            border-radius: 999px;
        }

        .record-button:focus {
            outline: none;
            box-shadow: none;
        }

        .timer-label {
            font-size: 13px;
            color: #999999;
            font-family: monospace;
        }

        .status-label {
            font-size: 11px;
            color: #777777;
        }

        .status-label.error {
            color: #e05c5c;
        }

        .status-label.success {
            color: #6fbf73;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ------------------------------------------------------------------
    # Mode handling
    # ------------------------------------------------------------------
    def _on_mode_clicked(self, button, mode):
        if self.recording:
            return  # switching mode while recording is disabled
        if mode == self.mode:
            return
        if mode == "video" and not self.video_device_ok:
            return
        if mode == "audio" and not self.audio_device_ok:
            return

        # Stop preview if leaving video mode
        if self.mode == "video":
            self._stop_preview()

        self.mode = mode
        self._update_mode_ui()

        if self.mode == "video" and self.video_device_ok:
            self._start_preview()

    def _update_mode_ui(self):
        self.main_stack.set_visible_child_name(self.mode)
        self.device_stack.set_visible_child_name(self.mode)

        if self.mode == "video":
            self.video_mode_btn.add_css_class("active")
            self.audio_mode_btn.remove_css_class("active")
        else:
            self.audio_mode_btn.add_css_class("active")
            self.video_mode_btn.remove_css_class("active")

    # ------------------------------------------------------------------
    # Device selection
    # ------------------------------------------------------------------
    def _on_video_device_changed(self, dropdown, _pspec):
        if not self.video_devices:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        _name, path = self.video_devices[idx]
        if path == self.selected_video_device:
            return
        self.selected_video_device = path

        if self.mode == "video" and not self.recording:
            self._stop_preview()
            self._start_preview()

    def _on_audio_device_changed(self, dropdown, _pspec):
        if not self.audio_devices:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        _name, node_name = self.audio_devices[idx]
        self.selected_audio_device = node_name

    # ------------------------------------------------------------------
    # Record icon drawing
    # ------------------------------------------------------------------
    def _draw_record_icon(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 2

        if not self.recording:
            # Idle: solid red circle
            cr.set_source_rgb(0.85, 0.18, 0.18)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.fill()
        else:
            # Recording: dark circle with red border
            cr.set_source_rgb(0.09, 0.09, 0.09)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgb(0.85, 0.18, 0.18)
            cr.set_line_width(2.5)
            cr.arc(cx, cy, radius - 1.5, 0, 2 * math.pi)
            cr.stroke()

    # ------------------------------------------------------------------
    # Waveform drawing
    # ------------------------------------------------------------------
    def _draw_waveform(self, area, cr, width, height):
        cr.set_source_rgb(0.086, 0.086, 0.086)
        cr.paint()

        cr.set_source_rgb(0.92, 0.92, 0.92)
        cr.set_line_width(1.2)

        mid = height / 2
        n = len(self.waveform_history)
        step = width / (n - 1) if n > 1 else width

        for i, amp in enumerate(self.waveform_history):
            x = i * step
            y_offset = amp * (height * 0.42)
            if i == 0:
                cr.move_to(x, mid - y_offset)
            else:
                cr.line_to(x, mid - y_offset)
        cr.stroke()

        cr.move_to(0, mid)
        cr.line_to(width, mid)
        for i, amp in enumerate(self.waveform_history):
            x = i * step
            y_offset = amp * (height * 0.42)
            cr.line_to(x, mid + y_offset)
        cr.stroke()

    # ------------------------------------------------------------------
    # Video preview (separate lightweight pipeline, only while idle)
    # ------------------------------------------------------------------
    def _start_preview(self):
        if self.pipeline is not None:
            return

        pipeline_str = (
            f"v4l2src device={self.selected_video_device} ! videoconvert ! "
            "video/x-raw,format=RGBA ! "
            "appsink name=previewsink emit-signals=true max-buffers=1 drop=true sync=false"
        )

        try:
            pipeline = Gst.parse_launch(pipeline_str)
        except GLib.Error as e:
            self._show_error_dialog("Camera error", str(e))
            return

        appsink = pipeline.get_by_name("previewsink")
        appsink.connect("new-sample", self._on_preview_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_preview_bus_error)

        pipeline.set_state(Gst.State.PLAYING)
        self.preview_pipeline = pipeline

    def _stop_preview(self):
        pipeline = getattr(self, "preview_pipeline", None)
        if pipeline is None:
            return
        pipeline.set_state(Gst.State.NULL)
        self.preview_pipeline = None

    def _on_preview_bus_error(self, bus, message):
        err, debug = message.parse_error()
        print(f"Preview pipeline error: {err} ({debug})")

    def _on_preview_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        struct = caps.get_structure(0)
        width = struct.get_value("width")
        height = struct.get_value("height")

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK

        try:
            data = bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)

        GLib.idle_add(self._update_preview_texture, data, width, height)
        return Gst.FlowReturn.OK

    def _update_preview_texture(self, data, width, height):
        gbytes = GLib.Bytes.new(data)
        stride = width * 4
        texture = Gdk.MemoryTexture.new(
            width, height, Gdk.MemoryFormat.R8G8B8A8, gbytes, stride
        )
        self.preview_picture.set_paintable(texture)
        return False

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def _on_record_clicked(self, button):
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _output_path_for_now(self):
        filename = time.strftime("%Y-%m-%d_%H-%M.mp4")
        return os.path.join(DIARY_DIR, filename)

    def _start_recording(self):
        if self.mode == "video" and not self.video_device_ok:
            return
        if self.mode == "audio" and not self.audio_device_ok:
            return

        self.current_output_path = self._output_path_for_now()

        if self.mode == "video":
            ok = self._start_video_recording(self.current_output_path)
        else:
            self._audio_temp_path = self.current_output_path + ".audio.tmp.mp4"
            ok = self._start_audio_recording(self._audio_temp_path)

        if not ok:
            return

        self.recording = True
        self.video_mode_btn.set_sensitive(False)
        self.audio_mode_btn.set_sensitive(False)
        self.video_device_dropdown.set_sensitive(False)
        self.audio_device_dropdown.set_sensitive(False)
        self.timer_seconds = 0
        self._update_timer_label()
        self.timer_source_id = GLib.timeout_add(1000, self._on_timer_tick)
        self.record_icon.queue_draw()

    def _stop_recording(self):
        self.recording = False

        if self.timer_source_id is not None:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None

        if self.mode == "video":
            self._stop_video_recording()
        else:
            self._stop_audio_recording()

        # restore mode buttons (respect device availability)
        self.video_mode_btn.set_sensitive(self.video_device_ok)
        self.audio_mode_btn.set_sensitive(self.audio_device_ok)
        self.video_device_dropdown.set_sensitive(bool(self.video_devices))
        self.audio_device_dropdown.set_sensitive(bool(self.audio_devices))

        self.record_icon.queue_draw()
        self.timer_label.set_label("")

    def _on_timer_tick(self):
        self.timer_seconds += 1
        self._update_timer_label()
        return True

    def _update_timer_label(self):
        minutes = self.timer_seconds // 60
        seconds = self.timer_seconds % 60
        self.timer_label.set_label(f"{minutes:02d}:{seconds:02d}")

    # ------------------------------------------------------------------
    # Video recording pipeline
    # ------------------------------------------------------------------
    def _audio_src_element(self):
        """GStreamer source description for the currently selected mic."""
        if self.selected_audio_device:
            return f"pulsesrc device={self.selected_audio_device}"
        return "autoaudiosrc"

    def _start_video_recording(self, output_path):
        # Stop the lightweight preview pipeline; the recording pipeline
        # provides its own preview branch via tee.
        self._stop_preview()

        escaped_path = output_path.replace('"', '\\"')

        pipeline_str = (
            f'v4l2src device={self.selected_video_device} ! videoconvert ! tee name=vtee '
            f'vtee. ! queue max-size-buffers=2 leaky=downstream ! '
            f'video/x-raw,format=RGBA ! '
            f'appsink name=previewsink emit-signals=true max-buffers=1 drop=true sync=false '
            f'vtee. ! queue ! videoconvert ! openh264enc ! h264parse ! queue ! mux.video_0 '
            f'{self._audio_src_element()} ! audioconvert ! audioresample ! '
            f'avenc_aac ! aacparse ! queue ! mux.audio_0 '
            f'mp4mux name=mux ! filesink location="{escaped_path}"'
        )

        try:
            pipeline = Gst.parse_launch(pipeline_str)
        except GLib.Error as e:
            self._show_error_dialog("Recording error", str(e))
            return False

        appsink = pipeline.get_by_name("previewsink")
        appsink.connect("new-sample", self._on_preview_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_recording_bus_error)

        pipeline.set_state(Gst.State.PLAYING)
        self.pipeline = pipeline
        return True

    def _stop_video_recording(self):
        if self.pipeline is None:
            return
        self._finalize_pipeline(self.pipeline)
        self.pipeline = None

        # Resume the lightweight preview pipeline
        if self.video_device_ok:
            self._start_preview()

        self._trigger_upload(self.current_output_path)

    # ------------------------------------------------------------------
    # Audio recording pipeline
    # ------------------------------------------------------------------
    def _start_audio_recording(self, temp_output_path):
        escaped_path = temp_output_path.replace('"', '\\"')

        pipeline_str = (
            f"{self._audio_src_element()} ! audioconvert ! audioresample ! tee name=atee "
            "atee. ! queue ! level name=audiolevel interval=50000000 ! fakesink sync=true "
            "atee. ! queue ! avenc_aac ! aacparse ! queue ! mux.audio_0 "
            f'mp4mux name=mux ! filesink location="{escaped_path}"'
        )

        try:
            pipeline = Gst.parse_launch(pipeline_str)
        except GLib.Error as e:
            self._show_error_dialog("Recording error", str(e))
            return False

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_recording_bus_error)
        bus.connect("message::element", self._on_level_message)

        pipeline.set_state(Gst.State.PLAYING)
        self.pipeline = pipeline
        return True

    def _on_level_message(self, bus, message):
        struct = message.get_structure()
        if struct is None or struct.get_name() != "level":
            return

        peak = struct.get_value("peak")
        if not peak:
            return

        peak_db = peak[0]
        amplitude = 10 ** (peak_db / 20.0)
        amplitude = max(0.0, min(1.0, amplitude))

        # Raw linear amplitude is dominated by the logarithmic dB scale
        # (typical speech sits around 0.01-0.1), so rescale against a
        # -50dB..0dB window for a visually responsive waveform.
        normalized = (peak_db + 50.0) / 50.0
        normalized = max(0.0, min(1.0, normalized))

        self.level_amplitude = amplitude

        self.waveform_history.pop(0)
        self.waveform_history.append(normalized)
        self.waveform_area.queue_draw()

    def _stop_audio_recording(self):
        if self.pipeline is None:
            return
        self._finalize_pipeline(self.pipeline)
        self.pipeline = None

        self.waveform_history = [0.0] * len(self.waveform_history)
        self.waveform_area.queue_draw()

        # Combine the audio-only file with a black video track via ffmpeg
        # in a background thread, then trigger the upload.
        audio_temp = self._audio_temp_path
        final_path = self.current_output_path
        threading.Thread(
            target=self._mux_audio_with_black_video,
            args=(audio_temp, final_path),
            daemon=True,
        ).start()

    def _mux_audio_with_black_video(self, audio_temp_path, final_path):
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30",
                    "-i", audio_temp_path,
                    "-c:v", "libopenh264",
                    "-c:a", "copy",
                    "-shortest",
                    final_path,
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"ffmpeg mux failed: {e.stderr.decode(errors='replace')}")
            GLib.idle_add(
                self._show_error_dialog,
                "Audio processing failed",
                f"Could not finalize recording: {final_path}",
            )
            return
        finally:
            if os.path.exists(audio_temp_path):
                os.remove(audio_temp_path)

        GLib.idle_add(self._trigger_upload, final_path)

    # ------------------------------------------------------------------
    # Pipeline teardown helper (ensure EOS is written so mp4 is valid)
    # ------------------------------------------------------------------
    def _finalize_pipeline(self, pipeline):
        bus = pipeline.get_bus()

        pipeline.send_event(Gst.Event.new_eos())

        # Wait (briefly) for EOS or error so the mp4 moov atom is written.
        msg = bus.timed_pop_filtered(
            5 * Gst.SECOND,
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"Error finalizing recording: {err} ({debug})")

        pipeline.set_state(Gst.State.NULL)

    def _on_recording_bus_error(self, bus, message):
        err, debug = message.parse_error()
        print(f"Recording pipeline error: {err} ({debug})")
        GLib.idle_add(
            self._show_error_dialog,
            "Recording error",
            str(err),
        )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def _trigger_upload(self, file_path):
        self.status_label.remove_css_class("error")
        self.status_label.remove_css_class("success")
        self.status_label.set_label("uploading...")

        threading.Thread(
            target=self._upload_worker, args=(file_path,), daemon=True
        ).start()
        return False

    def _upload_worker(self, file_path):
        try:
            import upload as upload_module
        except ImportError as e:
            print(f"Upload failed: {e}")
            GLib.idle_add(self._on_upload_failed, file_path)
            GLib.idle_add(
                self._show_error_dialog,
                "Missing dependencies",
                "Could not import the YouTube upload module. Install the "
                "required packages with:\n\n"
                "pip install google-api-python-client google-auth-oauthlib",
            )
            return

        filename = os.path.basename(file_path)
        timestamp = filename.rsplit(".", 1)[0]
        # filename format: YYYY-MM-DD_HH-MM
        try:
            date_part, time_part = timestamp.split("_")
            display_time = time_part.replace("-", ":")
            title = f"diary — {date_part} {display_time}"
        except ValueError:
            title = f"diary — {timestamp}"

        try:
            upload_module.upload_video(
                file_path,
                title=title,
                description="",
                privacy_status="private",
                category_id="22",
            )
        except upload_module.ClientSecretsNotFound:
            GLib.idle_add(self._on_upload_failed, file_path)
            GLib.idle_add(
                self._show_error_dialog,
                "Missing client_secrets.json",
                "Place your Google OAuth client_secrets.json at "
                "~/diary/client_secrets.json then restart the app.",
            )
            return
        except Exception as e:
            print(f"Upload failed: {e}")
            GLib.idle_add(self._on_upload_failed, file_path)
            return

        GLib.idle_add(self._on_upload_succeeded, file_path)

    def _on_upload_succeeded(self, file_path):
        filename = os.path.basename(file_path)
        self.status_label.remove_css_class("error")
        self.status_label.add_css_class("success")
        self.status_label.set_label("uploaded ✓")
        notify("Diary", f"Uploaded ✓ {filename}")
        GLib.timeout_add(3000, self._clear_status_label)
        return False

    def _on_upload_failed(self, file_path):
        filename = os.path.basename(file_path)
        self.status_label.remove_css_class("success")
        self.status_label.add_css_class("error")
        self.status_label.set_label("upload failed ✗")
        notify("Diary", f"Upload failed — file saved locally at {file_path}")
        return False

    def _clear_status_label(self):
        self.status_label.set_label("")
        self.status_label.remove_css_class("success")
        self.status_label.remove_css_class("error")
        return False

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def _show_error_dialog(self, heading, body):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=heading,
            secondary_text=body,
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()

    def _show_info_dialog(self, heading, body):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=heading,
            secondary_text=body,
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()


class DiaryApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.xevrion.diary")
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = DiaryWindow(self)
        self.window.present()


def main():
    ensure_diary_dir()
    ensure_config()
    app = DiaryApp()
    app.run(None)


if __name__ == "__main__":
    main()
