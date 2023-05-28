import urllib3
from flask import Flask, Response, request
from github_api import GitHubApi
from github_repository_settings import set_repositories_settings
from webhook import create_webhook


urllib3.disable_warnings()

app = Flask("webhook_server")


@app.route("/webhook_server", methods=["POST"])
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


@app.route("/webhook_server/tox/<string:filename>")
def return_tox(filename):
    app.logger.info("app.route: Processing tox file")
    with open(f"/webhook_server/tox/{filename}") as fd:
        return Response(fd.read(), mimetype="text/plain")


@app.route("/webhook_server/build-container/<string:filename>")
def return_build_container(filename):
    app.logger.info("app.route: Processing build-container file")
    with open(f"/webhook_server/build-container/{filename}") as fd:
        return Response(fd.read(), mimetype="text/plain")


def main():
    procs = create_webhook(app=app) + set_repositories_settings(app=app)
    for proc in procs:
        proc.join()

    app.logger.info("Starting webhook-server app")
    app.run(port=5000, host="0.0.0.0", use_reloader=False)


if __name__ == "__main__":
    main()
