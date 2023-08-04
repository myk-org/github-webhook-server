FROM fedora:38
EXPOSE 5000

ENV USER_HOME=/home/webhook
ENV USER_BIN_DIR="/home/webhook/.local/bin"
ENV PATH="$USER_BIN_DIR:$PATH"

VOLUME /var/run/docker.sock

RUN useradd -ms /bin/bash webhook \
    && mkdir -p $USER_BIN_DIR \

RUN dnf -y update \
    && dnf -y install python3.8 python3.9 python3.10 python3.11 python3-pip git hub podman unzip \
    && dnf clean all \
    && rm -rf /var/cache/yum

RUN set -x \
    && curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash \
    && curl -L https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output /tmp/rosa-linux.tar.gz \
    && tar xvf /tmp/rosa-linux.tar.gz --no-same-owner \
    && mv rosa $USER_BIN_DIR//rosa \
    && chmod +x $USER_BIN_DIR//rosa

RUN curl -L https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-5.0.0.2966-linux.zip --output /tmp/sonar-scanner-cli.zip \
    && unzip /tmp/sonar-scanner-cli.zip \
    && mv -f /sonar-scanner-5.0.0.2966-linux /sonar-scanner-cli

RUN ln -s /usr/bin/python3 /usr/bin/python

USER webhook
RUN python -m pip install pip --upgrade \
    && python -m pip install pipx importlib poetry tox

COPY pyproject.toml poetry.lock README.md $USER_HOME/github-webhook-server/
COPY webhook_server_container $USER_HOME/github-webhook-server/webhook_server_container/

WORKDIR $USER_HOME/github-webhook-server

RUN poetry config cache-dir $USER_HOME/github-webhook-server \
    && poetry config virtualenvs.in-project true \
    && poetry config installer.max-workers 10 \
    && poetry install

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
ENTRYPOINT ["poetry", "run", "python3", "$USER_HOME/github-webhook-server/webhook_server_container/app.py"]
