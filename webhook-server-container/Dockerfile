FROM python:3.9
EXPOSE 5000
RUN apt-get update && apt-get install git hub
RUN pip install --upgrade pip
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY . /app
WORKDIR /app
ENTRYPOINT ["./entrypoint.sh"]
