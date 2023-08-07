from webhook_server_container.libs.sonar_qube import SonarQubeExt
from webhook_server_container.utils.constants import FLASK_APP
from webhook_server_container.utils.helpers import get_data_from_config


def set_sonar_qube_projects():
    config_data = get_data_from_config()
    sonarqube = config_data.get("sonarqube")
    if not sonarqube:
        return

    sonarqube_api = SonarQubeExt(**sonarqube)
    for repository_name, data in config_data["repositories"].items():
        sonarqube = data.get("sonarqube")
        if not sonarqube:
            continue

        project_key = data["name"].replace("/", "_")
        try:
            if not sonarqube_api.get_project(project_key=project_key):
                FLASK_APP.logger.info(
                    f"{repository_name}: Creating SonarQube project {project_key}"
                )
                sonarqube_api.create_project(
                    project_key=project_key, project_name=repository_name
                )
        except Exception as ex:
            FLASK_APP.logger.error(
                f"{repository_name}: Failed to create SonarQube project {project_key}: {ex}"
            )
