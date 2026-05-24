#!/bin/bash
# Пересчёт ML-confidence для правил стоп-бота.
# Запускать раз в сутки через cron:
#   0 4 * * * /Users/markvasilev/Desktop/FB_Agent/bin/recalc_rule_confidence.sh >> /var/log/fb_agent/recalc_confidence.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
.venv/bin/python scripts/recalc_rule_confidence.py --window-days 7
