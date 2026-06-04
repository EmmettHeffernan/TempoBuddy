"""
Smart Metronome — Retro Video Game Visualizer (full single-file Kivy app)

Features:
- Big retro-styled sequencer visualizer (centered)
- Two rows: metronome (top, neon red pulses) and user input (bottom, hits colored by timing)
- CRT / scanline overlay for extra retro vibe
- Uses aubio onset detection + sounddevice for real-time guitar input
- Adaptive BPM logic preserved
- Subdivision math fixed: clicks match the selected note value
"""

import time
import queue
import threading
import numpy as np
from kivy.app import App
from kivy.lang import Builder
from kivy.core.text import LabelBase
from kivy.properties import NumericProperty, StringProperty, BooleanProperty, ListProperty
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line
from kivy.core.window import Window
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.spinner import Spinner
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
import sounddevice as sd
import soundfile as sf
import aubio
import json, os
from scipy.signal import butter, lfilter

# -----------------------
# Band-pass filter for guitar (80–2000 Hz)
# -----------------------
def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def bandpass_filter(data, lowcut=1000.0, highcut=5000.0, fs=44100):
    b, a = butter_bandpass(lowcut, highcut, fs)
    return lfilter(b, a, data)

# -----------------------
# Retro theming
# -----------------------
Window.clearcolor = (0, 0, 0, 1)

class NeonMeter(Widget):
    value = NumericProperty(0.0)  # 0.0 → 1.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_interval(self._draw, 1/60.0)

    def _draw(self, dt):
        self.canvas.clear()
        with self.canvas:
            x, y = self.pos
            w, h = self.size
            Color(0.0, 0.1, 0.0, 1)  # dark background
            Rectangle(pos=(x, y), size=(w, h))

            # Neon green fill
            Color(0.0, 1.0, 0.0, 0.8)
            fill_h = h * min(1.0, max(0.0, self.value))
            Rectangle(pos=(x, y), size=(w, fill_h))

            # Glow lines
            Color(0.0, 1.0, 0.0, 0.2)
            for i in range(3):
                offset = 2 + i*2
                Line(rectangle=(x-offset, y, w+2*offset, fill_h), width=1.0)


KV = """
ScreenManager:
    id: sm

    MetronomeScreen:
        name: "metronome"

    StatsScreen:
        name: "stats"
    
    CalibrationScreen:
        name: "calibration"

<MetronomeScreen@Screen>:
    RootUI:
        id: rootui

<StatsScreen@Screen>:

    on_enter:
        self.ids.statsview.refresh

    BoxLayout:
        orientation: "vertical"
        spacing: "8dp"
        padding: "12dp"

        BoxLayout:
            size_hint_y: None
            height: "48dp"
            spacing: "8dp"
            Button:
                text: "Back to Metronome"
                size_hint_x: None
                width: "180dp"
                on_release: app.root.current = "metronome"

        TabbedStatsView:
            id: statsview

<CalibrationScreen@Screen>:

    BoxLayout:
        orientation: "vertical"
        spacing: "10dp"
        padding: "12dp"

        BoxLayout:
            orientation: "vertical"
            size_hint_y: None
            height: "48dp"
            spacing: "8dp"
            Button:
                text: "Back"
                size_hint_y: None
                on_release: app.root.current = "metronome"
            Label:
                text: "Calibration Menu"
                bold: True
                color: 1,1,1,1

        Label:
            id: threshold_label
            text: "Detection Threshold"
            color: 1,1,1,1
        Slider:
            id: threshold_slider
            min: -80
            max: 0
            step: 1
            on_value: root.on_slider_change("threshold", self.value)
        
        Label:
            id: lowcut_label
            text: "Band-pass Low Cut"
            color: 1,1,1,1
        Slider:
            id: lowcut_slider
            min: 20
            max: 1000
            step: 10
            on_value: root.on_slider_change("lowcut", self.value)
        
        Label:
            id: highcut_label
            text: "Band-pass High Cut"
            color: 1,1,1,1
        Slider:
            id: highcut_slider
            min: 1000
            max: 8000
            step: 50
            on_value: root.on_slider_change("highcut", self.value)
        
        Label:
            id: min_interval_label
            text: "Refractory Period"
            color: 1,1,1,1
        Slider:
            id: min_interval_slider
            min: 20
            max: 200
            step: 5
            on_value: root.on_slider_change("min_interval", self.value)
        
        Label:
            id: noise_gate_label
            text: "Noise Gate: 0.010"
            color: 1,1,1,1
        Slider:
            id: noise_gate_slider
            min: -80
            max: 0
            step: 1
            on_value: root.on_slider_change("noise_gate", self.value)

        Button:
            text: "Save Calibration"
            size_hint_y: None
            height: "48dp"
            on_release: root.save_settings()

        SpectrumWidget:
            id: spectrum
            size_hint_y: 0.4

        
        Widget:
            size_hint_y: 1



<RootUI>:
    orientation: "vertical"
    padding: "14dp"
    spacing: "12dp"

    BoxLayout:
        size_hint_y: None
        height: "56dp"
        padding: "8dp"
        spacing: "8dp"
        canvas.before:
            Color:
                rgba: 0,0,0,1
            Rectangle:
                pos: self.pos
                size: self.size

        Label:
            text: "MENTOR-NOME"
            font_size: "20sp"
            bold: True
            color: 1,0.2,0.2,1
            halign: "left"
            valign: "middle"
        Widget:
        Button:
            id: start_btn
            text: "Start" if not root.running else "Stop"
            size_hint_x: None
            width: "120dp"
            on_release: root.toggle_running()
            background_normal: ''
            background_color: 0.15,0,0,1
            color: 1,1,1,1
        Button:
            text: "Stats"
            size_hint_x: None
            width: "80dp"
            on_release: app.root.current = "stats"
        Button:
            text: "Calibrate"
            size_hint_x: None
            width: "120dp"
            on_release: app.root.current = "calibration"


    BoxLayout:
        size_hint_y: None
        height: "44dp"
        spacing: "8dp"
        Label:
            text: f"Tempo: {int(root.bpm)} BPM"
            font_size: "12sp"
            color: 1,1,1,1
            size_hint_x: None
            width: "160dp"
        Slider:
            min: 40
            max: 220
            value: root.bpm
            on_value: root.set_bpm(self.value)

    BoxLayout:
        size_hint_y: None
        height: "44dp"
        spacing: "8dp"
        Label:
            text: "Mic Input:"
            color: 1,1,1,1
            size_hint_x: None
            width: "100dp"
        Spinner:
            id: device_spinner
            text: root.device_name if root.device_name else "Default"
            values: root.device_list
            on_text: root.set_device(self.text)
        NeonMeter:
            id: mic_meter
            size_hint_x: None
            width: "20dp"
            value: root.mic_level

    BoxLayout:
        size_hint_y: None
        height: "44dp"
        spacing: "8dp"
        Label:
            text: "Subdivision:"
            color: 1,1,1,1
            size_hint_x: None
            width: "100dp"
        Spinner:
            text: root.subdivision_name
            values: ["Quarter (1/4)", "Eighth (1/8)", "Sixteenth (1/16)"]
            on_text: root.set_subdivision(self.text)

    BoxLayout:
        size_hint_y: None
        height: "44dp"
        spacing: "8dp"
        Label:
            text: "Avg measures:"
            color: 1,1,1,1
            size_hint_x: None
            width: "120dp"
        Slider:
            min: 1
            max: 8
            step: 1
            value: root.avg_measures
            on_value: root.set_avg_measures(self.value)
        Label:
            text: f"{int(root.avg_measures)}"
            color: 1,1,1,1
            size_hint_x: None
            width: "40dp"

    BoxLayout:
        size_hint_y: None
        height: "56dp"
        spacing: "8dp"
        Label:
            text: "Beat Feedback:"
            color: 1,1,1,1
            size_hint_x: None
            width: "120dp"
        Label:
            text: root.feedback_text
            color: root.feedback_color
            font_size: "16sp"
            bold: True

    AnchorLayout:
        size_hint_y: 0.85
        anchor_x: "center"
        anchor_y: "center"
        SequencerWidget:
            id: sequencer
            size_hint: (0.98, 0.95)
            bpm: root.bpm
            beats_per_bar: root.beats_per_bar
            subdivision: root.subdivision
            tol_fraction: root.rhythm_tol_fraction
            running: root.running

    Widget:
"""



# -----------------------
# Sequencer visual widget
# -----------------------
class SequencerWidget(Widget):
    bpm = NumericProperty(90.0)
    beats_per_bar = NumericProperty(4)
    subdivision = NumericProperty(1)
    tol_fraction = NumericProperty(0.15)
    running = BooleanProperty(False)

    current_step = NumericProperty(0)
    steps = NumericProperty(4)
    user_marks = ListProperty([])

    mark_lifetime = NumericProperty(0.9)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_interval(self._draw, 1 / 60.0)

    def _draw(self, dt):
        self.steps = max(1, int(self.beats_per_bar * max(1, int(self.subdivision))))
        now = time.perf_counter()
        self.user_marks = [m for m in self.user_marks if now - m[2] <= self.mark_lifetime]

        self.canvas.clear()
        with self.canvas:
            Color(0, 0, 0, 1)
            Rectangle(pos=self.pos, size=self.size)
            x0, y0 = self.pos
            W, H = self.size

            padding_v = 18
            gap = 10
            row_h = (H - 3 * padding_v) / 2.0
            sq_h = row_h - padding_v
            total_gap = (self.steps - 1) * gap
            sq_w = min(sq_h, max(16, (W - 2 * 24 - total_gap) / max(1, self.steps)))
            sq_size = int(sq_w)
            total_w = self.steps * sq_size + (self.steps - 1) * gap
            left = x0 + (W - total_w) / 2.0
            top_row_y = y0 + H - padding_v - sq_size
            bottom_row_y = y0 + padding_v

            # frame
            frame_pad = 10
            Color(0.12, 0.0, 0.0, 1)
            RoundedRectangle(pos=(left - frame_pad, bottom_row_y - frame_pad),
                             size=(total_w + 2 * frame_pad, 2 * sq_size + gap + 2 * frame_pad),
                             radius=[10])
            Color(0.03, 0.03, 0.03, 1)
            RoundedRectangle(pos=(left - frame_pad + 4, bottom_row_y - frame_pad + 4),
                             size=(total_w + 2 * frame_pad - 8, 2 * sq_size + gap + 2 * frame_pad - 8),
                             radius=[8])

            for i in range(self.steps):
                x = left + i * (sq_size + gap)
                if i == self.current_step and self.running:
                    Color(1.0, 0.08, 0.08, 1.0)
                    RoundedRectangle(pos=(x, top_row_y), size=(sq_size, sq_size), radius=[6])
                    Color(1, 0.4, 0.4, 0.08)
                    RoundedRectangle(pos=(x + 2, top_row_y + 2), size=(sq_size - 4, sq_size - 4), radius=[4])
                else:
                    Color(1, 1, 1, 0.08)
                    RoundedRectangle(pos=(x, top_row_y), size=(sq_size, sq_size), radius=[6])
                    Color(1, 1, 1, 0.06)
                    Line(rectangle=(x, top_row_y, sq_size, sq_size), width=1)
                if (i % int(self.subdivision)) == 0:
                    Color(1, 1, 1, 0.06)
                    Rectangle(pos=(x + sq_size * 0.15, top_row_y - 8), size=(sq_size * 0.7, 6))

            for i in range(self.steps):
                x = left + i * (sq_size + gap)
                Color(0.06, 0.06, 0.06, 1)
                RoundedRectangle(pos=(x, bottom_row_y), size=(sq_size, sq_size), radius=[6])
                Color(1, 1, 1, 0.04)
                Line(rectangle=(x, bottom_row_y, sq_size, sq_size), width=1)

            for (step_idx, status, ts) in list(self.user_marks):
                if step_idx < 0 or step_idx >= self.steps:
                    continue
                x = left + step_idx * (sq_size + gap)

                if status == "ontime":
                    Color(0.85, 1.0, 0.9, 1.0)  # greenish flash
                elif status == "late":
                    Color(1.0, 0.55, 0.1, 1.0)  # orange flash
                else:
                    Color(0.98, 0.12, 0.12, 1.0)  # red flash

                RoundedRectangle(pos=(x, bottom_row_y), size=(sq_size, sq_size), radius=[6])

            # CRT scanlines
            lines = int(H // 6)
            Color(0.0, 0.15, 0.0, 0.03)
            for li in range(lines):
                ly = y0 + (li / float(lines)) * H
                Rectangle(pos=(x0, ly), size=(W, 1.0))
            # vignette
            Color(0, 0, 0, 0.18)
            Rectangle(pos=(x0, y0 + H - 24), size=(W, 24))
            Rectangle(pos=(x0, y0), size=(W, 24))

class SessionLogger:
    def __init__(self, filepath="sessions.json"):
        self.filepath = filepath
        self.current = None
        if not os.path.exists(filepath):
            with open(filepath, "w") as f:
                json.dump([], f)

    def start_session(self, bpm, subdivision, beats_per_bar):
        self.current = {
            "start_time": time.time(),
            "bpm": bpm,
            "subdivision": subdivision,
            "beats_per_bar": beats_per_bar,
            "hits": [],
            "accuracy": None,
            "avg_latency": None,
            "min_bpm": bpm,  # NEW
            "max_bpm": bpm  # NEW
        }

    def update_bpm(self, bpm):
        if self.current:
            self.current["min_bpm"] = min(self.current.get("min_bpm", bpm), bpm)
            self.current["max_bpm"] = max(self.current.get("max_bpm", bpm), bpm)
    def record_hit(self, status, latency_ms):
        if self.current:
            self.current["hits"].append({
                "status": status,
                "latency_ms": latency_ms,
                "ts": time.time()
            })

    def end_session(self):
        if not self.current:
            return
        self.current["end_time"] = time.time()
        hits = self.current["hits"]
        if hits:
            self.current["accuracy"] = sum(1 for h in hits if h["status"]=="ontime") / len(hits)
            self.current["avg_latency"] = sum(h["latency_ms"] for h in hits)/len(hits)
        else:
            self.current["accuracy"] = 0
            self.current["avg_latency"] = None
        with open(self.filepath, "r+") as f:
            data = json.load(f)
            data.append(self.current)
            f.seek(0)
            json.dump(data, f, indent=2)
        self.current = None

    def clear_all(self):
        """Remove all session data (both memory + file)."""
        self.sessions.clear()
        self.current = None
        # If you’re saving sessions to disk:
        open("sessions.json", "w").write("[]")  # overwrites with empty list

class CalibrationManager:
    def __init__(self, filepath="calibration.json"):
        self.filepath = filepath
        if not os.path.exists(filepath):
            self.save({
                "dyn_threshold_db": -40,
                "noise_gate_db": -60,
                "lowcut": 80,
                "highcut": 2000,
                "min_interval": 50,  # ms
            })

    def load(self):
        try:
            with open(self.filepath, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self, settings: dict):
        with open(self.filepath, "w") as f:
            json.dump(settings, f, indent=2)


class TabbedStatsView(TabbedPanel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.do_default_tab = False

        # Make tabs bigger and more readable
        self.tab_height = "48dp"
        self.tab_width = "180dp"
        self.padding = 8
        self.spacing = 6

        Clock.schedule_once(lambda dt: self.refresh(), 0)

        # Tabs
        self.overall_tab = TabbedPanelItem(text="Overall Stats")
        self.session_tab = TabbedPanelItem(text="Session Dashboard")
        self.add_widget(self.overall_tab)
        self.add_widget(self.session_tab)

        # Containers
        self.overall_box = BoxLayout(orientation="vertical", spacing=6, padding=10)
        self.overall_tab.add_widget(self.overall_box)

        vbox = BoxLayout(orientation="vertical", spacing=6, padding=10)
        self.session_tab.add_widget(vbox)

        self.session_spinner = Spinner(
            text="Select Session",
            size_hint_y=None,
            height="44dp"
        )
        # FIX: Correct binding signature → (spinner, text)
        self.session_spinner.bind(text=self.show_session_dashboard)
        vbox.add_widget(self.session_spinner)

        self.session_box = BoxLayout(orientation="vertical", spacing=6, padding=10)
        vbox.add_widget(self.session_box)

        self.clear_btn = Button(
            text="Clear All User Data",
            size_hint=(1, None), height="50dp",
            background_color=(1, 0.3, 0.3, 1)
        )
        self.clear_btn.bind(on_release=self.confirm_clear)
        self.overall_box.add_widget(self.clear_btn)

    def refresh(self):
        try:
            with open("sessions.json", "r") as f:
                self.sessions = json.load(f)
        except Exception:
            self.sessions = []

        self.populate_overall()
        self.populate_session_spinner()

    # -------- Overall Stats --------
    def populate_overall(self):
        self.overall_box.clear_widgets()

        if not self.sessions:
            self.overall_box.add_widget(Label(text="No sessions recorded yet.", color=(1, 1, 1, 1)))
        else:
            total_hits = sum(len(s.get("hits", [])) for s in self.sessions)
            avg_accuracy = sum(s.get("accuracy", 0) for s in self.sessions) / len(self.sessions)
            avg_latency = sum(s.get("avg_latency", 0) or 0 for s in self.sessions) / len(self.sessions)
            min_bpm_all = min(s.get("min_bpm", s["bpm"]) for s in self.sessions)
            max_bpm_all = max(s.get("max_bpm", s["bpm"]) for s in self.sessions)

            self.overall_box.add_widget(
                Label(text=f"Lowest BPM across sessions: {min_bpm_all}", color=(1, 1, 0.5, 1)))
            self.overall_box.add_widget(
                Label(text=f"Highest BPM across sessions: {max_bpm_all}", color=(0.5, 1, 0.5, 1)))
            self.overall_box.add_widget(Label(text=f"Sessions recorded: {len(self.sessions)}", color=(1, 1, 1, 1)))
            self.overall_box.add_widget(Label(text=f"Total hits: {total_hits}", color=(1, 1, 1, 1)))
            self.overall_box.add_widget(
                Label(text=f"Average accuracy: {avg_accuracy * 100:.1f}%", color=(0.7, 1, 0.7, 1)))
            self.overall_box.add_widget(Label(text=f"Average latency: {avg_latency:.1f} ms", color=(1, 1, 1, 1)))

        # 👇 Add the clear button back (since clear_widgets wipes it each time)
        self.overall_box.add_widget(self.clear_btn)

    # -------- Clear Data --------
    def confirm_clear(self, *args):
        popup = Popup(
            title="Confirm Clear",
            content=Button(text="Yes, clear all data", on_release=lambda btn: self.clear_data(popup)),
            size_hint=(0.6, 0.3)
        )
        popup.open()

    def clear_data(self, popup):
        # Wipe file
        open("sessions.json", "w").write("[]")
        self.sessions = []
        self.overall_box.clear_widgets()
        self.session_box.clear_widgets()
        self.populate_overall()
        self.populate_session_spinner()
        popup.dismiss()

    # -------- Session Selection --------
    def populate_session_spinner(self):
        self.session_spinner.values = [
            time.strftime('%Y-%m-%d %H:%M', time.localtime(s['start_time'])) for s in self.sessions
        ]

    def show_session_dashboard(self, spinner, text):
        self.session_box.clear_widgets()
        if text == "Select Session":
            return
        try:
            idx = [
                time.strftime('%Y-%m-%d %H:%M', time.localtime(s['start_time'])) for s in self.sessions
            ].index(text)
        except ValueError:
            return

        s = self.sessions[idx]

        self.session_box.add_widget(Label(text=f"Starting BPM: {s['bpm']}", color=(1, 1, 1, 1)))
        self.session_box.add_widget(Label(text=f"Lowest BPM: {s.get('min_bpm', s['bpm'])}", color=(1, 1, 0.5, 1)))
        self.session_box.add_widget(Label(text=f"Highest BPM: {s.get('max_bpm', s['bpm'])}", color=(0.5, 1, 0.5, 1)))
        self.session_box.add_widget(Label(text=f"Subdivision: {s['subdivision']}", color=(1, 1, 1, 1)))
        self.session_box.add_widget(Label(text=f"Beats per bar: {s['beats_per_bar']}", color=(1, 1, 1, 1)))
        self.session_box.add_widget(Label(text=f"Hits: {len(s.get('hits', []))}", color=(1, 1, 1, 1)))
        self.session_box.add_widget(Label(text=f"Accuracy: {s.get('accuracy', 0) * 100:.1f}%", color=(0.7, 1, 0.7, 1)))
        self.session_box.add_widget(Label(text=f"Avg latency: {s.get('avg_latency', 0):.1f} ms", color=(1, 1, 1, 1)))

        hits = s.get("hits", [])
        if hits:
            deltas = [h["latency_ms"] for h in hits]
            mad = sum(abs(d) for d in deltas) / len(deltas)
            self.session_box.add_widget(Label(text=f"Mean absolute deviation: {mad:.1f} ms", color=(1, 1, 0.5, 1)))

# -----------------------
# Root UI + Engine
# -----------------------
class RootUI(BoxLayout):
    dyn_threshold_db = NumericProperty(-40.0)  # in dB
    noise_gate_db = NumericProperty(-60.0)  # in dB
    lowcut = NumericProperty(80.0)
    highcut = NumericProperty(2000.0)
    min_interval = NumericProperty(0.05)  # seconds


    bpm = NumericProperty(100.0)
    running = BooleanProperty(False)
    feedback_text = StringProperty("—")
    feedback_color = ListProperty([1, 1, 1, 1])
    mic_level = NumericProperty(0.0)
    _mic_peak = 0.0

    tempo_step = NumericProperty(5.0)
    ema_alpha = NumericProperty(0.5)

    _counting_in = BooleanProperty(False)
    _countin_beats_left = NumericProperty(0)

    device_name = StringProperty("")
    device_list = ListProperty([])

    subdivision = NumericProperty(1)
    subdivision_name = StringProperty("Quarter (1/4)")
    rhythm_tol_fraction = NumericProperty(0.14)

    beats_per_bar = NumericProperty(4)
    avg_measures = NumericProperty(2)

    _delta_buffer = []
    _measure_start = None
    _measures_collected = 0

    _beat_idx = 0
    _last_click_time = None
    _last_bar_time = NumericProperty(0.0)
    _current_step = 0
    _onset_events = []
    _user_marks = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sr = 44100
        self.win_s = 2048
        self.hop_s = 512
        self.onset = aubio.onset("default", self.win_s, self.hop_s, self.sr)
        self.click_data = self._load_click("assets/click.wav", self.sr)
        self._out_stream = None
        self._in_queue = queue.Queue()
        self._stream = None
        self._audio_thread = None
        self._stop_event = threading.Event()
        self._interval = 60.0 / self.bpm / self.subdivision
        self._next_click = None
        self._ema_delta = 0.0
        self.refresh_devices()
        self._tick_ev = None
        self.calibration = CalibrationManager("calibration.json")
        settings = self.calibration.load()
        self.dyn_threshold_db = settings.get("dyn_threshold_db", -40)
        self.noise_gate_db = settings.get("noise_gate_db", -60)
        self.lowcut = settings.get("lowcut", 80)
        self.highcut = settings.get("highcut", 2000)
        self.min_interval = settings.get("min_interval", 50) / 1000.0  # ms → seconds
        self.logger = SessionLogger("sessions.json")

    def on_kv_post(self, base_widget):
        # Start the audio input loop once UI is ready
        self._start_audio_thread()

    def _update_spectrum(self, spectrum_db, dyn_threshold_db, noise_gate_db):
        try:
            app = App.get_running_app()
            calib = app.root.get_screen("calibration")
            if hasattr(calib, "spectrum_widget") and calib.spectrum_widget:
                calib.spectrum_widget.update_spectrum(spectrum_db, dyn_threshold_db, noise_gate_db)
        except Exception as e:
            print("Spectrum update error:", e)

    def set_threshold(self, val):
        self.dyn_threshold_db = float(val)

    def set_lowcut(self, val):
        self.lowcut = float(val)

    def set_highcut(self, val):
        self.highcut = float(val)

    def set_min_interval(self, val):
        self.min_interval = float(val) / 1000.0

    def set_noise_gate(self, val):
        self.noise_gate_db = float(val)

    def _remove_user_mark(self, step_idx, status, ts):
        self._user_marks = [
            m for m in self._user_marks
            if not (m[0] == step_idx and m[1] == status and m[2] == ts)
        ]
    def _load_click(self, path, target_sr):
        try:
            data, sr = sf.read(path, dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = data[:, 0]
        except Exception:
            dur = 0.03
            t = np.arange(int(target_sr * dur)) / target_sr
            wave = np.sign(np.sin(2 * np.pi * 2000 * t)).astype(np.float32)
            env = np.exp(-t * 80.0).astype(np.float32)
            data = 0.45 * wave * env
            sr = target_sr
        if sr != target_sr and len(data) > 0:
            x = np.arange(len(data))
            xp = np.linspace(0, len(data) - 1, int(len(data) * target_sr / sr))
            data = np.interp(xp, x, data).astype(np.float32)
        return data.astype(np.float32)

    def refresh_devices(self):
        try:
            devices = sd.query_devices()
            inputs = []
            for i, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0:
                    name = f"{i}: {d['name']}"
                    inputs.append(name)
            self.device_list = inputs or ["Default"]
        except Exception:
            self.device_list = ["Default"]

    def set_bpm(self, val):
        self.bpm = float(val)
        self._interval = 60.0 / self.bpm / self.subdivision
        self.logger.update_bpm(self.bpm)

    def set_device(self, text):
        self.device_name = text

        # Stop current thread if running
        if self._audio_thread and self._audio_thread.is_alive():
            self._stop_event.set()
            self._audio_thread.join(timeout=2.0)

        # Fresh stop_event
        self._stop_event = threading.Event()

        # Restart input
        self._start_audio_thread()

    def set_subdivision(self, text):
        if "Quarter" in text:
            self.subdivision = 1
        elif "Eighth" in text:
            self.subdivision = 2
        elif "Sixteenth" in text:
            self.subdivision = 4
        self.subdivision_name = text
        self._interval = 60.0 / self.bpm / self.subdivision

    def set_avg_measures(self, val):
        self.avg_measures = int(val)

    def toggle_running(self):
        if not self.running:
            self.start()
        else:
            self.stop()

    def start(self):
        self.running = True
        self.feedback_text = "Count-in…"
        self.feedback_color = [0.7, 1, 0.7, 1]
        self._stop_event.clear()
        self._in_queue = queue.Queue()
        self._ema_delta = 0.0
        self._delta_buffer = []
        self._measure_start = time.perf_counter()
        self._measures_collected = 0
        self._onset_events.clear()
        self._beat_idx = 0
        self._last_bar_time = 0.0
        self._current_step = 0
        self._user_marks = []
        self._counting_in = True
        self._countin_beats_left = self.beats_per_bar
        self.logger.start_session(bpm=self.bpm, subdivision=self.subdivision, beats_per_bar=self.beats_per_bar)

        try:
            if self._out_stream is None:
                self._out_stream = sd.OutputStream(samplerate=self.sr, channels=1, blocksize=0)
                self._out_stream.start()
        except Exception as e:
            self.feedback_text = f"Output error: {e}"
            self.feedback_color = [1, 0.3, 0.3, 1]
            self.running = False
            return

        self._audio_thread = threading.Thread(target=self._run_audio_stream, daemon=True)
        self._audio_thread.start()
        self._next_click = time.perf_counter() + 0.15
        if self._tick_ev:
            self._tick_ev.cancel()
        self._tick_ev = Clock.schedule_interval(self._metronome_loop, 0)

    def stop(self):
        self.running = False
        self.logger.end_session()

        app = App.get_running_app()
        if hasattr(app, "stats_view"):
            app.stats_view.refresh()

        if self._tick_ev:
            self._tick_ev.cancel()
            self._tick_ev = None

        # clean up output stream
        try:
            if self._out_stream:
                self._out_stream.stop()
                self._out_stream.close()
                self._out_stream = None
        except Exception:
            pass

        self.feedback_text = "—"
        self.feedback_color = [1, 1, 1, 1]

    def _start_audio_thread(self):
        """Start input thread if not running."""
        if self._audio_thread and self._audio_thread.is_alive():
            return
        self._stop_event.clear()
        self._audio_thread = threading.Thread(
            target=self._run_audio_stream, daemon=True
        )
        self._audio_thread.start()

    def _run_audio_stream(self):
        device = None
        if self.device_name and self.device_name != "Default":
            try:
                device_idx = int(self.device_name.split(":")[0])
                device = device_idx
            except ValueError:
                # fallback: match by name
                for i, dev in enumerate(sd.query_devices()):
                    if self.device_name.lower() in dev["name"].lower():
                        device = i
                        break
        print(f"Opening audio input stream on device: {device or 'Default'}")

        buffer_size = self.hop_s

        # State for gating (initialized from settings, in dB)
        self._dyn_threshold_db = self.dyn_threshold_db
        self._last_hit_time = 0.0

        def callback(indata, frames, time_info, status):
            try:
                if self._stop_event.is_set():
                    raise sd.CallbackAbort
                if frames != buffer_size:
                    return

                # Ensure float32
                samples = indata[:, 0].astype(np.float32, copy=False)

                # --- Envelope follower for mic meter (linear for GUI) ---
                rms = float(np.sqrt(np.mean(samples ** 2)))
                scaled = min(rms * 8.0, 1.0)
                self._mic_peak = max(scaled, self._mic_peak * 0.85)
                Clock.schedule_once(lambda dt: setattr(self, 'mic_level', self._mic_peak), 0)

                # --- Band-pass filter: 80Hz–2000Hz for guitar ---
                samples_bp = bandpass_filter(samples, self.lowcut, self.highcut, self.sr)

                # --- Convert to dB values ---
                rms_db = 20 * np.log10(rms + 1e-6)
                peak = float(np.max(np.abs(samples_bp)))
                peak_db = 20 * np.log10(peak + 1e-6)
                energy = float(np.mean(np.abs(samples_bp)))
                energy_db = 20 * np.log10(energy + 1e-6)

                # --- Send FFT to CalibrationScreen if active ---
                app = App.get_running_app()
                if app.root.current == "calibration":
                    spectrum = np.abs(np.fft.rfft(samples_bp))[:512]
                    spectrum_db = 20 * np.log10(spectrum + 1e-6)

                    # Use ONLY slider values (fixed thresholds)
                    t_db = self.dyn_threshold_db
                    g_db = self.noise_gate_db

                    Clock.schedule_once(
                        lambda dt: self._update_spectrum(spectrum_db, t_db, g_db),
                        0
                    )

                # --- Noise gate check ---
                gate_db = self.noise_gate_db

                # block if *either* RMS or peak are below gate
                if rms_db < gate_db or peak_db < gate_db:
                    return

                # enforce hysteresis: must be 3 dB over gate to count as valid
                if peak_db < gate_db + 3:
                    return

                self._dyn_threshold_db = self.dyn_threshold_db  # use slider value only


                # --- Onset detection ---
                onset_detected = self.onset(samples_bp.astype(np.float32))
                strong_peak = (
                        peak_db > self._dyn_threshold_db + 6
                        and energy_db > self._dyn_threshold_db + 3
                )

                if onset_detected != 0 or strong_peak:
                    now = time.perf_counter()
                    if now - self._last_hit_time > self.min_interval:
                        # Derivative check
                        env = np.abs(samples_bp)
                        env_smooth = np.convolve(env, np.ones(64) / 64, mode="same")
                        diff = np.diff(env_smooth, prepend=env_smooth[0])
                        if np.max(diff) > (np.mean(diff) + 3 * np.std(diff)):
                            self._last_hit_time = now
                            self._in_queue.put(now)

            except Exception as e:
                print("Audio callback error:", e)

        try:
            with sd.InputStream(
                    callback=callback,
                    channels=1,
                    samplerate=self.sr,
                    blocksize=buffer_size,
                    device=device,
            ) as stream:
                self._stream = stream
                while not self._stop_event.is_set():
                    time.sleep(0.01)
            self._stream = None
        except Exception as e:
            def set_err(*_):
                self.feedback_text = f"Audio error: {e}"
                self.feedback_color = [1, 0.3, 0.3, 1]

            Clock.schedule_once(set_err, 0)

    def _play_click(self):
        if self._out_stream:
            try:
                self._out_stream.write(self.click_data)
            except Exception as e:
                self.feedback_text = f"Click error: {e}"
                self.feedback_color = [1, 0.3, 0.3, 1]
        self._last_click_time = time.perf_counter()
        self._beat_idx += 1
        steps_per_bar = int(self.beats_per_bar * max(1, int(self.subdivision)))
        self._current_step = (self._beat_idx - 1) % steps_per_bar

        # Update start of current bar
        if (self._beat_idx - 1) % (self.beats_per_bar * self.subdivision) == 0:
            self._last_bar_time = self._last_click_time

    # -----------------------
    # Classify user hit timing (fixed alignment)
    # -----------------------
    def _classify_onset(self, onset_time):
        """
        Determines how close the user's hit is to the nearest metronome subdivision.
        Returns: delta (seconds), status ('ontime', 'early', 'late'), step index
        """
        beat_len = 60.0 / max(1e-6, self.bpm)
        subdiv = max(1, int(self.subdivision))
        subdiv_len = beat_len / subdiv

        # Reference time: start of the current bar
        if self._last_bar_time is None or self._last_bar_time == 0.0:
            t0 = self._last_click_time if self._last_click_time else time.perf_counter()
        else:
            t0 = self._last_bar_time

        # Compute the nearest subdivision step
        k = int(np.floor((onset_time - t0) / subdiv_len + 0.5))
        nearest = t0 + k * subdiv_len
        delta = onset_time - nearest

        # Determine status
        tol = subdiv_len * self.rhythm_tol_fraction
        if abs(delta) <= tol:
            status = "ontime"
        elif delta > 0:
            status = "late"
        else:
            status = "early"

        # Step index for visual display, relative to current bar
        steps_per_bar = int(self.beats_per_bar * subdiv)
        step_idx = k % steps_per_bar

        return delta, status, step_idx

    # -----------------------
    # Metronome loop: handle clicks and user hits
    # -----------------------
    def _metronome_loop(self, dt):
        if not self.running:
            return False

        now = time.perf_counter()

        # Play next click if needed
        if self._next_click is None:
            self._next_click = now + self._interval
        if now >= self._next_click:
            self._play_click()
            self._next_click += self._interval

        # Process all queued user hits
        while True:
            try:
                onset_time = self._in_queue.get_nowait()
            except queue.Empty:
                break

            if self._last_click_time is None:
                continue  # ignore hits before first click

            delta, status, step_idx = self._classify_onset(onset_time)
            self._user_marks.append((step_idx, status, onset_time))
            Clock.schedule_once(
                lambda dt: self._remove_user_mark(step_idx, status, onset_time),
                0.1  # flash duration in seconds
            )

            latency_ms = delta * 1000
            self.logger.record_hit(status, latency_ms)

            # Keep only recent marks (for visuals)
            cutoff = now - 1.5
            self._user_marks = [m for m in self._user_marks if m[2] >= cutoff]

            # Store delta for adaptive BPM calculation
            self._delta_buffer.append(delta)

        # Update sequencer visuals
        seq = self.ids.get("sequencer")
        if seq:
            seq.steps = int(self.beats_per_bar * max(1, int(self.subdivision)))
            seq.current_step = int(self._current_step % max(1, seq.steps))
            seq.user_marks = list(self._user_marks)

        # Check if a measure completed
        beat_len = 60.0 / self.bpm
        measure_len = self.beats_per_bar * beat_len
        if now - self._measure_start >= measure_len:
            if self._delta_buffer:
                deltas = np.array(self._delta_buffer, dtype=np.float32)
                self._measures_collected += 1
                note_len = beat_len / self.subdivision
                tol = note_len * self.rhythm_tol_fraction

                if self._measures_collected >= self.avg_measures:
                    # --- Robust timing evaluation ---
                    mad = float(np.mean(np.abs(deltas)))  # consistency (spread)
                    med = float(np.median(deltas))  # signed bias
                    frac_in_tol = float(np.mean(np.abs(deltas) <= tol))

                    if frac_in_tol >= 0.70 and mad <= tol * 0.80:
                        # Consistently inside tolerance → speed up
                        self._adjust_bpm(self.tempo_step)
                        self.feedback_text = "Consistently on time, speeding up 5 BPM"
                        self.feedback_color = [1, 1, 1, 1]

                    elif med > tol:
                        # On average late
                        self._adjust_bpm(-self.tempo_step)
                        self.feedback_text = f"Average late {int(med * 1000)} ms. Slowing down 5 BPM."
                        self.feedback_color = [1, 0.35, 0.0, 1]

                    elif med < -tol:
                        # On average early
                        self._adjust_bpm(-self.tempo_step)
                        self.feedback_text = f"Avg early {int(-med * 1000)} ms. Slowing down 5 BPM."
                        self.feedback_color = [1, 0.2, 0.2, 1]

                    else:
                        # Mixed → don’t adjust tempo
                        self.feedback_text = f"Inconsistent ±{int(mad * 1000)} ms. Try again."
                        self.feedback_color = [1, 1, 0.4, 1]

                    self._measures_collected = 0

            self._delta_buffer = []
            self._measure_start = now

        return True

    def _adjust_bpm(self, delta_bpm):
        new_bpm = float(np.clip(self.bpm + delta_bpm, 40.0, 220.0))
        if new_bpm == self.bpm:
            return
        self.bpm = new_bpm
        self._interval = 60.0 / self.bpm / self.subdivision
        now = time.perf_counter()
        self._next_click = now + self._interval
        self.logger.update_bpm(self.bpm)

    def save_calibration(self):
        settings = {
            "dyn_threshold_db": self.dyn_threshold_db,
            "noise_gate_db": self.noise_gate_db,
            "lowcut": self.lowcut,
            "highcut": self.highcut,
            "min_interval": int(self.min_interval * 1000),  # seconds → ms
        }
        self.calibration.save(settings)


class SpectrumWidget(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.spectrum_db = np.zeros(512)
        self.threshold_db = -40  # detection threshold in dB
        self.noise_gate_db = -60  # noise gate in dB
        Clock.schedule_interval(self._draw, 1/30.0)

    def update_spectrum(self, spectrum_db, threshold_db, noise_gate_db=None):
        self.spectrum_db = spectrum_db[:512]
        self.threshold_db = threshold_db
        if noise_gate_db is not None:
            self.noise_gate_db = noise_gate_db

    def _draw(self, dt):
        self.canvas.clear()
        with self.canvas:
            Color(0, 1, 0.3, 0.8)
            W, H = self.size
            x0, y0 = self.pos
            n = len(self.spectrum_db)
            if n == 0:
                return
            step = W / n

            def db_to_y(db_val):
                return y0 + ((db_val + 80) / 80.0) * H  # -80→bottom, 0→top

            # draw FFT bars
            for i, db_val in enumerate(self.spectrum_db):
                bar_h = db_to_y(db_val)
                Rectangle(pos=(x0 + i * step, y0), size=(step*0.9, bar_h - y0))

            # draw detection threshold line (red)
            Color(1, 0.2, 0.2, 0.9)
            line_y = db_to_y(self.threshold_db)
            Line(points=[x0, line_y, x0 + W, line_y], width=1.5)

            # draw noise gate line (blue)
            Color(0.2, 0.4, 1, 0.9)
            gate_y = db_to_y(self.noise_gate_db)
            Line(points=[x0, gate_y, x0 + W, gate_y], width=1.5)


class CalibrationScreen(Screen):
    def on_kv_post(self, base_widget):
        try:
            rootui = self.manager.get_screen("metronome").ids.rootui
            self.ids.threshold_slider.value = rootui.dyn_threshold_db
            self.ids.lowcut_slider.value = rootui.lowcut
            self.ids.highcut_slider.value = rootui.highcut
            self.ids.min_interval_slider.value = rootui.min_interval * 1000
            self.ids.noise_gate_slider.value = rootui.noise_gate_db

            self.ids.lowcut_label.text = f"Band-pass Low Cut: {int(rootui.lowcut)} Hz"
            self.ids.highcut_label.text = f"Band-pass High Cut: {int(rootui.highcut)} Hz"
            self.ids.min_interval_label.text = f"Refractory Period: {int(rootui.min_interval*1000)} ms"
            self.ids.noise_gate_label.text = f"Noise Gate: {rootui.noise_gate_db:.3f}"

            # store reference to spectrum widget for RootUI
            self.spectrum_widget = self.ids.spectrum
        except Exception as e:
            print("CalibrationScreen init error:", e)

    def on_slider_change(self, slider_id, val):
        try:
            rootui = self.manager.get_screen("metronome").ids.rootui
            if slider_id == "threshold":
                rootui.dyn_threshold_db = val
                self.ids.threshold_label.text = f"Detection Threshold: {int(val)} dB"
            elif slider_id == "lowcut":
                rootui.set_lowcut(val)
                self.ids.lowcut_label.text = f"Band-pass Low Cut: {int(val)} Hz"
            elif slider_id == "highcut":
                rootui.set_highcut(val)
                self.ids.highcut_label.text = f"Band-pass High Cut: {int(val)} Hz"
            elif slider_id == "min_interval":
                rootui.set_min_interval(val / 1000)
                self.ids.min_interval_label.text = f"Refractory Period: {int(val)} ms"
            elif slider_id == "noise_gate":
                rootui.noise_gate_db = val
                self.ids.noise_gate_label.text = f"Noise Gate: {int(val)} dB"
        except Exception as e:
            print("Calibration slider error:", e)

    def update_spectrum(self, spectrum_db):
        try:
            rootui = self.manager.get_screen("metronome").ids.rootui
            thresh_db = rootui.dyn_threshold_db
            gate_db = rootui.noise_gate_db
            self.ids.spectrum.update_spectrum(spectrum_db, thresh_db, gate_db)
        except Exception as e:
            print("Spectrum update error:", e)

    def save_settings(self):
        rootui = self.manager.get_screen("metronome").ids.rootui
        rootui.save_calibration()
        popup = Popup(
            title="Saved",
            content=Label(text="Calibration settings saved!", color=(1, 1, 1, 1)),
            size_hint=(0.5, 0.3)
        )
        popup.open()


# -----------------------
# App
# -----------------------
class SmartMetronomeApp(App):
    def build(self):
        Builder.load_file("assets/retro_theme.kv")
        sm = Builder.load_string(KV)
        self.stats_view = sm.get_screen("stats").ids.statsview
        return sm

if __name__ == "__main__":
    # Register retro font
    LabelBase.register(
        name="RetroFont",
        fn_regular="assets/fonts/PressStart2P-Regular.ttf"
    )

    SmartMetronomeApp().run()
