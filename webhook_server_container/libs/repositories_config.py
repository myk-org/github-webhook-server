import os

from github import Github

from webhook_server_container.libs.logs import Logs
from webhook_server_container.libs.sonar_qube import SonarQubeExt
from webhook_server_container.utils.dockerhub_rate_limit import DockerHub
from webhook_server_container.utils.helpers import (
    check_rate_limit,
    get_data_from_config,
    get_github_repo_api,
)


class RepositoryNotFoundError(Exception):
    pass


class RepositoriesConfig:
    def __init__(
        self, hook_data, github_event, repositories_app_api, missing_app_repositories
    ):
        self.github_event = github_event
        self.hook_data = hook_data
        self.repository_name = self.hook_data["repository"]["name"]
        self.repositories_app_api = repositories_app_api
        self.missing_app_repositories = missing_app_repositories
        self.container_repo_dir = "/tmp/repository"
        self.webhook_server_data_dir = os.environ.get(
            "WEBHOOK_SERVER_DATA_DIR", "/webhook_server"
        )

        # filled by self._repo_data_from_config()
        self.dockerhub_username = None
        self.dockerhub_password = None
        self.container_repository_username = None
        self.container_repository_password = None
        self.container_repository = None
        self.dockerfile = None
        self.container_tag = None
        self.container_build_args = None
        self.container_command_args = None
        self.token = None
        self.repository_full_name = None
        self.api_user = None
        self.github_app_id = None
        self.sonarqube_api = None
        self.sonarqube_project_key = None
        # End of filled by self._repo_data_from_config()
        self._repo_data_from_config()

        self.github_app_api = self.get_github_app_api()
        self.github_api = Github(login_or_token=self.token)
        self.repository_by_github_app = get_github_repo_api(
            github_api=self.github_app_api, repository=self.repository_full_name
        )
        self.repository = get_github_repo_api(
            github_api=self.github_api, repository=self.repository_full_name
        )

        self.clone_repository_path = os.path.join("/", self.repository.name)

        self.dockerhub = DockerHub(
            username=self.dockerhub_username,
            password=self.dockerhub_password,
        )

        self.api_user = self._api_username

        log = Logs(repository_name=self.repository_name, token=self.token)
        self.logger = log.logger
        self.log_prefix = log.log_prefix

        self.logger.info(f"{self.log_prefix} Check rate limit")
        check_rate_limit()

    def _repo_data_from_config(self):
        config_data = get_data_from_config()
        self.github_app_id = config_data["github-app-id"]
        self.token = config_data["github-token"]
        self.webhook_url = config_data.get("webhook_ip")
        sonarqube = config_data.get("sonarqube")
        if sonarqube:
            self.sonarqube_url = sonarqube["url"]
            self.sonarqube_api = SonarQubeExt(**sonarqube)

        repo_data = config_data["repositories"].get(self.repository_name)
        if not repo_data:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_name} not found in config file"
            )

        self.repository_full_name = repo_data["name"]
        self.pypi = repo_data.get("pypi")
        self.verified_job = repo_data.get("verified_job", True)
        self.tox_enabled = repo_data.get("tox")
        self.slack_webhook_url = repo_data.get("slack_webhook_url")
        self.build_and_push_container = repo_data.get("container")
        self.dockerhub = repo_data.get("docker")
        if sonarqube:
            self.sonarqube_project_key = self.repository_full_name.replace("/", "_")

        if self.dockerhub:
            self.dockerhub_username = self.dockerhub["username"]
            self.dockerhub_password = self.dockerhub["password"]

        if self.build_and_push_container:
            self.container_repository_username = self.build_and_push_container[
                "username"
            ]
            self.container_repository_password = self.build_and_push_container[
                "password"
            ]
            self.container_repository = self.build_and_push_container["repository"]
            self.dockerfile = self.build_and_push_container.get(
                "dockerfile", "Dockerfile"
            )
            self.container_tag = self.build_and_push_container.get("tag", "latest")
            self.container_build_args = self.build_and_push_container.get("build-args")
            self.container_command_args = self.build_and_push_container.get("args")

    @property
    def _api_username(self):
        return self.github_api.get_user().login

    def get_github_app_api(self):
        if self.repository_full_name in self.missing_app_repositories:
            raise RepositoryNotFoundError(
                f"Repository {self.repository_full_name} not found by manage-repositories-app, "
                f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
            )
        return self.repositories_app_api[self.repository_full_name]
