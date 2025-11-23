FROM quay.io/podman/stable:v5

EXPOSE 5000

ENV USERNAME="podman"
ENV HOME_DIR="/home/$USERNAME"
ENV BIN_DIR="$HOME_DIR/.local/bin"
ENV PATH="$PATH:$BIN_DIR" \
  DATA_DIR="$HOME_DIR/data" \
  APP_DIR="$HOME_DIR/github-webhook-server"

RUN systemd-machine-id-setup

RUN  dnf --nodocs --setopt=install_weak_deps=False --disable-repo=fedora-cisco-openh264 -y install dnf-plugins-core \
  && dnf --nodocs --setopt=install_weak_deps=False --disable-repo=fedora-cisco-openh264 -y update \
  && dnf --nodocs --setopt=install_weak_deps=False --disable-repo=fedora-cisco-openh264 -y install \
  git \
  unzip \
  gcc \
  python3-devel \
  python3.10-devel \
  python3.11-devel \
  python3.12-devel \
  python3.13-devel \
  clang \
  cargo \
  libcurl-devel \
  libxml2-devel \
  nodejs \
  npm \
  which \
  tini \
  && dnf clean all \
  && rm -rf /var/cache /var/log/dnf* /var/log/yum.* /var/lib/dnf /var/log/dnf.* /var/log/hawkey.log


RUN mkdir -p $BIN_DIR \
  && mkdir -p $APP_DIR \
  && mkdir -p $DATA_DIR \
  && mkdir -p $DATA_DIR/logs

COPY entrypoint.py pyproject.toml uv.lock README.md alembic.ini $APP_DIR/
COPY webhook_server $APP_DIR/webhook_server/
COPY scripts $APP_DIR/scripts/

RUN usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USERNAME \
  && chown -R $USERNAME:$USERNAME $HOME_DIR

USER $USERNAME
WORKDIR $HOME_DIR

ENV UV_PYTHON=python3.13 \
  UV_COMPILE_BYTECODE=1 \
  UV_NO_SYNC=1 \
  UV_CACHE_DIR=${APP_DIR}/.cache \
  PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx ${BIN_DIR}/
RUN uv tool install pre-commit && uv tool install poetry && uv tool install prek && uv tool install tox

RUN set -ex \
  && curl --fail -vL https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz | tar -C $BIN_DIR -xzvf - rosa \
  && chmod +x $BIN_DIR/rosa \
  && curl --fail -vL https://github.com/regclient/regclient/releases/latest/download/regctl-linux-amd64 -o $BIN_DIR/regctl \
  && chmod +x $BIN_DIR/regctl \
  && curl --fail -vL https://github.com/mislav/hub/releases/download/v2.14.2/hub-linux-amd64-2.14.2.tgz | tar --wildcards --strip-components=2 -C $BIN_DIR -xzvf - '*/bin/hub' \
  && chmod +x $BIN_DIR/hub

WORKDIR $APP_DIR

RUN uv sync

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1

ENTRYPOINT ["tini", "--", "uv", "run", "entrypoint.py"]
