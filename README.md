# github-webhook-server

A [FastAPI-based](https://fastapi.tiangolo.com) webhook server for managing GitHub pull requests workflow. and manage repositories.

## Overview

The tool will manage the following:

###### Repositories

- Configure repositories setting
- Configure branch protection
- Set itself as webhook for the repository
- Add missing lables to the repository

###### Pull requests

- Add reviewers from OWNER file
- Manage pull requests labels
- Check when the pull request is ready to be merged
- Build container from Dockerfile when pull request is merged
- Build container from Dockerfile when new release is pushed
- Push new release to PyPI when new release is pushed
- Open an issue for each pull request
- Add pull request size label

###### Available user actions

- Mark pull request as WIP by comment /wip to the pull request, To remove it from the pull request comment /wip cancel to the pull request.
- Block merging of pull request by comment /hold, To un-block merging of pull request comment /hold cancel.
- Mark pull request as verified by comment /verified to the pull request, to un-verify comment /verified cancel to the pull request.
  - verified label removed on each new commit push.
- Cherry pick a merged pull request comment /cherry-pick <target branch to cherry-pick to> in the pull request.
  - Multiple target branches can be cherry-picked, separated by spaces. (/cherry-pick branch1 branch2)
  - Cherry-pick will be started when pull request is merged
- Build and push container image command /build-and-push-container in the pull request (tag will be the pull request number).
  - You can add extra args to the Podman build command
    - Example: /build-and-push-container --build-arg OPENSHIFT_PYTHON_WRAPPER_COMMIT=<commit_hash>
- Add a label by comment use /<label name>, to remove, use /<label name> cancel
- Assign reviewers based on OWNERS file use /assign-reviewers
- Check if pull request can be merged use /check-can-merge

Pre-build container images available in:

- quay.io/myakove/github-webhook-server

## Build container

Webhook server to manage GitHub repositories.
On start, it will configure the following for each repository:

- Set branch protection based on config.yaml

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

## Setup

Before running the application, ensure to set the following environment variables and configuration file:

- `WEBHOOK_SERVER_DATA_DIR`: Path to the data directory where the `config.yaml` file is located.
- `config.yaml`: Configuration file that contains settings for the server and repositories, which should be placed in the `WEBHOOK_SERVER_DATA_DIR` directory.

Follow the instructions to build the container using either podman or docker as described in the Build container section. Once that is done, proceed with the configurations outlined below.

### Config file

Minimum config to get started

```yaml
github-app-id: 123456
webhook_ip: https://IP or FQDN
github-tokens:
  - <GITHIB TOKEN1>

repositories:
  my-repository-name:
    name: my-org/my-repository-name
    protected-branches:
      main: []
```

Server configuration and security:

```yaml
ip-bond: "0.0.0.0" # IP address to bind the server to
port: 5000 # Port to bind the server to
max-workers: 10 # Maximum number of workers to run
webhook-secret: "<SECRET>" # Secret to verify hook is a valid hook from Github
verify-github-ips: True # Verify hook request is from GitHub IPs
```

Repository config can be override by config file in the root on the repository named `.github-webhook-server.yaml`

```yaml
minimum-lgtm: 1
can-be-merged-required-labels:
  - "my-label"
tox:
  main: all
set-auto-merge-prs:
  - dev
pre-commit: true
```

````

- `github-app-id`: The ID of the GitHub app. Need to add the APP to the repository.
- `name`: repository full name (org or user/repository name)
- `webhook_ip`: Ip or FQDN where this app will run, this will be added as webhook in the repository setting
- `github-tokens`: List of admin users token for the repositories

- If `slack_webhook_url` configured for the repository a slack massages will be sent to the configured channel
  about new releases to pypi, new containers that was pushed

```yaml
slack_webhook_url: https://hooks.slack.com/services/<channel>
````

If `pypi` configured for the repository a new version will be pushed to pypi on new GitHub release

- `token`: pypi token with push permissions
- `tool`: The tool to use to build the package, can be `twine` or `poetry`

```yaml
pypi:
  token: pypi-token
  tool: twine
```

if `tox` configured for the repository a tox job will run on each push and new commits

- `tox`: The tox tests to run, can be set of tests separated by `,` or `all` to run all tests defined in tox.ini

```yaml
tox: all
tox_python_version: python3.11 # if passed run on specified python version else run on default
```

Top level array which define the defaults required check runs for all the repositories

```yaml
default-status-checks:
  - "WIP"
  - "dpulls"
  - "SonarCloud Code Analysis"
  - "can-be-merged"
```

```yaml
protected-branches:
  main: []
```

This tool configure branch protection and set required to be run for each branch to pass before the pull request can be merged

if the repository have the file `.pre-commit-config.yaml` then `pre-commit.ci - pr` will be added, can be excluded by
set it in `exclude-runs`

- `protected-branches`: array of branches to set protection
- `branch name`: List of required to be run to set for the branch, when empty set `default-status-checks` as required
- `include-runs`: Only include those runs as required
- `exclude-runs`: Exclude those runs from the `default-status-checks`

By default, we create a `verified_job` run, for each pull request the owner needs to comment `/verified` to mark the pull request as verified
In order to not add this job set `verified_job` to `false`

```yaml
verified_job: false
```

if `container` is configured for the repository we create `build-container` run that will build the container on each
pull request push/commit
Once the pull request is merged, the container will be build and push to the repository
if `release` is set to `true` a new container will be pushed with the release version as the tag
if the merged pull request is in any other branch than `main` or `master` the tag will be set to `branch name`, otherwise `tag` will be used

- `username`: User with push permissions to the repository
- `password`: The password for the username
- `repository`: the repository to push the container, for example `quay.io/myakove/github-webhook-server`
- `tag`: The container tag to use when pushing the container
- `release`: if `true` a new container will be pushed with the release version as the tag

```yaml
container:
  username: username
  password: password
  repository: repository path
  tag: latest
  release: true
```

If `docker` is configured for the repository we log in to docker.io to increase pull rate limit

```yaml
docker:
  username: username
  password: password
```

## Supported actions

Following actions are done automatically:

- Add reviewers from [OWNERS](OWNERS) file, support add different reviewers based on files/folders.
- Set pull request size label.
- New issue is created for the pull request.
- Issues get closed when pull request is merged/closed.

## OWNERS file example

Root approvers YAML:

```yaml
approvers:
  - root-approver1
  - root-approver2
reviewers:
  - root-reviewer1
  - root_reviewer2
```

Under folder inside the repositpory (for example `libs`):

```yaml
root-approvers: False # Not required repository root approvers for this folder
approvers:
  - lib-approver1
  - lib-approver2
reviewers:
  - lib-reviewer1
  - lib-reviewer2
```

### Supported user actions via adding comment

- `/verified`: to verify a pull request
- `/verified cancel`: to undo verify
- `/cherry-pick <target_branch_name>`: cherry-pick a merged pull request against a target branch
  - Multiple target branches are allowed, separated by spaces
  - If the current pull request is nor merged label will be added and once the pull request is merged it will be cherry-picked
- `/retest tox`: run tox
- `/retest build-container`: run build-container
- `/retest python-module-install`: run python-module-install command
- `/retest all`: run all tests
- `/build-and-push-container`: build and push container image (tag will be the pull request number)
  - You can add extra args to the Podman build command
    - Example: `/build-and-push-container --build-arg OPENSHIFT_PYTHON_WRAPPER_COMMIT=<commit_hash>`
- `/assign-reviewers`: assign reviewers based on OWNERS file

### Supported user labels

Usage:

- `/<label name>`: add a label to a pull request
- `/<label name> cancel`: remove label

Supported labels:

- hold
- verified
- wip
- lgtm
- approve

### Note

- verified label removed on each new commit push.
- Cherry-picking is supported only on merged pull requests

### Issues

- New issues can be created for this project [here](https://github.com/myakove/github-webhook-server/issues)

## Main Functionalities

### Logging Setup

The webhook server configures custom logging with color-coded log level names to enhance readability. It also supports optional logging to a file.
See [example-config](example.config.yaml) for more details.

### Webhook Creation

Webhooks are automatically created for GitHub repositories based on settings defined in `webhook.py`. These webhooks enable real-time integration with GitHub events such as push, pull requests, and more.

### Usage Guide

To use the webhook server, first prepare the [config.yaml](example.config.yaml) file with the necessary repository and server configurations. Set the required environment variables, including `WEBHOOK_SERVER_DATA_DIR`. Build and start the server using the instructions in the 'Build container' section.
