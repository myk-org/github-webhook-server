"""Utility functions for the FastAPI application."""

import datetime
import hashlib
import hmac
import ipaddress

import httpx
from fastapi import HTTPException, Request, status

from webhook_server.utils.helpers import get_logger_with_params

# Constants
HTTP_TIMEOUT_SECONDS: float = 10.0
GITHUB_META_URL: str = "https://api.github.com/meta"
CLOUDFLARE_IPS_URL: str = "https://api.cloudflare.com/client/v4/ips"

# Logger for utilities
LOGGER = get_logger_with_params(name="app_utils")


async def get_github_allowlist(http_client: httpx.AsyncClient) -> list[str]:
    """Fetch and cache GitHub IP allowlist asynchronously."""
    try:
        response = await http_client.get(GITHUB_META_URL)
        response.raise_for_status()  # Check for HTTP errors
        data = response.json()
        return data.get("hooks", [])

    except httpx.RequestError as e:
        LOGGER.error(f"Error fetching GitHub allowlist: {e}")
        raise

    except Exception as e:
        LOGGER.error(f"Unexpected error fetching GitHub allowlist: {e}")
        raise


async def get_cloudflare_allowlist(http_client: httpx.AsyncClient) -> list[str]:
    """Fetch and cache Cloudflare IP allowlist asynchronously."""
    try:
        response = await http_client.get(CLOUDFLARE_IPS_URL)
        response.raise_for_status()
        result = response.json().get("result", {})
        return result.get("ipv4_cidrs", []) + result.get("ipv6_cidrs", [])

    except httpx.RequestError as e:
        LOGGER.error(f"Error fetching Cloudflare allowlist: {e}")
        raise

    except Exception as e:
        LOGGER.error(f"Unexpected error fetching Cloudflare allowlist: {e}")
        raise


def verify_signature(payload_body: bytes, secret_token: str, signature_header: str | None = None) -> None:
    """Verify that the payload was sent from GitHub by validating SHA256.

    Raise and return 403 if not authorized.

    Args:
        payload_body: original request body to verify (request.body())
        secret_token: GitHub app webhook token (WEBHOOK_SECRET)
        signature_header: header received from GitHub (x-hub-signature-256)
    """
    if not signature_header:
        raise HTTPException(status_code=403, detail="x-hub-signature-256 header is missing!")

    hash_object = hmac.new(secret_token.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=403, detail="Request signatures didn't match!")


async def gate_by_allowlist_ips(request: Request, allowed_ips: tuple[ipaddress._BaseNetwork, ...]) -> None:
    """Gate access by IP allowlist."""
    if allowed_ips:
        if not request.client or not request.client.host:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not determine client IP address")

        try:
            src_ip = ipaddress.ip_address(request.client.host)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not parse client IP address")

        for valid_ip_range in allowed_ips:
            if src_ip in valid_ip_range:
                return
        else:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{src_ip} IP is not a valid ip in allowlist IPs",
            )


def parse_datetime_string(datetime_str: str | None, field_name: str) -> datetime.datetime | None:
    """Parse datetime string to datetime object or raise HTTPException.

    Args:
        datetime_str: The datetime string to parse (can be None)
        field_name: Name of the field for error messages

    Returns:
        Parsed datetime object or None if input is None

    Raises:
        HTTPException: If datetime string is invalid
    """
    if not datetime_str:
        return None

    try:
        return datetime.datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format: {datetime_str}. Expected ISO 8601 format. Error: {str(e)}",
        )
