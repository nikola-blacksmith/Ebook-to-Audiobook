#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "========================================"
echo "Ebook to Audiobook - Mac Setup Script"
echo "========================================"
echo ""

# 1. Check for Homebrew (needed for ffmpeg)
if ! command -v brew &> /dev/null; then
    echo "[!] Homebrew is not installed."
    echo "[!] Please install it first from https://brew.sh/ or run:"
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
fi

# 2. Install ffmpeg (required by pydub for audio conversion)
echo "[*] Checking for ffmpeg (required for audio processing)..."
if ! command -v ffmpeg &> /dev/null; then
    echo "    Installing ffmpeg via Homebrew..."
    brew install ffmpeg
else
    echo "    ffmpeg is already installed."
fi
echo ""

# 3. Detect or install Python 3.10+ (required for Pocket-TTS)
echo "[*] Detecting Python 3.10+ ..."
PYTHON_BIN=""
for py_candidate in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py_candidate" &> /dev/null; then
        PY_VER=$("$py_candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON_BIN=$(command -v "$py_candidate")
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "    Python >= 3.10 not found. Installing python@3.12 via Homebrew..."
    brew install python@3.12
    PYTHON_BIN=$(command -v python3.12 || echo "/opt/homebrew/bin/python3.12")
fi

echo "    Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"
echo ""

# 4. Create and activate virtual environment
echo "[*] Setting up Python virtual environment (venv)..."
if [ ! -d "venv" ] || [ ! -f "venv/bin/activate" ]; then
    "$PYTHON_BIN" -m venv venv
    echo "    Created 'venv' directory."
else
    # Check existing venv python version
    VENV_VER=$(./venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    VENV_MINOR=$(echo "$VENV_VER" | cut -d. -f2)
    if [ "$VENV_MINOR" -lt 10 ]; then
        echo "    Existing venv is Python $VENV_VER (< 3.10). Recreating with $PYTHON_BIN..."
        rm -rf venv
        "$PYTHON_BIN" -m venv venv
    else
        echo "    'venv' directory already exists with Python $VENV_VER."
    fi
fi

# Activate the venv
source venv/bin/activate
echo "    Virtual environment activated."
echo ""

# 5. Install requirements
echo "[*] Upgrading pip..."
pip install --upgrade pip

echo "[*] Installing Python dependencies..."
pip install -r requirements.txt
echo ""

# 6. Pre-download AI Models for offline usage
echo "[*] Pre-downloading Kokoro & Pocket-TTS (MLX) AI models so the app works offline..."
export HF_HUB_OFFLINE=0
python -c "
try:
    from huggingface_hub import snapshot_download
    print('    Pre-downloading Kokoro-82M model weights...')
    snapshot_download(repo_id='hexgrad/Kokoro-82M')
    print('    Kokoro-82M downloaded successfully.')
except Exception as e:
    print('    Notice during Kokoro download:', e)

try:
    from pocket_tts_mlx import TTSModel
    print('    Pre-downloading Pocket-TTS (MLX) model weights and English voice profiles...')
    m = TTSModel.load_model()
    en_voices = ['alba', 'azelma', 'cosette', 'eponine', 'fantine', 'javert', 'jean', 'marius']
    for v in en_voices:
        m.get_state_for_audio_prompt(v)
    print('    All 8 English Pocket-TTS voices cached successfully.')
except Exception as e:
    print('    Notice during Pocket-TTS download:', e)
"
echo ""

# 7. Install tesseract (optional OCR engine for scanned PDFs)
echo "[*] Checking for tesseract (OCR engine for scanned PDF pages)..."
if ! command -v tesseract &> /dev/null; then
    echo "    Installing tesseract via Homebrew..."
    brew install tesseract
else
    echo "    tesseract is already installed."
fi
echo ""


echo "========================================"
echo "Setup Complete! 🎉"
echo "========================================"
echo "To start the app, run:"
echo "  ./run.sh"
echo ""
