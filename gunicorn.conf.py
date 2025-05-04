import os

bind = f"{os.environ.get('WEBHOOK_SERVER_IP_BIND', '0.0.0.0')}:{os.environ.get('WEBHOOK_SERVER_PORT', 5000)}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("MAX_WORKERS", 10))
on_starting = "webhook_server.app.on_starting"
accesslog = "-"
timeout = 60 * 30  # some operation can take long time.
