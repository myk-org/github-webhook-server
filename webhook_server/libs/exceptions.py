class NoPullRequestError(Exception):
    pass


class RepositoryNotFoundError(Exception):
    pass


class ProcessGithubWehookError(Exception):
    def __init__(self, err: dict[str, str]):
        self.err = err

    def __str__(self) -> str:
        return f"{self.err}"
