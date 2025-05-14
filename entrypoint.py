import asyncio

import uvicorn

from webhook_server.libs.config import Config
from webhook_server.utils.github_repository_and_webhook_settings import repository_and_webhook_settings

_config = Config()
_root_config = _config.root_data
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")

# LOGGING_CONFIG = {
#     "version": 1,
#     "disable_existing_loggers": True,
#     "formatters": {
#         "standard": {
#             "()": "colorlog.ColoredFormatter",
#             "format": "%(asctime)s %(name)s %(log_color)s%(levelname)s%(reset)s %(message)s",
#             "log_colors": {
#                 "DEBUG": "cyan",
#                 "INFO": "green",
#                 "WARNING": "yellow",
#                 "SUCCESS": "bold_green",
#                 "ERROR": "red",
#                 "CRITICAL": "red,bg_white",
#                 "HASH": "bold_yellow",
#             },
#         },
#     },
#     "handlers": {
#         "default": {
#             "formatter": "standard",
#             "class": "logging.StreamHandler",
#             "stream": "ext://sys.stdout",  # Default is stderr
#         },
#         "file_handler": {
#             "formatter": "standard",
#             "class": "logging.handlers.RotatingFileHandler",
#             "filename": _config.get_value("log-file", return_on_none="webhook-server.log"),
#             "maxBytes": 1024 * 1024 * 5,
#             "backupCount": 10,
#         },
#     },
#     "loggers": {
#         "main": {
#             "handlers": ["default", "file_handler"],
#             "level": _config.get_value("log-level", return_on_none="INFO"),
#             "propagate": False,
#         },
#         "GithubWebhook": {
#             "handlers": ["default", "file_handler"],
#             "level": _config.get_value("log-level", return_on_none="INFO"),
#             "propagate": False,
#         },
#         "repository-and-webhook-settings": {
#             "handlers": ["default", "file_handler"],
#             "level": _config.get_value("log-level", return_on_none="INFO"),
#             "propagate": False,
#         },
#         "github-repository-settings": {
#             "handlers": ["default", "file_handler"],
#             "level": _config.get_value("log-level", return_on_none="INFO"),
#             "propagate": False,
#         },
#         "helpers": {
#             "handlers": ["default", "file_handler"],
#             "level": _config.get_value("log-level", return_on_none="INFO"),
#             "propagate": False,
#         },
#         "uvicorn": {
#             "handlers": ["default", "file_handler"],
#             "level": "INFO",
#             "propagate": False,
#         },
#         "uvicorn.access": {"handlers": ["default", "file_handler"], "level": "INFO", "propagate": False},
#         "uvicorn.error": {"handlers": ["default", "file_handler"], "level": "ERROR", "propagate": False},
#         "uvicorn.asgi": {"handlers": ["default", "file_handler"], "level": "INFO", "propagate": False},
#     },
# }

if __name__ == "__main__":
    result = asyncio.run(repository_and_webhook_settings(webhook_secret=_webhook_secret))
    uvicorn.run(
        "webhook_server.app:FASTAPI_APP",
        host=_ip_bind,
        port=int(_port),
        workers=int(_max_workers),
        reload=False,
        # log_config=LOGGING_CONFIG,
    )
