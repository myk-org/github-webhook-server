FROM quay.io/podman/stable:v5

EXPOSE 5000

ENV USERNAME="podman"
ENV HOME_DIR="/home/$USERNAME"
ENV BIN_DIR="$HOME_DIR/.local/bin"
ENV PATH="$PATH:$BIN_DIR"
ENV DATA_DIR="$HOME_DIR/data"
ENV APP_DIR="$HOME_DIR/github-webhook-server"

RUN dnf -y install dnf-plugins-core \
  && dnf -y update \
  && dnf -y install \
  git \
  hub \
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
  && dnf clean all \
  && rm -rf /var/cache /var/log/dnf* /var/log/yum.*


RUN mkdir -p $BIN_DIR \
  && mkdir -p $APP_DIR \
  && mkdir -p $DATA_DIR \
  && mkdir -p $DATA_DIR/logs

COPY gunicorn.conf.py pyproject.toml uv.lock README.md $APP_DIR/
COPY webhook_server $APP_DIR/webhook_server/

RUN usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USERNAME \
  && chown -R $USERNAME:$USERNAME $HOME_DIR

USER $USERNAME
WORKDIR $HOME_DIR

ENV UV_PYTHON=python3.13
ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_SYNC=1
ENV UV_CACHE_DIR=${APP_DIR}/.cache

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx ${BIN_DIR}/
RUN uv tool install pre-commit && uv tool install poetry

RUN set -x \
  && curl https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output $BIN_DIR/rosa-linux.tar.gz \
  && tar xvf $BIN_DIR/rosa-linux.tar.gz \
  && mv rosa $BIN_DIR/rosa \
  && chmod +x $BIN_DIR/rosa \
  && rm -rf $BIN_DIR/rosa-linux.tar.gz \
  && curl -L https://github.com/regclient/regclient/releases/latest/download/regctl-linux-amd64 >$BIN_DIR/regctl \
  && chmod +x $BIN_DIR/regctl

WORKDIR $APP_DIR

RUN uv sync

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1

ENTRYPOINT ["uv", "run", "gunicorn", "webhook_server.app:FASTAPI_APP", "-c", "./gunicorn.conf.py"]
