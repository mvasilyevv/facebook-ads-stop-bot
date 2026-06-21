#!/usr/bin/env bash
# Улучшение рендеринга шрифтов на Linux-сервере (для Vision/Chrome GUI на Xvfb/NoMachine).
#
# Решает: "фиолетовый кружок" (missing emoji glyph) + тонкий/режущий глаз шрифт.
#   - emoji-шрифт (Noto Color Emoji)
#   - Noto + Liberation (Arial/Times-подобные, Windows-like метрики)
#   - fontconfig: сглаживание + субпиксельный хинтинг (аналог ClearType)
#
# Запуск: bash deploy/setup-fonts.sh
set -e
export DEBIAN_FRONTEND=noninteractive

echo "=== Установка шрифтов ==="
apt-get install -y \
  fonts-noto-color-emoji \
  fonts-noto-core \
  fonts-noto-ui-core \
  fonts-noto-cjk \
  fonts-liberation \
  fontconfig

echo "=== fontconfig (сглаживание + хинтинг + emoji-fallback) ==="
cp "$(dirname "$0")/fonts-local.conf" /etc/fonts/local.conf
fc-cache -f

echo "Готово. Перезапусти Vision, чтобы подхватил шрифты:"
echo "  systemctl restart vision.service && sleep 18 && systemctl start vision-autostart.service"
