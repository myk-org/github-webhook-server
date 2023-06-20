FROM fedora:38
EXPOSE 5000

ENV PATH="/root/.local/bin:$PATH"

RUN set -x \
    && dnf -y update \
    && dnf -y install python3.8 python3.9 python3.10 python3.11 python3-pip git hub podman \
    && dnf clean all \
    && rm -rf /var/cache/yum

COPY webhook_server_container pyproject.toml poetry.lock /app/

WORKDIR /app
RUN ln -s /usr/bin/python3 /usr/bin/python \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && poetry --version \
    && python3 -m pip install pip --upgrade \
    && python3 -m pip install pipx importlib \
    && poetry config cache-dir /app \
    && poetry config virtualenvs.in-project true \
    && poetry config installer.max-workers 10 \
    && poetry config --list \
    && poetry install

HEALTHCHECK CMD curl --fail http://127.0.0.1:5000/webhook_server/healthcheck || exit 1
ENTRYPOINT ["poetry", "run", "python3", "/app/app.py"]
