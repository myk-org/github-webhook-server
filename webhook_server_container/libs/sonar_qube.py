import os

from sonarqube import SonarQubeClient

from webhook_server_container.utils.helpers import run_command


class SonarQubeExt(SonarQubeClient):
    def __init__(self, url, token):
        super().__init__(sonarqube_url=url, token=token)
        self.token = token

    def get_project(self, project_key):
        return self.projects.get_project(project_key)

    def create_project(self, project_key, project_name):
        self.projects.request(
            method="POST",
            path="api/projects/create",
            params={"name": project_name, "project": project_key},
        )

    def run_sonar_scanner(self, project_key, log_prefix):
        cmd = self.get_sonar_scanner_command(project_key=project_key)
        return run_command(command=cmd, log_prefix=log_prefix)[0]

    def get_project_quality_status(self, project_key):
        return self.qualitygates.request(
            path="api/qualitygates/project_status",
            params={"projectKey": project_key},
        ).json()

    def get_sonar_scanner_command(self, project_key):
        _cli = os.path.join(
            os.environ.get("SONAR_SCANNER_CLI_DIR", "/sonar-scanner-cli"),
            "bin",
            "sonar-scanner",
        )
        return (
            f"{_cli} -Dsonar.projectKey={project_key} "
            f"-Dsonar.sources=. "
            f"-Dsonar.host.url={self.base_url} "
            f"-Dsonar.token={self.token}"
        )
