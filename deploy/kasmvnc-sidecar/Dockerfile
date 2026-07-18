FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG KASMVNC_VERSION=1.4.0
ARG KASMVNC_DEB_SHA256=12bac6014149c5fdee75f0d403785aaa3e5dd4ea222de73253a5d4181bc9567e

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl procps tini x11-utils \
    && curl --fail --location --retry 3 \
      --output /tmp/kasmvncserver.deb \
      "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_noble_${KASMVNC_VERSION}_amd64.deb" \
    && echo "${KASMVNC_DEB_SHA256}  /tmp/kasmvncserver.deb" | sha256sum --check --strict \
    && apt-get install -y --no-install-recommends /tmp/kasmvncserver.deb \
    && rm -f /tmp/kasmvncserver.deb \
    && rm -rf /var/lib/apt/lists/*

COPY kasmvnc.yaml /etc/kasmvnc/kasmvnc.yaml
COPY entrypoint.sh healthcheck.sh /usr/local/bin/

RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
    && mkdir -p /run/kasmvnc \
    && chmod 0700 /run/kasmvnc

EXPOSE 8444/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
