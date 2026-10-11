"""Proxy policy for local model gateways and remote providers."""
from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


def uses_environment_proxy(base_url: str) -> bool:
    """Local endpoints connect directly; remote endpoints retain the user's proxy."""
    host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    if host == "localhost":
        return False
    try:
        return not ip_address(host).is_loopback
    except ValueError:
        return True
