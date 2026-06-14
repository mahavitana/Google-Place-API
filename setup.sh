#!/usr/bin/env bash
# One-time setup on Debian. Run from inside the biz-scraper folder:
#   bash setup.sh
set -e
echo ">> Installing system packages (needs sudo)..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip
echo ">> Creating virtual environment..."
python3 -m venv venv
echo ">> Installing Python dependencies..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo ">> Created config.yaml (edit it before running)."
fi
echo ""
echo "Setup complete. Next:"
echo "  1) edit config.yaml"
echo "  2) source venv/bin/activate"
echo "  3) python run.py all"
