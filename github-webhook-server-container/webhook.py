import os

import yaml
from github import Github
from selenium import webdriver
from urllib3.exceptions import MaxRetryError


def _get_firefox_driver():
    try:
        firefox_options = webdriver.FirefoxOptions()
        firefox_options.headless = True
        return webdriver.Remote("http://firefox:4444", options=firefox_options)
    except (ConnectionRefusedError, MaxRetryError):
        return _get_firefox_driver()


def create_webhook():
    with open("/config.yaml") as fd:
        repos = yaml.safe_load(fd)

    for repo, data in repos["repositories"].items():
        ngrok_url = None
        webhook_ip = os.environ.get("GITHUB_WEBHOOK_IP")
        github_repository = data["name"]
        github_token = data["token"]
        events = data["events"]

        if webhook_ip == "ngrok":
            driver = _get_firefox_driver()
            driver.get("http://ngrok:4040/status")
            ngrok_url = driver.find_element(
                "xpath",
                '//*[@id="app"]/div/div/div/div[1]/div[1]/ul/li[1]/div/table/tbody/tr[1]/td',
            ).text
            driver.close()
            config = {"url": f"{ngrok_url}/github_webhook", "content_type": "json"}
            gapi = Github(login_or_token=github_token)
            repo = gapi.get_repo(github_repository)
            for _hook in repo.get_hooks():
                if "ngrok.io" in _hook.config["url"]:
                    print(f"Deleting existing webhook: {_hook.config['url']}")
                    _hook.delete()

        print(
            f"Creating webhook: {ngrok_url or webhook_ip}/github_webhook for "
            f"{github_repository} "
            f"with events: {events}"
        )
        repo.create_hook("web", config, events, active=True)


if __name__ == "__main__":
    create_webhook()
