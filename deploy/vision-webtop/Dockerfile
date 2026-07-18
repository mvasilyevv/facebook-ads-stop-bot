FROM lscr.io/linuxserver/webtop:ubuntu-xfce@sha256:f106549e832cd9b548263d45f81020afd96c4b090a56e1d420e34ce8e4cb36a0

ARG VISION_VERSION=3.6.8
ARG VISION_DEB_URL=https://vision-releases.s3.eu-north-1.amazonaws.com/v3.6.8/linux-deb/vision_3.6.8_amd64.deb
ARG VISION_DEB_SHA256=b0040ba8feac569d86b4b6a1ab9269837c8b0a2d2c5a26657b591395f4bbb762

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl wmctrl x11-utils \
    && curl --fail --location --retry 4 --output /tmp/vision.deb "$VISION_DEB_URL" \
    && echo "$VISION_DEB_SHA256  /tmp/vision.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/vision.deb \
    && test "$(dpkg-query -W -f='${Version}' vision)" = "$VISION_VERSION" \
    && rm -f /tmp/vision.deb \
    && rm -rf /var/lib/apt/lists/*

COPY vision-service-run /etc/s6-overlay/s6-rc.d/svc-vision/run
COPY vision-window-fit-run /etc/s6-overlay/s6-rc.d/svc-vision-window-fit/run
RUN install -d /etc/s6-overlay/s6-rc.d/svc-vision/dependencies.d \
    /etc/s6-overlay/s6-rc.d/svc-vision-window-fit/dependencies.d \
    /etc/s6-overlay/s6-rc.d/user/contents.d \
    && printf 'longrun\n' >/etc/s6-overlay/s6-rc.d/svc-vision/type \
    && printf 'longrun\n' >/etc/s6-overlay/s6-rc.d/svc-vision-window-fit/type \
    && touch /etc/s6-overlay/s6-rc.d/svc-vision/dependencies.d/svc-xorg \
    /etc/s6-overlay/s6-rc.d/svc-vision/dependencies.d/svc-de \
    /etc/s6-overlay/s6-rc.d/svc-vision/dependencies.d/init-services \
    /etc/s6-overlay/s6-rc.d/svc-vision-window-fit/dependencies.d/svc-vision \
    /etc/s6-overlay/s6-rc.d/user/contents.d/svc-vision \
    /etc/s6-overlay/s6-rc.d/user/contents.d/svc-vision-window-fit \
    && chmod 0755 /etc/s6-overlay/s6-rc.d/svc-vision/run \
    /etc/s6-overlay/s6-rc.d/svc-vision-window-fit/run
