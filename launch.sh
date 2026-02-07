#!/bin/bash
# APN Core Launcher
# Ensures correct Python version is used

# Find Python 3.10
if command -v python3.10 &> /dev/null; then
    PYTHON=python3.10
elif command -v python3 &> /dev/null; then
    PYTHON=python3
else
    echo "Error: Python 3.10+ required"
    exit 1
fi

echo "Starting APN Core with $PYTHON..."
cd "$(dirname "$0")"
exec $PYTHON main.py
