#!/usr/bin/env bash
# Render Build Script for Chat Transit Backend
# This runs during the build phase on Render

set -e  # Exit on any error

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Installing Playwright..."
pip install playwright

echo "==> Installing Chromium browser..."
playwright install chromium

echo "==> Installing Chromium system dependencies..."
playwright install-deps chromium

echo "==> Build complete."
