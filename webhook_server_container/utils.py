import os
import shlex
import subprocess
from functools import wraps
from time import sleep

import yaml
from constants import FLASK_APP
from github.GithubException import RateLimitExceededException, UnknownObjectException


def get_github_repo_api(gapi, repository):
    try:
        repo = gapi.get_repo(repository)
    except (UnknownObjectException, RateLimitExceededException) as ex:
        if ex == UnknownObjectException:
            FLASK_APP.logger.error(
                f"Repository {repository}: Not found or token invalid"
            )
        else:
            FLASK_APP.logger.error(f"Repository {repository}: Rate limit exceeded")
        return
    return repo


def get_repository_from_config():
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)
    return repos


def extract_key_from_dict(key, _dict):
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


def ignore_exceptions(logger=None, retry=None):
    def wrapper(func):
        @wraps(func)
        def inner(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                if retry:
                    for _ in range(0, retry):
                        try:
                            return func(*args, **kwargs)
                        except Exception:
                            sleep(1)

                if logger:
                    logger.info(ex)
                return None

        return inner

    return wrapper


def run_command(
    command,
    verify_stderr=True,
    shell=False,
    timeout=None,
    capture_output=True,
    check=True,
    **kwargs,
):
    """
    Run command locally.

    Args:
        command (str): Command to run
        verify_stderr (bool, default True): Check command stderr
        shell (bool, default False): run subprocess with shell toggle
        timeout (int, optional): Command wait timeout
        capture_output (bool, default False): Capture command output
        check (boot, default True):  If check is True and the exit code was non-zero, it raises a
            CalledProcessError

    Returns:
        tuple: True, out if command succeeded, False, err otherwise.
    """
    FLASK_APP.logger.info(f"Running '{command}' command")
    sub_process = subprocess.run(
        shlex.split(command),
        capture_output=capture_output,
        check=check,
        shell=shell,
        text=True,
        timeout=timeout,
        **kwargs,
    )
    out_decoded = sub_process.stdout
    err_decoded = sub_process.stderr

    error_msg = f"Failed to run '{command}'. rc: {sub_process.returncode}, out: {out_decoded}, error: {err_decoded}"
    if sub_process.returncode != 0:
        FLASK_APP.logger.error(error_msg)
        return False, out_decoded, err_decoded

    # From this point and onwards we are guaranteed that sub_process.returncode == 0
    if err_decoded and verify_stderr:
        FLASK_APP.logger.error(error_msg)
        return False, out_decoded, err_decoded

    return True, out_decoded, err_decoded
