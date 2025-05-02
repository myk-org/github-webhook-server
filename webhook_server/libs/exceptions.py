class NoPullRequestError(Exception):
    pass


class RepositoryNotFoundError(Exception):
    pass


class ProcessGithubWebhookError(Exception):
    def __init__(self, err: dict[str, str]):
        self.err = err
        super().__init__(str(err))
