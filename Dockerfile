FROM ubuntu:20.04
EXPOSE 5000

RUN set -x \
    && apt-get update \
    && apt-get install -y curl add-apt-key \
    && echo "deb https://download.opensuse.org/repositories/devel:/kubic:/libcontainers:/stable/xUbuntu_20.04/ /" | tee /etc/apt/sources.list.d/devel:kubic:libcontainers:stable.list \
    && curl -L https://download.opensuse.org/repositories/devel:/kubic:/libcontainers:/stable/xUbuntu_20.04/Release.key | apt-key add - \
    && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y git hub podman \
    && apt-get install -y --no-install-recommends python3.8 python3.9 python3.10 python3.11 python3-pip\
    && apt-get purge -y --auto-remove software-properties-common \
    && rm -rf /var/lib/apt/lists/*

COPY . /github-gitlab-webhook-server
WORKDIR /github-gitlab-webhook-server
RUN python3 -m pip install pip --upgrade \
    && python3 -m pip install poetry \
    && poetry config cache-dir /app \
    && poetry config virtualenvs.in-project true \
    && poetry config installer.max-workers 10 \
    && poetry config --list \
    && poetry install

ENTRYPOINT ["poetry", "run", "python3", "webhook_server_container/app.py"]
