FROM python:3.9
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5151
ENTRYPOINT ["./entrypoint.sh"]
