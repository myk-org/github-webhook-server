FROM fedora:38
EXPOSE 5000

RUN dnf -y update \
    && dnf -y install python3.8 python3.9 python3.10 python3.11 python3-pip git hub podman \
    && dnf clean all \
    && rm -rf /var/cache/yum

RUN set -x \
    && curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash \
    && curl -L https://mirror.openshift.com/pub/openshift-v4/clients/rosa/latest/rosa-linux.tar.gz --output /tmp/rosa-linux.tar.gz \
    && tar xvf /tmp/rosa-linux.tar.gz --no-same-owner \
    && mv rosa /usr/bin/rosa \
    && chmod +x /usr/bin/rosa

RUN ln -s /usr/bin/python3 /usr/bin/python

RUN python -m pip install pip --upgrade \
    && python -m pip install pipx importlib poetry tox

COPY webhook_server_container pyproject.toml poetry.lock /app/

WORKDIR /app

RUN poetry config cache-dir /app \
    && poetry config virtualenvs.in-project true \
    && poetry config installer.max-workers 10 \
    && poetry install

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
ENTRYPOINT ["poetry", "run", "python3", "/app/app.py"]
