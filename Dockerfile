FROM quay.io/podman/stable:latest

EXPOSE 5000

ENV USERNAME="podman"
ENV HOME_DIR="/home/$USERNAME"
ENV BIN_DIR="$HOME_DIR/.local/bin"
ENV UV_INSTALL_DIR="$HOME_DIR/.local"
ENV PATH="$PATH:$BIN_DIR"
ENV DATA_DIR="$HOME_DIR/data"
ENV APP_DIR="$HOME_DIR/github-webhook-server"

RUN dnf -y install dnf-plugins-core \
  && dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo \
  && dnf -y update \
  && dnf -y install python3.8 \
  python3.9 \
  python3.10 \
  python3.11 \
  python3.12 \
  python3-pip \
  git \
  hub \
  unzip \
  libcurl-devel \
  gcc \
  python3-devel \
  libffi-devel \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin \
  slirp4netns \
  && dnf clean all \
  && rm -rf /var/cache /var/log/dnf* /var/log/yum.*

RUN ln -s /usr/bin/python3 /usr/bin/python

RUN mkdir -p $BIN_DIR \
  && mkdir -p $APP_DIR \
  && mkdir -p $DATA_DIR \
  && mkdir -p $DATA_DIR/logs \
  && mkdir -p /tmp/containers

COPY entrypoint.sh pyproject.toml uv.lock README.md $APP_DIR/
COPY webhook_server_container $APP_DIR/webhook_server_container/

RUN usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USERNAME \
  && chown -R $USERNAME:$USERNAME $HOME_DIR

USER $USERNAME
WORKDIR $HOME_DIR

# Download the latest uv installer
RUN curl -sSL https://astral.sh/uv/install.sh -o /tmp/uv-installer.sh \
  && sh /tmp/uv-installer.sh \
  && rm /tmp/uv-installer.sh

RUN set -x \
  && curl https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output $BIN_DIR/rosa-linux.tar.gz \
  && tar xvf $BIN_DIR/rosa-linux.tar.gz \
  && mv rosa $BIN_DIR/rosa \
  && chmod +x $BIN_DIR/rosa \
  && rm -rf $BIN_DIR/rosa-linux.tar.gz

RUN python -m pip install --no-cache-dir pip --upgrade \
  && python -m pip install --no-cache-dir poetry tox twine pre-commit

RUN python3.8 -m ensurepip \
  && python3.9 -m ensurepip \
  && python3.10 -m ensurepip \
  && python3.11 -m ensurepip \
  && python3.12 -m ensurepip \
  && python3.8 -m pip install tox \
  && python3.9 -m pip install tox \
  && python3.10 -m pip install tox \
  && python3.11 -m pip install tox \
  && python3.12 -m pip install tox

WORKDIR $APP_DIR

RUN uv sync

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1

ENTRYPOINT ["./entrypoint.sh"]
