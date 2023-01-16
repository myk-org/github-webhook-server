FROM ubuntu:20.04
EXPOSE 5000

RUN set -x \
    && apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y git hub \
    && apt-get install -y --no-install-recommends python3.8 python3.9 python3.10 python3.11 python3-pip\
    && apt-get purge -y --auto-remove software-properties-common \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN python3 -m pip install pip --upgrade && python3 -m pip install -r requirements.txt
COPY . /app
WORKDIR /app
ENTRYPOINT ["./entrypoint.sh"]
