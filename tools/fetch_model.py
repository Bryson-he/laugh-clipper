"""Downloads Google's YAMNet sound classifier into models/ so it gets bundled into the exe."""

import urllib.request
from pathlib import Path

URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
OUT = Path(__file__).resolve().parent.parent / "models" / "yamnet.tflite"

if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    if not OUT.exists():
        urllib.request.urlretrieve(URL, OUT)
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB)")
