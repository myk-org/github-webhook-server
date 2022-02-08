import os

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
    github_owner = os.environ["GITHUB_OWNER"]
    github_repository = os.environ["GITHUB_REPOSITORY"]
    driver = _get_firefox_driver()
    driver.get("http://ngrok:4040/status")
    ngrok_url = driver.find_element(
        "xpath",
        '//*[@id="app"]/div/div/div/div[1]/div[1]/ul/li[1]/div/table/tbody/tr[1]/td',
    ).text
    driver.close()
    print(f"Creating webhook: {ngrok_url}/github_webhook")
    config = {"url": f"{ngrok_url}/github_webhook", "content_type": "json"}
    gapi = Github(login_or_token=os.getenv("GITHUB_TOKEN"))
    repo = gapi.get_repo(f"{github_owner}/{github_repository}")
    for _hook in repo.get_hooks():
        if "ngrok.io" in _hook.config["url"]:
            _hook.delete()

    repo.create_hook(
        "web", config, ["push", "pull_request", "issue_comment"], active=True
    )


if __name__ == "__main__":
    create_webhook()
