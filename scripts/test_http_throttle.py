"""
Regresión: trato del 429 en las webs externas.

Sin esto el pipeline dispara cientos de peticiones seguidas a FutbolFantasy
(páginas de equipo + previas + perfiles, por cada liga) y acaba baneado a mitad
de ciclo, dejando el JSON con datos parciales.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scrapers import http_util  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, *, headers: dict | None = None, text: str = "ok"):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"ok": True}


class _FakeSession:
    """Devuelve la cola de respuestas programada y anota cada URL pedida."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.calls.append(url)
        if self._responses:
            return self._responses.pop(0)
        return _FakeResponse(200)


def _install(responses: list[_FakeResponse]) -> _FakeSession:
    session = _FakeSession(responses)
    http_util.reset_rate_limits()
    http_util._session = session  # noqa: SLF001
    return session


def _restore() -> None:
    http_util.reset_rate_limits()
    http_util._session = None  # noqa: SLF001


FF = "https://www.futbolfantasy.com/laliga/equipos/real-sociedad"


def test_429_se_reintenta_y_acaba_sirviendo():
    session = _install([_FakeResponse(429), _FakeResponse(200, text="<html/>")])
    http_util.RATE_LIMIT_BACKOFF_S = 0.01
    try:
        out = http_util.get_text(FF)
        assert out == "<html/>", out
        assert len(session.calls) == 2, session.calls
    finally:
        _restore()


def test_429_repetido_apaga_el_host_el_resto_del_ciclo():
    session = _install([_FakeResponse(429) for _ in range(5)])
    http_util.RATE_LIMIT_BACKOFF_S = 0.01
    try:
        assert http_util.get_text(FF) is None
        peticiones_tras_el_corte = len(session.calls)
        assert http_util.host_is_rate_limited("www.futbolfantasy.com")
        # Las siguientes páginas no deben ni salir a la red
        for slug in ("barcelona", "getafe", "elche"):
            assert http_util.get_text(f"https://www.futbolfantasy.com/laliga/equipos/{slug}") is None
        assert len(session.calls) == peticiones_tras_el_corte, session.calls
    finally:
        _restore()


def test_el_corte_no_afecta_a_otros_hosts():
    _install([_FakeResponse(429) for _ in range(3)] + [_FakeResponse(200, text="jp")])
    http_util.RATE_LIMIT_BACKOFF_S = 0.01
    try:
        http_util.get_text(FF)
        assert http_util.host_is_rate_limited("www.futbolfantasy.com")
        otro = http_util.get_text("https://www.jornadaperfecta.com/laliga/lesionados/")
        assert otro == "jp", otro
    finally:
        _restore()


def test_respeta_retry_after_sin_esperas_absurdas():
    _install([_FakeResponse(429, headers={"Retry-After": "9999"}), _FakeResponse(200)])
    tope = http_util.RATE_LIMIT_MAX_WAIT_S
    http_util.RATE_LIMIT_MAX_WAIT_S = 0.2
    try:
        t0 = time.monotonic()
        assert http_util.get_text(FF) is not None
        assert time.monotonic() - t0 < 2.0, "un Retry-After enorme no puede colgar el ciclo"
    finally:
        http_util.RATE_LIMIT_MAX_WAIT_S = tope
        _restore()


def test_peticiones_seguidas_al_mismo_host_van_espaciadas():
    _install([_FakeResponse(200) for _ in range(3)])
    try:
        t0 = time.monotonic()
        for slug in ("barcelona", "getafe", "elche"):
            http_util.get_text(f"https://www.futbolfantasy.com/laliga/equipos/{slug}")
        elapsed = time.monotonic() - t0
        gap = http_util.HOST_MIN_GAP_S["www.futbolfantasy.com"]
        assert elapsed >= gap * 2 * 0.8, f"{elapsed:.2f}s para 3 peticiones (gap={gap})"
    finally:
        _restore()


def test_el_ciclo_deja_constancia_de_los_429():
    _install([_FakeResponse(429) for _ in range(5)])
    http_util.RATE_LIMIT_BACKOFF_S = 0.01
    try:
        http_util.get_text(FF)
        report = http_util.rate_limit_report()
        assert report.get("www.futbolfantasy.com", 0) >= 1, report
    finally:
        _restore()


def test_una_respuesta_buena_borra_la_racha():
    _install([_FakeResponse(429), _FakeResponse(200), _FakeResponse(429), _FakeResponse(200)])
    http_util.RATE_LIMIT_BACKOFF_S = 0.01
    try:
        assert http_util.get_text(FF) is not None
        assert http_util.get_text(FF) is not None
        assert not http_util.host_is_rate_limited("www.futbolfantasy.com")
    finally:
        _restore()


def main() -> None:
    tests = [
        test_429_se_reintenta_y_acaba_sirviendo,
        test_429_repetido_apaga_el_host_el_resto_del_ciclo,
        test_el_corte_no_afecta_a_otros_hosts,
        test_respeta_retry_after_sin_esperas_absurdas,
        test_peticiones_seguidas_al_mismo_host_van_espaciadas,
        test_el_ciclo_deja_constancia_de_los_429,
        test_una_respuesta_buena_borra_la_racha,
    ]
    backoff = http_util.RATE_LIMIT_BACKOFF_S
    failed = 0
    for fn in tests:
        http_util.RATE_LIMIT_BACKOFF_S = backoff
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERR  {fn.__name__}: {exc}")
    http_util.RATE_LIMIT_BACKOFF_S = backoff
    if failed:
        raise SystemExit(f"{failed} test(s) failed")
    print(f"All {len(tests)} tests passed")


if __name__ == "__main__":
    main()
