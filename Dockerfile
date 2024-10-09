FROM quay.io/podman/stable:latest
EXPOSE 5000

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
  && dnf clean all \
  && rm -rf /var/cache /var/log/dnf* /var/log/yum.*

ENV USER_BIN_DIR="/root/.local/bin"
ENV UV_INSTALL_DIR="/root/.local"
ENV PATH="$PATH:$USER_BIN_DIR"

ENV DATA_DIR=/webhook_server
ENV APP_DIR=/github-webhook-server

RUN mkdir -p $USER_BIN_DIR \
  && mkdir -p $DATA_DIR \
  && mkdir -p $DATA_DIR/logs \
  && mkdir -p /tmp/containers

# Download the latest uv installer
RUN curl -sSL https://astral.sh/uv/install.sh -o /tmp/uv-installer.sh \
  && sh /tmp/uv-installer.sh \
  && rm /tmp/uv-installer.sh

RUN set -x \
  && curl https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output /tmp/rosa-linux.tar.gz \
  && tar xvf /tmp/rosa-linux.tar.gz --no-same-owner \
  && mv rosa $USER_BIN_DIR/rosa \
  && chmod +x $USER_BIN_DIR/rosa \
  && rm -rf /tmp/rosa-linux.tar.gz

RUN ln -s /usr/bin/python3 /usr/bin/python

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

COPY entrypoint.sh pyproject.toml uv.lock README.md $APP_DIR/
COPY webhook_server_container $APP_DIR/webhook_server_container/

WORKDIR $APP_DIR

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
ENTRYPOINT ["./entrypoint.sh"]
