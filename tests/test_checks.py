from __future__ import annotations

import asyncio
import socket
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.checks import CheckRunner, _system_tls_probe
from app.models import CheckResult, CheckStatus, Website, utc_now


def make_website(url: str = "https://example.test/") -> Website:
    now = utc_now()
    return Website(
        id=1,
        name="Example",
        url=url,
        enabled=True,
        interval_seconds=60,
        timeout_seconds=3,
        created_at=now,
        updated_at=now,
    )


def make_runner(settings, handler, *, resolver=None, tls_probe=None) -> CheckRunner:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    return CheckRunner(settings, client, resolver=resolver, tls_probe=tls_probe)


async def pass_resolver(host: str, port: int):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.8", port))]


def valid_tls_probe(host: str, port: int, timeout: float):
    return {"expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(), "issuer": "test CA"}


@pytest.mark.parametrize("status_code", [200, 302, 399])
async def test_http_2xx_and_3xx_pass(status_code: int, settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    runner = make_runner(settings, handler)
    result = await runner.check_http("https://example.test/", 2)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.PASS
    assert result.value["status_code"] == status_code
    assert result.value["final_url"] == "https://example.test/"


@pytest.mark.parametrize("status_code", [400, 503])
async def test_http_4xx_and_5xx_fail(status_code: int, settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    runner = make_runner(settings, handler)
    result = await runner.check_http("https://example.test/", 2)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.FAIL
    assert result.error == f"HTTP request returned {status_code}"


async def test_http_redirects_record_final_url_and_count(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(
                302, headers={"Location": "https://final.example.test/ok"}, request=request
            )
        return httpx.Response(204, request=request)

    runner = make_runner(settings, handler)
    result = await runner.check_http("https://example.test/", 2)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.PASS
    assert result.value["final_url"] == "https://final.example.test/ok"
    assert result.value["redirect_count"] == 1


async def test_http_timeout_is_a_normalized_failure(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timeout", request=request)

    runner = make_runner(settings, handler)
    result = await runner.check_http("https://example.test/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.FAIL
    assert result.message == "HTTP request timed out"
    assert "ReadTimeout" in (result.error or "")


async def test_unexpected_http_error_is_visible_as_unknown(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("transport implementation defect")

    runner = make_runner(settings, handler)
    result = await runner.check_http("https://example.test/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.UNKNOWN
    assert result.message == "HTTP check could not produce a result"
    assert "RuntimeError" in (result.error or "")


async def test_dns_success_records_addresses_and_duration(settings) -> None:
    async def resolver(host: str, port: int):
        assert host == "example.test"
        assert port == 443
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.4", port)),
        ]

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), resolver=resolver
    )
    result = await runner.check_dns("https://example.test/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.PASS
    assert result.value["addresses"] == ["192.0.2.4", "192.0.2.8"]
    assert result.duration_ms is not None


async def test_dns_failure_is_normalized(settings) -> None:
    async def resolver(host: str, port: int):
        raise socket.gaierror("name not known")

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), resolver=resolver
    )
    result = await runner.check_dns("https://missing.example.test/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.FAIL
    assert result.message == "DNS resolution failed"
    assert "gaierror" in (result.error or "")


async def test_dns_timeout_and_empty_answer_are_failures(settings) -> None:
    async def slow_resolver(host: str, port: int):
        await asyncio.Future()

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), resolver=slow_resolver
    )
    timed_out = await runner.check_dns("https://slow.example.test/", 0.001)

    async def empty_resolver(host: str, port: int):
        return []

    runner.resolver = empty_resolver
    empty_answer = await runner.check_dns("https://empty.example.test/", 1)
    await runner.http_client.aclose()
    assert timed_out.status is CheckStatus.FAIL
    assert timed_out.message == "DNS lookup timed out"
    assert empty_answer.status is CheckStatus.FAIL
    assert empty_answer.message == "DNS resolution failed"


async def test_ip_literal_is_reported_without_resolver(settings) -> None:
    def should_not_run(host: str, port: int):
        raise AssertionError("IP literal should not be resolved")

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), resolver=should_not_run
    )
    result = await runner.check_dns("http://192.0.2.3/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.PASS
    assert result.value["addresses"] == ["192.0.2.3"]


def test_system_tls_probe_uses_hostname_validation_and_records_expiry(monkeypatch) -> None:
    class FakeRawSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeTLSSocket(FakeRawSocket):
        def getpeercert(self):
            return {
                "notAfter": "Jan 01 00:00:00 2030 GMT",
                "issuer": ((("commonName", "Test CA"),),),
                "subject": ((("commonName", "example.test"),),),
            }

    class FakeContext:
        def wrap_socket(self, raw_socket, *, server_hostname):
            assert server_hostname == "example.test"
            return FakeTLSSocket()

    def fake_connection(address, *, timeout):
        assert address == ("example.test", 443)
        assert timeout == 4
        return FakeRawSocket()

    monkeypatch.setattr("app.checks.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("app.checks.socket.create_connection", fake_connection)
    certificate = _system_tls_probe("example.test", 443, 4)
    assert certificate["expires_at"].startswith("2030-01-01T00:00:00")


async def test_tls_valid_and_expiring_certificates(settings) -> None:
    def probe_days(days: int):
        def probe(host: str, port: int, timeout: float):
            return {"expires_at": (datetime.now(UTC) + timedelta(days=days)).isoformat()}

        return probe

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), tls_probe=probe_days(60)
    )
    valid = await runner.check_tls("https://example.test/", 1)
    runner.tls_probe = probe_days(7)
    warning = await runner.check_tls("https://example.test/", 1)
    await runner.http_client.aclose()
    assert valid.status is CheckStatus.PASS
    assert warning.status is CheckStatus.WARN
    assert warning.value["days_remaining"] <= 30


async def test_tls_expired_and_invalid_certificates_fail(settings) -> None:
    def expired(host: str, port: int, timeout: float):
        return {"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}

    runner = make_runner(
        settings, lambda request: httpx.Response(200, request=request), tls_probe=expired
    )
    expired_result = await runner.check_tls("https://example.test/", 1)
    runner.tls_probe = lambda host, port, timeout: (_ for _ in ()).throw(
        ssl.SSLCertVerificationError("hostname mismatch")
    )
    mismatch_result = await runner.check_tls("https://example.test/", 1)
    await runner.http_client.aclose()
    assert expired_result.status is CheckStatus.FAIL
    assert "expired" in expired_result.message.lower()
    assert mismatch_result.status is CheckStatus.FAIL
    assert "TLS validation failed" in mismatch_result.message


async def test_tls_is_not_applicable_to_http(settings) -> None:
    runner = make_runner(settings, lambda request: httpx.Response(200, request=request))
    result = await runner.check_tls("http://example.test/", 1)
    await runner.http_client.aclose()
    assert result.status is CheckStatus.UNKNOWN
    assert result.value == {"not_applicable": True}


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (999.9, CheckStatus.PASS),
        (1000, CheckStatus.WARN),
        (3000, CheckStatus.WARN),
        (3000.1, CheckStatus.FAIL),
    ],
)
def test_latency_threshold_boundaries(duration: float, expected: CheckStatus, settings) -> None:
    runner = CheckRunner(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    http_result = CheckResult("http", CheckStatus.PASS, utc_now(), duration_ms=duration)
    assert runner.check_latency(http_result).status is expected
    asyncio.run(runner.http_client.aclose())


def test_latency_unknown_without_http_duration(settings) -> None:
    runner = CheckRunner(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    result = runner.check_latency(CheckResult("http", CheckStatus.UNKNOWN, utc_now()))
    assert result.status is CheckStatus.UNKNOWN
    assert "unavailable" in result.message
    asyncio.run(runner.http_client.aclose())


async def test_one_check_failure_does_not_prevent_other_checks(settings) -> None:
    async def failing_resolver(host: str, port: int):
        raise socket.gaierror("temporary lookup failure")

    runner = make_runner(
        settings,
        lambda request: httpx.Response(200, request=request),
        resolver=failing_resolver,
        tls_probe=valid_tls_probe,
    )
    results = await runner.run_all(make_website())
    await runner.http_client.aclose()
    by_name = {result.check_name: result for result in results}
    assert by_name["dns"].status is CheckStatus.FAIL
    assert by_name["http"].status is CheckStatus.PASS
    assert by_name["tls"].status is CheckStatus.PASS
    assert by_name["latency"].status in {CheckStatus.PASS, CheckStatus.WARN, CheckStatus.FAIL}


async def test_unexpected_dns_exception_is_logged_and_isolated(settings, caplog) -> None:
    async def broken_resolver(host: str, port: int):
        raise RuntimeError("resolver adapter bug")

    runner = make_runner(
        settings,
        lambda request: httpx.Response(200, request=request),
        resolver=broken_resolver,
        tls_probe=valid_tls_probe,
    )
    results = await runner.run_all(make_website())
    await runner.http_client.aclose()
    by_name = {result.check_name: result for result in results}
    assert by_name["dns"].status is CheckStatus.UNKNOWN
    assert by_name["http"].status is CheckStatus.PASS
    assert by_name["tls"].status is CheckStatus.PASS
    assert "Unexpected dns check error" in caplog.text
