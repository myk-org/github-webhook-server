import pytest
from webhook_server.utils.constants import SIZE_LABEL_PREFIX


class Label:
    def __init__(self, name: str):
        self.name = name


class PullRequest:
    def __init__(self, additions: int, deletions: int, labels: list[str] | None = None):
        self.additions = additions
        self.deletions = deletions
        self.labels = labels or []

    @property
    def lables(self) -> list[Label]:
        return [Label(label) for label in self.labels]


@pytest.mark.parametrize(
    "additions, deletions, expected_label",
    [
        (0, 0, "XS"),
        (18, 1, "XS"),
        (48, 1, "S"),
        (98, 1, "M"),
        (298, 1, "L"),
        (498, 1, "XL"),
        (1000, 1, "XXL"),
    ],
)
def test_get_size_thresholds(process_github_webhook, additions, deletions, expected_label):
    process_github_webhook.pull_request = PullRequest(additions=additions, deletions=deletions)
    result = process_github_webhook.get_size()

    assert result == f"{SIZE_LABEL_PREFIX}{expected_label}"
