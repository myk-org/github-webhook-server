# github-webhook-server
Webhook server to manage GitHub repositories.

# Setup:
See [example.config.yaml](https://github.com/myakove/github-webhook-server/blob/main/example.config.yaml) and [docker-compose-example.yaml](https://github.com/myakove/github-webhook-server/blob/main/docker-compose-example.yaml) for sample config files.

# Supported actions:

Following actions are done automatically:

- Add reviewers from OWNER file
- Set PR size label.
- New issue is created for the PR.
- Issues get closed when PR is merged/closed.

## Supported user actions via adding comment:
- `/verified` - to verify a PR
- `/verified cancel` - to undo verify
- `/cherry-pick <target_branch_name>` - cherry-pick a merged PR against a target branch
- `/tox` - run tox
- `/build-container` - run build-container
- `/build-and-push-container` - build and push container image (tag will be the PR number).
- `/python-module-install` - run python-module-install command
- `/<label name>` - add a label to a PR
- `/<label name> cancel` - remove label

### Note:
- verified label removed on each new commit push.
- Cherry-picking is supported only on merged PRs

# Issues:
- New issues can be created for this project [here](https://github.com/myakove/github-webhook-server/issues)
