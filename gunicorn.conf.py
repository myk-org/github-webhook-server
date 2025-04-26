import os

bind = "0.0.0.0:5000"
worker_class = "uvicorn.workers.UvicornWorker"
workers = os.environ.get("UVICORN_WORKERS", 10)
on_starting = "webhook_server.app.on_starting"
accesslog = "-"
