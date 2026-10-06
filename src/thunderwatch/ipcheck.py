"""Public IP lookup with independent provider confirmation."""

from __future__ import annotations

import http.client
import ipaddress
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from . import __version__
from .updater import HTTPSRedirectHandler, installed_version

IPV4_PROVIDERS = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://ipv4.icanhazip.com",
)
IPV6_PROVIDERS = ("https://api6.ipify.org", "https://ipv6.icanhazip.com")


@dataclass(frozen=True)
class LookupResult:
    family: str
    value: str | None
    providers: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def fetch(url: str, version: str | None = None) -> str:
    if version is None:
        version = installed_version() or __version__
    request = urllib.request.Request(url, headers={"User-Agent": f"ThunderWatch/{version}"})
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    with opener.open(request, timeout=10) as response:
        body = cast(bytes, response.read(65))
    if len(body) > 64:
        raise ValueError("response exceeds 64 bytes")
    return body.decode("ascii").strip()


def normalize(text: str, family: str) -> str:
    address = ipaddress.ip_address(text.strip())
    if family == "ipv4":
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_global:
            raise ValueError("not a global IPv4 address")
        return str(address)
    if family != "ipv6":
        raise ValueError(f"unknown address family: {family}")
    if (
        not isinstance(address, ipaddress.IPv6Address)
        or address.ipv4_mapped
        or not address.is_global
    ):
        raise ValueError("not a global IPv6 address")
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


def lookup(
    family: str,
    fetcher: Callable[[str], str] = fetch,
    providers: tuple[str, ...] | None = None,
    baseline: str | None = None,
) -> LookupResult:
    """Try providers in order; changes require two providers agreeing on one value.

    A lone valid response is accepted only if it matches the known baseline. This
    permits an unchanged check with one healthy provider but never a one-source change.
    """
    urls = providers or (IPV4_PROVIDERS if family == "ipv4" else IPV6_PROVIDERS)
    valid: list[tuple[str, str]] = []
    errors: list[str] = []
    for url in urls:
        try:
            value = normalize(fetcher(url), family)
            if not valid and value == baseline:
                return LookupResult(family, value, (url,))
            valid.append((url, value))
        except (
            OSError,
            ValueError,
            UnicodeError,
            urllib.error.URLError,
            http.client.HTTPException,
        ) as exc:
            errors.append(str(exc) or type(exc).__name__)
    counts: dict[str, list[str]] = {}
    for url, value in valid:
        counts.setdefault(value, []).append(url)
    confirmed = next(
        (
            (value, urls_for_value)
            for value, urls_for_value in counts.items()
            if len(urls_for_value) >= 2
        ),
        None,
    )
    if confirmed:
        return LookupResult(family, confirmed[0], tuple(confirmed[1][:2]))
    if len(counts) == 1:
        value, urls_for_value = next(iter(counts.items()))
        if value == baseline:
            return LookupResult(family, value, (urls_for_value[0],))
        return LookupResult(
            family, None, (urls_for_value[0],), "a second provider did not confirm the candidate"
        )
    error = "providers disagree" if counts else "; ".join(errors) or "no provider answered"
    return LookupResult(family, None, tuple(url for url, _ in valid), error)
