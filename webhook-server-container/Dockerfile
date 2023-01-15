FROM python:3.9
EXPOSE 5000
RUN apt-get update && apt-get install git hub
COPY requirements.txt requirements.txt
RUN python3 -m pip install pip --upgrade && python3 -m pip install -r requirements.txt
COPY . /app
WORKDIR /app
ENTRYPOINT ["./entrypoint.sh"]
