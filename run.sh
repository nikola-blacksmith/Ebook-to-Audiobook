#!/bin/bash

# Navigate to the directory where this script is located
cd "$(dirname "$0")"

echo "Starting Ebook to Audiobook..."

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found."
    echo "Please run './setup.sh' first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Run the Kivy application
python kivy_gui.py
