# github-webhook-server

Webhook server to manage GitHub repositories.
On start, it will configure the following for each repository:

* Enable CodeQL with default setting
* Set branch protection based on config.yaml

**Private repositories are not supported.**

## Build container

Using podman:

```bash
podman build --format docker -t github-webhook-server .
```

Using docker:

```bash
docker build -t github-webhook-server .
```

## Setup

[example.config.yaml](https://github.com/myakove/github-webhook-server/blob/main/example.config.yaml)  
[docker-compose-example.yaml](https://github.com/myakove/github-webhook-server/blob/main/docker-compose-example.yaml)

## Getting started

### Config file

Minimum config to get started

```yaml
github-app-id: 123456
webhook_ip: https://IP or FQDN

repositories:
  my-repository-name:
    name: my-org/my-repository-name
    token: Github token
    protected-branches:
      main: []
```

* `github-app-id`: The ID of the GitHub app. Need to add the APP to the repository.
* `name`: repository full name (org or user/repository name)
* `webhook_ip`: Ip or FQDN where this app will run, this will be added as webhook in the repository setting
* `token`: Admin user token for the repository

```yaml
slack_webhook_url: https://hooks.slack.com/services/<channel>
```

* If `slack_webhook_url` configured for the repository a slack massages will be sent to the configured channel
about new releases to pypi, new containers that was pushed
* `slack_webhook_url`: Slack webhook URL to Slack channel

```yaml
pypi:
  token: pypi-token
  tool: twine
```

if `pypi` configured for the repository a new version will be pushed to pypi on new GitHub release

* `token`: pypi token with push permissions
* `tool`: The tool to use to build the package, can be `twine` or `poetry`

```yaml
tox: all
```

if `tox` configured for the repository a tox job will run on each push and new commits

* `tox`: The tox tests to run, can be set of tests seperated by `,` or `all` to run all tests defined in tox.ini

```yaml
default-status-checks:
  - "WIP"
  - "dpulls"
  - "Inclusive Language",
  - "SonarCloud Code Analysis"
  - "can-be-merged"
```

Top level array which define the defaults required check runs for all the repositories

```yaml
protected-branches:
  main: []
```

This tool configure branch protection and set required to be run for each branch to pass before the PR can be merged

* `protected-branches`: array of branches to set protection
* `branch name`: List of required to be run to set for the branch, when empty set `default-status-checks` as required
* `include-runs`: Only include those runs as required
* `exclude-runs`: Exclude those runs from the `default-status-checks`

if the repository have the file `.pre-commit-config.yaml` then `pre-commit.ci - pr` will be added, can be excluded by
set it in `exclude-runs`

```yaml
verified_job: false
```

By default, we create a `verified_job` run, for each PR the owner needs to comment `/verified` to mark the PR as verified
In order to not add this job set `verified_job` to `false`

```yaml
container:
  username: username
  password: password
  repository: repository path
  tag: latest
```

if `container` is configures for the repository we create `build-container` run that will build the container on each
PR push/commit
Once the PR is merged, the container will be build and push to the repository

* `username`: User with push permissions to the repository
* `password`: The password for the username
* `repository`: the repository to push the container, for example `quay.io/myakove/github-webhook-server`
* `tag`: The container tag to use when pushing the container

```yaml
docker:
  username: username
  password: password
```

If `docker` is configures for the repository we log in to docker.io to increase pull rate limit

## Supported actions

Following actions are done automatically:

* Add reviewers from OWNER file
* Set PR size label.
* New issue is created for the PR.
* Issues get closed when PR is merged/closed.

### Supported user actions via adding comment

* `/verified`: to verify a PR
* `/verified cancel`: to undo verify
* `/cherry-pick <target_branch_name>`: cherry-pick a merged PR against a target branch
  * Multiple target branches are allowed, separated by spaces
  * If the current PR is nor merged label will be added and once the PR is merged it will be cherry-picked
* `/retest tox`: run tox
* `/retest build-container`: run build-container
* `/retest python-module-install`: run python-module-install command
* `/build-and-push-container`: build and push container image (tag will be the PR number).

### Supported user labels

Usage:

* `/<label name>`: add a label to a PR
* `/<label name> cancel`: remove label

Supported labels:

* hold
* verified
* wip\
* lgtm
* approve

### Note

* verified label removed on each new commit push.
* Cherry-picking is supported only on merged PRs

### Issues

* New issues can be created for this project [here](https://github.com/myakove/github-webhook-server/issues)

### Development

To run locally you need to export some os environment variables

```bash
poetry install

WEBHOOK_SERVER_DATA_DIR=/tmp/webhook_server_data
SONAR_SCANNER_URL=https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-5.0.0.2966-linux.zip

mkdir -p $WEBHOOK_SERVER_DATA_DIR
cp -f webhook-server.private-key.pem $WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem
cp -f config.yaml $WEBHOOK_SERVER_DATA_DIR/config.yaml
export WEBHOOK_SERVER_PORT=5003
curl $SONAR_SCANNER_URL --output /tmp/sonar-scanner-cli.zip \
    && unzip -o /tmp/sonar-scanner-cli.zip -d /tmp/sonar-scanner-cli \
    && rm -rf /tmp/sonar-scanner-cli.zip

export FLASK_DEBUG=1
export WEBHOOK_SERVER_DATA_DIR=$WEBHOOK_SERVER_DATA_DIR
export SONAR_SCANNER_CLI_DIR=/tmp/sonar-scansner-cli
poetry run python webhook_server_container/app.py
```
