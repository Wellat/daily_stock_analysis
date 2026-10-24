#!/bin/bash
# run in wsl
set -e
source .venv/bin/activate
sudo service cron start
python main.py --serve