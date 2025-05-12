from webhook_server.libs.config import Config
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings

_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")

repository_and_webhook_settings(webhook_secret=_webhook_secret)

print(f"uv run uvicorn webhook_server.app:FASTAPI_APP --host {_ip_bind} --port {_port} --workers {_max_workers}")
