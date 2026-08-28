#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

sudo apt update
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libjpeg-dev \
  zlib1g-dev \
  libfreetype6-dev \
  fonts-dejavu-core \
  sqlite3 \
  git

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
mkdir -p data logs

python -m compileall -q .

echo "Raspberry-Bot dependencies installed."
echo "Next: cp .env.example .env && edit .env"
