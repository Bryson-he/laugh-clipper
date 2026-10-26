"""Downloads the models into models/ so they get bundled into the exe: Google's YAMNet sound classifier, and
Vosk's small English speech model (for the "don't clip" voice command)."""

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MODELS = Path(__file__).resolve().parent.parent / "models"
YAMNET_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
VOSK_NAME = "vosk-model-small-en-us-0.15"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip"


def size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size


def fetch_yamnet():
    out = MODELS / "yamnet.tflite"
    if not out.exists():
        urllib.request.urlretrieve(YAMNET_URL, out)
    return out


def fetch_vosk():
    out = MODELS / "vosk"
    if not (out / "am").is_dir():
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "vosk.zip"
            urllib.request.urlretrieve(VOSK_URL, archive)
            with zipfile.ZipFile(archive) as z:
                for name in z.namelist():                      # only plain relative paths
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise RuntimeError(f"unexpected path in the model archive: {name}")
                z.extractall(tmp)
            if out.exists():
                shutil.rmtree(out)
            shutil.move(str(Path(tmp) / VOSK_NAME), out)
    return out


if __name__ == "__main__":
    MODELS.mkdir(exist_ok=True)
    for path in (fetch_yamnet(), fetch_vosk()):
        print(f"{path} ({size(path) // 1024} KB)")
