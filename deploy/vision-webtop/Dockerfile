# Стол без веб-канала: X-сервером служит Xvfb, а доступ даёт нативный
# RustDesk. Браузерный канал убран целиком — на iPhone он не мог отдать
# системный буфер обмена, и это ограничение WebKit, а не настройка.
FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG VISION_VERSION=3.6.8
ARG VISION_DEB_URL=https://vision-releases.s3.eu-north-1.amazonaws.com/v3.6.8/linux-deb/vision_3.6.8_amd64.deb
ARG VISION_DEB_SHA256=b0040ba8feac569d86b4b6a1ab9269837c8b0a2d2c5a26657b591395f4bbb762
# Обычный браузер на столе — для облачных панелей: Vision, трекер, кабинет.
# ESR, а не текущий релиз: приборная машина не должна получать новый мажор
# каждый месяц. В Ubuntu 24.04 apt-пакет firefox — заглушка над snap, внутри
# контейнера он не работает, поэтому берём официальный tarball по фиксированной
# версии и сверяем контрольную сумму, как Vision.
ARG FIREFOX_VERSION=140.13.0esr
ARG FIREFOX_SHA256=866d7e5f94abe93132e02a0db72da32b6e133905fcbea6afa417a96d496021da
# Нативный канал к тому же столу. Браузерный буфер обмена на iPhone не
# чинится: WebKit требует свежего жеста и считает его протухшим после любого
# await, а VNC-клиент между жестом и буфером ходит на сервер. RustDesk отдаёт
# системный буфер и нормальные жесты, потому что он не в браузере.
# Не ниже 1.4.6: в ней закрыты три уязвимости высокой критичности, задевавшие
# всё до 1.4.5 включительно.
ARG RUSTDESK_VERSION=1.4.6
ARG RUSTDESK_SHA256=0da46d7a7b252282ded5323f74319a10c1fa7271001d3b297b3def415c8c8f04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:1 \
    HOME=/config \
    XDG_CONFIG_HOME=/config/.config \
    XDG_CACHE_HOME=/config/.cache

# Полное окружение XFCE, а не пять пакетов: настройки внешнего вида, клавиатуры
# и мыши, поиск программ, уведомления, буфер обмена, снимок экрана, диспетчер
# задач, менеджер архивов, просмотр изображений и PDF, редактор, калькулятор,
# монтирование дисков. Так стол ведёт себя как обычный Linux, а не как киоск с
# одной программой.
#
# Пакеты перечислены поимённо, а не метапакетами xfce4/xfce4-goodies: при
# --no-install-recommends метапакет тянет только Depends, и состав окружения
# начинает зависеть от того, что мейнтейнер счёл обязательным. Здесь состав
# задаём мы и видим его при чтении.
#
# База остаётся своей, закреплённой по digest. Полнота окружения и доверенная
# цепочка поставки — разные вещи, второй жертвовать незачем: возврат к чужому
# готовому образу отдал бы и digest, и собственный entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl dbus-x11 gosu locales procps tini xz-utils xvfb \
        fonts-dejavu-core fonts-liberation2 fonts-noto-color-emoji \
        libasound2t64 libdbus-glib-1-2 libnss3 libxt6t64 \
        gvfs gvfs-backends wmctrl x11-utils xdg-utils \
        xfce4-appfinder xfce4-notifyd xfce4-panel xfce4-session xfce4-settings \
        xfce4-terminal xfdesktop4 xfwm4 \
        thunar thunar-archive-plugin thunar-volman \
        xfce4-clipman-plugin xfce4-screenshooter xfce4-taskmanager \
        atril engrampa galculator mousepad ristretto \
    && curl --fail --location --retry 4 --output /tmp/firefox.tar.xz \
      "https://ftp.mozilla.org/pub/firefox/releases/${FIREFOX_VERSION}/linux-x86_64/en-US/firefox-${FIREFOX_VERSION}.tar.xz" \
    && echo "${FIREFOX_SHA256}  /tmp/firefox.tar.xz" | sha256sum --check --strict \
    && tar --extract --xz --directory /opt --file /tmp/firefox.tar.xz \
    && rm -f /tmp/firefox.tar.xz \
    && ln -s /opt/firefox/firefox /usr/local/bin/firefox \
    && test -x /opt/firefox/firefox \
    && curl --fail --location --retry 4 --output /tmp/vision.deb "${VISION_DEB_URL}" \
    && echo "${VISION_DEB_SHA256}  /tmp/vision.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/vision.deb \
    && test "$(dpkg-query -W -f='${Version}' vision)" = "${VISION_VERSION}" \
    && curl --fail --location --retry 4 --output /tmp/rustdesk.deb \
      "https://github.com/rustdesk/rustdesk/releases/download/${RUSTDESK_VERSION}/rustdesk-${RUSTDESK_VERSION}-x86_64.deb" \
    && echo "${RUSTDESK_SHA256}  /tmp/rustdesk.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/rustdesk.deb \
    && test -x /usr/bin/rustdesk \
    && test "$(getent passwd 1000 | cut -d: -f1)" = "ubuntu" \
    && test "$(getent group 1000 | cut -d: -f1)" = "ubuntu" \
    && usermod --login vision --home /config --shell /bin/bash ubuntu \
    && groupmod --new-name vision ubuntu \
    && test "$(id -u vision)" = "1000" \
    && test "$(id -g vision)" = "1000" \
    && locale-gen en_US.UTF-8 ru_RU.UTF-8 \
    && rm -f /tmp/vision.deb /tmp/rustdesk.deb \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh healthcheck.sh vision-window-fit.sh /usr/local/bin/
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/fb-agent-vision-desktop/THIRD_PARTY_NOTICES.md
COPY firefox.desktop /usr/share/applications/firefox.desktop

RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
      /usr/local/bin/vision-window-fit.sh \
    && chmod 0644 /usr/share/applications/firefox.desktop

EXPOSE 50051/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
