# Smart Metronome (Kivy, Live Audio)

A real-time Python "smart metronome" that listens to your live instrument via microphone, 
compares your onsets to the click, and adapts the tempo:

- If you're **on time** (within a tolerance window), it **speeds up** slightly.
- If you're **early** or **late**, it **slows down** to let you settle in.

## Features
- Kivy GUI with Start/Stop, BPM display, and early/late feedback.
- Live audio input using `sounddevice`.
- Onset detection via `aubio`.
- Click playback via `simpleaudio`.
- Gentle tempo changes with smoothing/clamping.

## Install
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python main.py
```

## Tips
- Use headphones if possible to avoid the mic hearing the click.
- If you get audio device errors, set the input device in the app.
- Thresholds and tempo step can be tuned in the UI drop-down (gear icon) or constants.
