from webhook_server.libs.config import Config

_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)

bind = f"{_ip_bind}:{_port}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(_max_workers)
on_starting = "webhook_server.app.on_starting"
accesslog = "-"
timeout = 60 * 30  # some operation can take long time.
