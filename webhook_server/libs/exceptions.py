class RepositoryNotFoundInConfigError(Exception):
    pass


class ProcessGithubWebhookError(Exception):
    def __init__(self, err: dict[str, str]):
        self.err = err
        super().__init__(str(err))


class NoApiTokenError(Exception):
    pass
