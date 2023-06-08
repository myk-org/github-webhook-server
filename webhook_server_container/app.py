import urllib3
from flask import Flask, Response, request
from github_api import GitHubApi
from github_repository_settings import set_repositories_settings
from webhook import create_webhook


urllib3.disable_warnings()

app = Flask("webhook-server")
PLAIN_TEXT_MIME_TYPE = "text/plain"
APP_ROOT_PATH = "/webhook_server"
FILENAME_STRING = "<string:filename>"


@app.route(APP_ROOT_PATH, methods=["POST"])
def process_webhook():
    try:
        hook_data = request.json
        github_event = request.headers.get("X-GitHub-Event")
        api = GitHubApi(app=app, hook_data=hook_data)

        app.logger.info(
            f"{api.repository_full_name} Event type: {github_event} "
            f"event ID: {request.headers.get('X-GitHub-Delivery')}"
        )
        api.process_hook(data=github_event)
        return "process success"
    except Exception as ex:
        app.logger.error(f"Error: {ex}")
        return "Process failed"


@app.route(f"{APP_ROOT_PATH}/tox/{FILENAME_STRING}")
def return_tox(filename):
    app.logger.info("app.route: Processing tox file")
    with open(f"{APP_ROOT_PATH}/tox/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@app.route(f"{APP_ROOT_PATH}/build-container/{FILENAME_STRING}")
def return_build_container(filename):
    app.logger.info("app.route: Processing build-container file")
    with open(f"{APP_ROOT_PATH}/build-container/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


@app.route(f"{APP_ROOT_PATH}/python-module-install/{FILENAME_STRING}")
def return_python_module_install(filename):
    app.logger.info("app.route: Processing python-module-install file")
    with open(f"{APP_ROOT_PATH}/python-module-install/{filename}") as fd:
        return Response(fd.read(), mimetype=PLAIN_TEXT_MIME_TYPE)


def main():
    procs = create_webhook(app=app) + set_repositories_settings(app=app)
    for proc in procs:
        proc.join()

    app.logger.info(f"Starting {app.name} app")
    app.run(port=5000, host="0.0.0.0", use_reloader=False)


if __name__ == "__main__":
    main()
