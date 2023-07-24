FROM fedora:38
EXPOSE 5000

ENV USER_BIN_DIR="/home/webhook/.local/bin"
ENV PATH="$USER_BIN_DIR:$PATH"

VOLUME /var/run/docker.sock

RUN useradd -ms /bin/bash webhook \
    && mkdir -p $USER_BIN_DIR

RUN set -x \
    && curl -L "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" --output $USER_BIN_DIR/kubectl \
    && chmod +x $USER_BIN_DIR/kubectl \
    && curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash \
    && curl -L https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output /tmp/rosa-linux.tar.gz \
    && tar xvf /tmp/rosa-linux.tar.gz --no-same-owner \
    && mv rosa $USER_BIN_DIR/rosa \
    && chmod +x $USER_BIN_DIR/rosa \
    && dnf -y update \
    && dnf -y install python3.8 python3.9 python3.10 python3.11 python3-pip git hub podman dnf-plugins-core \
    && dnf config-manager --add-repo=https://download.docker.com/linux/fedora/docker-ce.repo \
    && dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && dnf clean all \
    && rm -rf /var/cache/yum

RUN ln -s /usr/bin/python3 /usr/bin/python \
    && usermod -g docker webhook \
    && groupmod -g 972 docker \
    && usermod -u $(stat -c %u /home/webhook) webhook \
    && chown -R webhook:webhook /home/webhook

USER webhook
WORKDIR /home/webhook/app
COPY webhook_server_container pyproject.toml poetry.lock /home/webhook/app/

RUN curl -sSL https://install.python-poetry.org | python3 - \
    && python3 -m pip install pip --upgrade \
    && python3 -m pip install pipx importlib \
    && poetry config cache-dir /home/webhook/app \
    && poetry config virtualenvs.in-project true \
    && poetry config installer.max-workers 10 \
    && poetry install

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
ENTRYPOINT poetry run python3 /home/webhook/app/app.py
