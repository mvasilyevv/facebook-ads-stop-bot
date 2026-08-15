FROM node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS kasm-client-builder

ARG KASMVNC_SOURCE_COMMIT=17265facc40ab50db5740cdf0d12c61173edafc9
ARG KASM_NOVNC_SOURCE_COMMIT=475ecfa5356579ef222983c7ce4619a7576a3bce

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git init /src \
    && git -C /src remote add origin https://github.com/kasmtech/KasmVNC.git \
    && git -C /src fetch --depth 1 origin "${KASMVNC_SOURCE_COMMIT}" \
    && git -C /src checkout --detach FETCH_HEAD \
    && test "$(git -C /src rev-parse HEAD)" = "${KASMVNC_SOURCE_COMMIT}" \
    && git -C /src submodule update --init --depth 1 kasmweb \
    && test "$(git -C /src/kasmweb rev-parse HEAD)" = "${KASM_NOVNC_SOURCE_COMMIT}"

WORKDIR /src/kasmweb
COPY kasm-client/package.json kasm-client/package-lock.json ./
RUN npm ci --ignore-scripts

COPY kasm-client/apply-patch.mjs /opt/fb-agent-kasm/apply-patch.mjs
COPY kasm-client/fb-agent-client.js app/fb-agent-client.js
COPY kasm-client/fb-agent-client.css app/styles/fb-agent-client.css
COPY kasm-client/vite.config.js vite.config.js
RUN node /opt/fb-agent-kasm/apply-patch.mjs /src/kasmweb \
    && npm run build

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG KASMVNC_VERSION=1.5.0
ARG KASMVNC_DEB_SHA256=f599fe02e2175b9817b6165f74a5d2bebdc73118dde9181ba3410963bed7ae1e
ARG VISION_VERSION=3.6.8
ARG VISION_DEB_URL=https://vision-releases.s3.eu-north-1.amazonaws.com/v3.6.8/linux-deb/vision_3.6.8_amd64.deb
ARG VISION_DEB_SHA256=b0040ba8feac569d86b4b6a1ab9269837c8b0a2d2c5a26657b591395f4bbb762
# Обычный браузер на столе — для облачных панелей: Vision, трекер, кабинет.
# ESR, а не текущий релиз: приборная машина не должна получать новый мажор
# каждый месяц. В Ubuntu 24.04 apt-пакет firefox — заглушка над snap, внутри
# контейнера он не работает, поэтому берём официальный tarball по фиксированной
# версии и сверяем контрольную сумму, как KasmVNC и Vision.
ARG FIREFOX_VERSION=140.13.0esr
ARG FIREFOX_SHA256=866d7e5f94abe93132e02a0db72da32b6e133905fcbea6afa417a96d496021da

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
# готовому образу отдал бы и digest, и собственный entrypoint, и KasmVNC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl dbus-x11 gosu locales procps tini xz-utils \
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
    && curl --fail --location --retry 4 --output /tmp/kasmvncserver.deb \
      "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_noble_${KASMVNC_VERSION}_amd64.deb" \
    && echo "${KASMVNC_DEB_SHA256}  /tmp/kasmvncserver.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/kasmvncserver.deb \
    && curl --fail --location --retry 4 --output /tmp/vision.deb "${VISION_DEB_URL}" \
    && echo "${VISION_DEB_SHA256}  /tmp/vision.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/vision.deb \
    && test "$(dpkg-query -W -f='${Version}' vision)" = "${VISION_VERSION}" \
    && test "$(getent passwd 1000 | cut -d: -f1)" = "ubuntu" \
    && test "$(getent group 1000 | cut -d: -f1)" = "ubuntu" \
    && usermod --login vision --home /config --shell /bin/bash ubuntu \
    && groupmod --new-name vision ubuntu \
    && test "$(id -u vision)" = "1000" \
    && test "$(id -g vision)" = "1000" \
    # KasmVNC читает ssl-cert-snakeoil под этим пользователем. Без группы
    # ssl-cert ключ недоступен, а сервер сообщает об этом как об отсутствии
    # файла и уходит в цикл перезапусков.
    && usermod --append --groups ssl-cert vision \
    && gosu vision test -r /etc/ssl/private/ssl-cert-snakeoil.key \
    && locale-gen en_US.UTF-8 ru_RU.UTF-8 \
    && rm -f /tmp/kasmvncserver.deb /tmp/vision.deb \
    && rm -rf /var/lib/apt/lists/*

COPY --from=kasm-client-builder /src/kasmweb/dist/ /usr/share/kasmvnc/www/
COPY kasmvnc.yaml /etc/kasmvnc/kasmvnc.yaml
COPY entrypoint.sh healthcheck.sh vision-window-fit.sh /usr/local/bin/
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/fb-agent-vision-desktop/THIRD_PARTY_NOTICES.md
COPY firefox.desktop /usr/share/applications/firefox.desktop

RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
      /usr/local/bin/vision-window-fit.sh \
    && chmod 0644 /usr/share/applications/firefox.desktop \
    && install -d -m 0700 /run/kasmvnc

EXPOSE 50051/tcp 8444/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
