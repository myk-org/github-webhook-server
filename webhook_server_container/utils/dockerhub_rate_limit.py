# From https://docs.docker.com/docker-hub/download-rate-limit/#how-can-i-check-my-current-rate
from typing import Any, Dict

import requests
from requests import Response


class DockerHub:
    def __init__(self, username: str, password: str):
        self.repository: str = "ratelimitpreview/test"
        self.token_url: str = (
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{self.repository}:pull"
        )
        self.registry_url: str = f"https://registry-1.docker.io/v2/{self.repository}/manifests/latest"
        self.username = username
        self.password = password

    @staticmethod
    def limit_extractor(str_raw: str) -> int:
        if not str_raw:
            return 0

        if ";" in str_raw:
            split_arr = str_raw.split(";")  # TODO: return other values too?
            if len(split_arr) > 0:
                return int(split_arr[0])
        else:
            return int(str_raw)

    def get_token(self) -> str:
        _kwargs: Dict[str, Any] = {"url": self.token_url}
        if self.username and self.password:
            _kwargs["auth"] = (self.username, self.password)

        r_token: Response = requests.get(**_kwargs)
        r_token.raise_for_status()
        resp_token: Dict[Any, Any] = r_token.json()
        token: str = resp_token.get("token")

        if not token:
            raise ValueError("Cannot obtain token from Docker Hub. Please try again!")

        return token

    def get_registry_limits(self) -> Dict[str, int]:
        r_registry = requests.head(self.registry_url, headers={"Authorization": f"Bearer {self.get_token()}"})
        r_registry.raise_for_status()
        resp_headers = r_registry.headers

        return {
            "limit": self.limit_extractor(resp_headers.get("RateLimit-Limit")),
            "remaining": self.limit_extractor(resp_headers.get("RateLimit-Remaining")),
            "reset": self.limit_extractor(resp_headers.get("RateLimit-Reset")),
        }
