"""Isolated DNS, HTTP, TLS, and latency checks."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.models import CheckResult, CheckStatus, Website, utc_now

logger = logging.getLogger(__name__)

Resolver = Callable[[str, int], Any]
TLSProbe = Callable[[str, int, float], dict[str, Any]]
CheckOperation = Callable[[], Awaitable[CheckResult]]


async def _system_resolver(hostname: str, port: int) -> list[tuple[Any, ...]]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)


def _system_tls_probe(hostname: str, port: int, timeout_seconds: float) -> dict[str, Any]:
    context = ssl.create_default_context()
    with (
        socket.create_connection((hostname, port), timeout=timeout_seconds) as raw_socket,
        context.wrap_socket(raw_socket, server_hostname=hostname) as tls_socket,
    ):
        certificate = tls_socket.getpeercert()
        not_after = certificate.get("notAfter")
        if not_after is None:
            raise ssl.SSLError("peer certificate has no expiry date")
        expiry_epoch = ssl.cert_time_to_seconds(not_after)
        expiry = datetime.fromtimestamp(expiry_epoch, UTC)
        return {
            "expires_at": expiry.isoformat(),
            "issuer": certificate.get("issuer", ()),
            "subject": certificate.get("subject", ()),
        }


class CheckRunner:
    """Execute checks independently and normalize exceptions into check results."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        resolver: Resolver | None = None,
        tls_probe: TLSProbe | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.resolver = resolver or _system_resolver
        self.tls_probe = tls_probe or _system_tls_probe
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_checks)

    async def run_all(self, website: Website) -> list[CheckResult]:
        """Run independent network checks concurrently; one exception cannot cancel siblings."""
        checks = await asyncio.gather(
            self._safe_check("dns", lambda: self.check_dns(website.url, website.timeout_seconds)),
            self._safe_check("http", lambda: self.check_http(website.url, website.timeout_seconds)),
            self._safe_check("tls", lambda: self.check_tls(website.url, website.timeout_seconds)),
        )
        dns_result, http_result, tls_result = checks
        latency_result = self.check_latency(http_result)
        return [dns_result, http_result, tls_result, latency_result]

    async def _safe_check(self, check_name: str, operation: CheckOperation) -> CheckResult:
        try:
            async with self._semaphore:
                return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Check isolation is a deliberate service boundary.
            logger.exception("Unexpected %s check error", check_name)
            return CheckResult(
                check_name=check_name,
                status=CheckStatus.UNKNOWN,
                timestamp=utc_now(),
                message=f"{check_name.upper()} check could not produce a result",
                error=f"{type(exc).__name__}: {exc}",
            )

    async def check_dns(self, url: str, timeout_seconds: float) -> CheckResult:
        started = time.monotonic()
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            try:
                address = ipaddress.ip_address(hostname)
                addresses = [address.compressed]
            except ValueError:
                answers = await asyncio.wait_for(
                    self.resolver(hostname, port), timeout=timeout_seconds
                )
                addresses = sorted(
                    {str(answer[4][0]) for answer in answers if len(answer) > 4 and answer[4]}
                )
            duration_ms = (time.monotonic() - started) * 1000
            if not addresses:
                raise socket.gaierror("resolver returned no addresses")
            return CheckResult(
                check_name="dns",
                status=CheckStatus.PASS,
                timestamp=utc_now(),
                duration_ms=duration_ms,
                value={"host": hostname, "addresses": addresses},
                message=f"Resolved {hostname} to {', '.join(addresses)}",
            )
        except TimeoutError as exc:
            return self._failed_result("dns", started, "DNS lookup timed out", exc)
        except (OSError, UnicodeError) as exc:
            return self._failed_result("dns", started, "DNS resolution failed", exc)

    async def check_http(self, url: str, timeout_seconds: float) -> CheckResult:
        started = time.monotonic()
        try:
            response = await self.http_client.get(url, timeout=timeout_seconds)
            duration_ms = (time.monotonic() - started) * 1000
            status = CheckStatus.PASS if 200 <= response.status_code < 400 else CheckStatus.FAIL
            message = f"HTTP request returned {response.status_code}"
            return CheckResult(
                check_name="http",
                status=status,
                timestamp=utc_now(),
                duration_ms=duration_ms,
                value={
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "redirect_count": len(response.history),
                },
                message=message,
                error=None if status is CheckStatus.PASS else message,
            )
        except httpx.TimeoutException as exc:
            return self._failed_result("http", started, "HTTP request timed out", exc)
        except httpx.RequestError as exc:
            return self._failed_result("http", started, "HTTP connection failed", exc)
        except Exception as exc:
            logger.exception("Unexpected HTTP check error")
            return CheckResult(
                check_name="http",
                status=CheckStatus.UNKNOWN,
                timestamp=utc_now(),
                duration_ms=(time.monotonic() - started) * 1000,
                message="HTTP check could not produce a result",
                error=f"{type(exc).__name__}: {exc}",
            )

    async def check_tls(self, url: str, timeout_seconds: float) -> CheckResult:
        parts = urlsplit(url)
        started = time.monotonic()
        if parts.scheme != "https":
            return CheckResult(
                check_name="tls",
                status=CheckStatus.UNKNOWN,
                timestamp=utc_now(),
                duration_ms=0.0,
                value={"not_applicable": True},
                message="TLS is not applicable to an HTTP URL",
            )
        hostname = parts.hostname or ""
        port = parts.port or 443
        try:
            certificate = await asyncio.wait_for(
                asyncio.to_thread(self.tls_probe, hostname, port, timeout_seconds),
                timeout=timeout_seconds + 0.25,
            )
            expires_at = datetime.fromisoformat(str(certificate["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            remaining_days = (expires_at.astimezone(UTC) - utc_now()).total_seconds() / 86400
            if remaining_days <= 0:
                return self._failed_result(
                    "tls",
                    started,
                    "TLS certificate is expired",
                    ssl.SSLCertVerificationError("certificate expired"),
                )
            status = (
                CheckStatus.WARN
                if remaining_days <= self.settings.tls_warning_days
                else CheckStatus.PASS
            )
            return CheckResult(
                check_name="tls",
                status=status,
                timestamp=utc_now(),
                duration_ms=(time.monotonic() - started) * 1000,
                value={
                    "expires_at": expires_at.astimezone(UTC).isoformat(),
                    "days_remaining": round(remaining_days, 2),
                    "issuer": certificate.get("issuer"),
                    "subject": certificate.get("subject"),
                },
                message=(
                    f"TLS certificate expires in {remaining_days:.1f} days"
                    if status is CheckStatus.WARN
                    else "TLS certificate is valid"
                ),
                error=(
                    f"TLS certificate expires within {self.settings.tls_warning_days} days"
                    if status is CheckStatus.WARN
                    else None
                ),
            )
        except TimeoutError as exc:
            return self._failed_result("tls", started, "TLS handshake timed out", exc)
        except (ssl.SSLError, OSError, ValueError, KeyError) as exc:
            return self._failed_result("tls", started, "TLS validation failed", exc)
        except Exception as exc:
            logger.exception("Unexpected TLS check error")
            return CheckResult(
                check_name="tls",
                status=CheckStatus.UNKNOWN,
                timestamp=utc_now(),
                duration_ms=(time.monotonic() - started) * 1000,
                message="TLS check could not produce a result",
                error=f"{type(exc).__name__}: {exc}",
            )

    def check_latency(self, http_result: CheckResult) -> CheckResult:
        if http_result.duration_ms is None:
            return CheckResult(
                check_name="latency",
                status=CheckStatus.UNKNOWN,
                timestamp=utc_now(),
                message="Response time is unavailable because the HTTP check has no duration",
                error=http_result.error,
            )
        duration_ms = http_result.duration_ms
        if duration_ms < self.settings.latency_warning_ms:
            status = CheckStatus.PASS
            message = f"Response time is {duration_ms:.1f} ms"
        elif duration_ms <= self.settings.latency_failure_ms:
            status = CheckStatus.WARN
            message = f"Response time is elevated at {duration_ms:.1f} ms"
        else:
            status = CheckStatus.FAIL
            message = f"Response time is too high at {duration_ms:.1f} ms"
        return CheckResult(
            check_name="latency",
            status=status,
            timestamp=utc_now(),
            duration_ms=duration_ms,
            value={"duration_ms": round(duration_ms, 2)},
            message=message,
            error=message if status is CheckStatus.FAIL else None,
        )

    @staticmethod
    def _failed_result(
        check_name: str, started: float, message: str, exception: BaseException
    ) -> CheckResult:
        detail = f"{type(exception).__name__}: {exception}".strip()
        return CheckResult(
            check_name=check_name,
            status=CheckStatus.FAIL,
            timestamp=utc_now(),
            duration_ms=(time.monotonic() - started) * 1000,
            message=message,
            error=f"{message}: {detail}" if detail else message,
        )
