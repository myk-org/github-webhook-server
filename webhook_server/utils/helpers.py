from __future__ import annotations

import asyncio
import datetime
import json
import os
import random
import shlex
import subprocess
from concurrent.futures import Future, as_completed
from logging import Logger
from typing import Any

import github
from colorama import Fore
from github.RateLimitOverview import RateLimitOverview
from github.Repository import Repository
from simple_logger.logger import get_logger
from stringcolor import cs

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoApiTokenError


def get_logger_with_params(
    name: str,
    repository_name: str = "",
    mask_sensitive: bool = True,
) -> Logger:
    mask_sensitive_patterns: list[str] = [
        "container_repository_password",
        "-p",
        "password",
        "token",
        "apikey",
        "secret",
    ]

    _config = Config(repository=repository_name)

    log_level: str = _config.get_value(value="log-level", return_on_none="INFO")
    log_file: str = _config.get_value(value="log-file")

    if log_file and not log_file.startswith("/"):
        log_file_path = os.path.join(_config.data_dir, "logs")

        if not os.path.isdir(log_file_path):
            os.makedirs(log_file_path, exist_ok=True)

        log_file = os.path.join(log_file_path, log_file)

    return get_logger(
        name=name,
        filename=log_file,
        level=log_level,
        file_max_bytes=1024 * 1024 * 10,
        mask_sensitive=mask_sensitive,
        mask_sensitive_patterns=mask_sensitive_patterns,
    )


def extract_key_from_dict(key: Any, _dict: dict[Any, Any]) -> Any:
    if isinstance(_dict, dict):
        for _key, _val in _dict.items():
            if _key == key:
                yield _val
            if isinstance(_val, dict):
                for result in extract_key_from_dict(key, _val):
                    yield result
            elif isinstance(_val, list):
                for _item in _val:
                    for result in extract_key_from_dict(key, _item):
                        yield result


def get_github_repo_api(github_app_api: github.Github, repository: int | str) -> Repository:
    logger = get_logger_with_params(name="helpers")
    logger.debug(f"Get GitHub API for repository {repository}")

    return github_app_api.get_repo(repository)


async def run_command(
    command: str,
    log_prefix: str,
    verify_stderr: bool = False,
    **kwargs: Any,
) -> tuple[bool, Any, Any]:
    """
    Run command locally.

    Args:
        command (str): Command to run
        log_prefix (str): Prefix for log messages
        verify_stderr (bool, default False): Check command stderr

    Returns:
        tuple: True, out if command succeeded, False, err otherwise.
    """
    logger = get_logger_with_params(name="helpers")
    out_decoded: str = ""
    err_decoded: str = ""
    kwargs["stdout"] = subprocess.PIPE
    kwargs["stderr"] = subprocess.PIPE

    try:
        logger.debug(f"{log_prefix} Running '{command}' command")
        command_list = shlex.split(command)

        sub_process = await asyncio.create_subprocess_exec(
            *command_list,
            **kwargs,
        )

        stdout, stderr = await sub_process.communicate()
        out_decoded = stdout.decode(errors="ignore") if isinstance(stdout, bytes) else stdout
        err_decoded = stderr.decode(errors="ignore") if isinstance(stderr, bytes) else stderr

        error_msg = (
            f"{log_prefix} Failed to run '{command}'. "
            f"rc: {sub_process.returncode}, out: {out_decoded}, error: {err_decoded}"
        )

        if sub_process.returncode != 0:
            logger.error(error_msg)
            return False, out_decoded, err_decoded

        # From this point and onwards we are guaranteed that sub_process.returncode == 0
        if err_decoded and verify_stderr:
            logger.error(error_msg)
            return False, out_decoded, err_decoded

        return True, out_decoded, err_decoded

    except Exception as ex:
        logger.error(f"{log_prefix} Failed to run '{command}' command: {ex}")
        return False, out_decoded, err_decoded


def get_apis_and_tokes_from_config(config: Config) -> list[tuple[github.Github, str]]:
    apis_and_tokens: list[tuple[github.Github, str]] = []
    tokens = config.get_value(value="github-tokens")

    for _token in tokens:
        apis_and_tokens.append((github.Github(auth=github.Auth.Token(_token)), _token))

    return apis_and_tokens


def get_api_with_highest_rate_limit(config: Config, repository_name: str = "") -> tuple[github.Github, str, str]:
    """
    Get API with the highest rate limit

    Args:
        config (Config): Config object
        repository_name (str, optional): Repository name, if provided try to get token set in config repository section.

    Returns:
        tuple: API, token, api_user
    """
    logger = get_logger_with_params(name="helpers")

    api: github.Github | None = None
    token: str | None = None
    _api_user: str = ""
    rate_limit: RateLimitOverview | None = None

    remaining = 0

    msg = "Get API and tokens"

    if repository_name:
        msg += f" for repository {repository_name}"

    logger.debug(msg)

    apis_and_tokens = get_apis_and_tokes_from_config(config=config)

    for _api, _token in apis_and_tokens:
        if _api.rate_limiting[-1] == 60:
            logger.warning("API has rate limit set to 60 which indicates an invalid token, skipping")
            continue

        try:
            _api_user = _api.get_user().login
        except Exception as ex:
            logger.warning(f"Failed to get API user for API {_api}, skipping. {ex}")
            continue

        _rate_limit = _api.get_rate_limit()

        if _rate_limit.rate.remaining > remaining:
            remaining = _rate_limit.rate.remaining
            api, token, _api_user, rate_limit = _api, _token, _api_user, _rate_limit

    if rate_limit:
        log_rate_limit(rate_limit=rate_limit, api_user=_api_user)

    if not _api_user or not api or not token:
        raise NoApiTokenError("Failed to get API with highest rate limit")

    logger.info(f"API user {_api_user} selected with highest rate limit: {remaining}")
    return api, token, _api_user


def log_rate_limit(rate_limit: RateLimitOverview, api_user: str) -> None:
    logger = get_logger_with_params(name="helpers")

    rate_limit_str: str
    time_for_limit_reset: int = (rate_limit.rate.reset - datetime.datetime.now(tz=datetime.timezone.utc)).seconds
    below_minimum: bool = rate_limit.rate.remaining < 700

    if below_minimum:
        rate_limit_str = f"{Fore.RED}{rate_limit.rate.remaining}{Fore.RESET}"

    elif rate_limit.rate.remaining < 2000:
        rate_limit_str = f"{Fore.YELLOW}{rate_limit.rate.remaining}{Fore.RESET}"

    else:
        rate_limit_str = f"{Fore.GREEN}{rate_limit.rate.remaining}{Fore.RESET}"

    msg = (
        f"{Fore.CYAN}[{api_user}] API rate limit:{Fore.RESET} Current {rate_limit_str} of {rate_limit.rate.limit}. "
        f"Reset in {rate_limit.rate.reset} [{datetime.timedelta(seconds=time_for_limit_reset)}] "
        f"(UTC time is {datetime.datetime.now(tz=datetime.timezone.utc)})"
    )
    logger.debug(msg)
    if below_minimum:
        logger.warning(msg)


def get_future_results(futures: list["Future"]) -> None:
    """
    result must return tuple[bool, str, Callable] when the Callable is Logger function (LOGGER.info, LOGGER.error, etc)
    """
    for result in as_completed(futures):
        _res = result.result()
        _log = _res[2]
        if result.exception():
            _log(result.exception())

        if _res[0]:
            _log(_res[1])

        else:
            _log(_res[1])


def get_repository_color_for_log_prefix(repository_name: str, data_dir: str) -> str:
    """
    Get a consistent color for repository name in log prefixes.

    Args:
        repository_name: Repository name to get color for
        data_dir: Directory to store color mappings

    Returns:
        Colored repository name string
    """

    def _get_random_color(_colors: list[str], _json: dict[str, str]) -> str:
        color = random.choice(_colors)
        _json[repository_name] = color
        if _selected := cs(repository_name, color).render():
            return _selected
        return repository_name

    _all_colors: list[str] = []
    color_json: dict[str, str]
    _colors_to_exclude = ("blue", "white", "black", "grey")
    color_file: str = os.path.join(data_dir, "log-colors.json")

    for _color_name in cs.colors.values():
        _cname = _color_name["name"]
        if _cname.lower() in _colors_to_exclude:
            continue
        _all_colors.append(_cname)

    try:
        with open(color_file) as fd:
            color_json = json.load(fd)
    except Exception:
        color_json = {}

    if color := color_json.get(repository_name, ""):
        _cs_object = cs(repository_name, color)
        if cs.find_color(_cs_object):
            _str_color = _cs_object.render()
        else:
            _str_color = _get_random_color(_colors=_all_colors, _json=color_json)
    else:
        _str_color = _get_random_color(_colors=_all_colors, _json=color_json)

    with open(color_file, "w") as fd:
        json.dump(color_json, fd)

    if _str_color:
        _str_color = _str_color.replace("\x1b", "\033")
        return _str_color
    return repository_name


def prepare_log_prefix(
    event_type: str,
    delivery_id: str,
    repository_name: str | None = None,
    api_user: str | None = None,
    pr_number: int | None = None,
    data_dir: str | None = None,
) -> str:
    """
    Prepare standardized log prefix for consistent formatting across webhook processing.

    Args:
        event_type: GitHub event type (e.g., 'pull_request', 'check_run')
        delivery_id: GitHub delivery ID (x-github-delivery header)
        repository_name: Repository name for color coding (optional)
        api_user: API user for the request (optional)
        pr_number: Pull request number if applicable (optional)
        data_dir: Directory for storing color mappings (optional, defaults to /tmp)

    Returns:
        Formatted log prefix string
    """
    if repository_name and data_dir:
        repository_color = get_repository_color_for_log_prefix(repository_name, data_dir)
    else:
        repository_color = repository_name or ""

    # Build prefix components
    components = [event_type, delivery_id]
    if api_user:
        components.append(api_user)

    prefix = f"{repository_color} [{']['.join(components)}]"

    if pr_number:
        prefix += f"[PR {pr_number}]"

    return prefix + ":"
