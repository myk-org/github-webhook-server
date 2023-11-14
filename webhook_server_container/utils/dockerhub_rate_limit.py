# From https://docs.docker.com/docker-hub/download-rate-limit/#how-can-i-check-my-current-rate

import requests


class DockerHub:
    def __init__(self, username, password):
        self.repository = "ratelimitpreview/test"
        self.token_url = (
            f"https://auth.docker.io/token?service=registry.docker.io&" f"scope=repository:{self.repository}:pull"
        )
        self.registry_url = f"https://registry-1.docker.io/v2/{self.repository}/manifests/latest"
        self.username = username
        self.password = password

    @staticmethod
    def limit_extractor(str_raw):
        if not str_raw:
            return 0

        if ";" in str_raw:
            split_arr = str_raw.split(";")  # TODO: return other values too?
            if len(split_arr) > 0:
                return split_arr[0]
        else:
            return str_raw

    def get_token(self):
        _kwargs = {"url": self.token_url}
        if self.username and self.password:
            _kwargs["auth"] = (self.username, self.password)

        r_token = requests.get(**_kwargs)
        r_token.raise_for_status()
        resp_token = r_token.json()
        token = resp_token.get("token")

        if not token:
            raise ValueError("Cannot obtain token from Docker Hub. Please try again!")

        return token

    def get_registry_limits(self):
        r_registry = requests.head(self.registry_url, headers={"Authorization": f"Bearer {self.get_token()}"})
        r_registry.raise_for_status()
        resp_headers = r_registry.headers

        return {
            "limit": self.limit_extractor(resp_headers.get("RateLimit-Limit")),
            "remaining": self.limit_extractor(resp_headers.get("RateLimit-Remaining")),
            "reset": self.limit_extractor(resp_headers.get("RateLimit-Reset")),
        }
