"""
Cliente de escritura de Mister Fantasy.

El resto del proyecto solo lee. Este módulo es el único que manda operaciones
que cambian la partida, así que todas pasan por `_post_action`, que centraliza
el interruptor de seguridad, el modo simulación y el registro.

Endpoints (confirmados en el JS y las plantillas de Mister):

    ajax/bid         pujar a un libre / ofertar a un rival / actualizar / retirar
    ajax/sale        listar en el mercado o retirar el listado
    ajax/sell        vender al sistema al valor de mercado (sale del equipo ya)
    ajax/clause-set  subir la cláusula de un jugador propio
    ajax/clause-pay  pagar la cláusula de rescisión
                     (views/ajax/clause-pay.twig: id_player, id_uc, id_giphy)

Endpoints que existen (aparecen sus `callback_*` en el JS) pero cuyo payload
exacto no está confirmado. `scripts/probe_offer_endpoints.py` los lista y el
ejecutor los deja apagados hasta confirmarlos:

    ajax/offer       aceptar (type=accept, id_bid) o rechazar (type=decline, id_bid)
    ajax/resale      renovar el listado (solo id_market; Mister rechaza las ofertas)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import config
from mister_client import ajax_post

log = logging.getLogger("mister_actions")

# Acciones del formulario de puja (`name="action"` en form#form-bid)
BID_PLACE = "bid"
BID_UPDATE = "update"
BID_REMOVE = "remove"

# Acciones del formulario de venta (`name="action"` en el popup de sale)
SALE_LIST = "sale"
SALE_REMOVE = "remove"

# Acciones sobre una oferta recibida
OFFER_ACCEPT = "accept"
OFFER_DECLINE = "decline"


def _offer_marked_decline(record: dict[str, Any]) -> bool:
    """True si Mister guardó la oferta como rechazada pese a un status exterior ok."""
    resp = record.get("response")
    if not isinstance(resp, dict):
        return False
    data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    if not isinstance(data, dict):
        return False
    return str(data.get("status") or "") == "decline"

# Rutas cuyo payload no está confirmado en la caché del repo. Se pueden simular,
# pero un POST real necesita `verified_only=False` explícito: si el formato es
# otro, Mister podría interpretar la petición de una forma que no queremos.
UNVERIFIED_PATHS = frozenset({"/ajax/offer", "/ajax/resale"})


class ActionError(RuntimeError):
    """Mister rechazó la operación o respondió algo que no podemos dar por buena."""

    def __init__(self, message: str, *, record: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.record = record


class UnverifiedAction(ActionError):
    """La ruta existe pero su contrato no está confirmado; no se manda en vivo."""


def _error_response(exc: BaseException) -> tuple[int | None, Any]:
    """Cuerpo que devolvió Mister cuando el POST no llegó a un JSON de éxito."""
    resp = getattr(exc, "response", None)
    if resp is None and exc.__cause__ is not None:
        resp = getattr(exc.__cause__, "response", None)
    if resp is None:
        return None, None
    code = getattr(resp, "status_code", None)
    text = getattr(resp, "text", None)
    if not isinstance(text, str) or not text.strip():
        return code if isinstance(code, int) else None, None
    head = text.lstrip()[:20].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return code if isinstance(code, int) else None, {"html": True}
    try:
        body = json.loads(text)
    except ValueError:
        body = text[:500]
    return code if isinstance(code, int) else None, body


class MisterWriteClient:
    """
    Envoltorio de escritura con interruptor propio.

    `transport` se inyecta para poder probar el ejecutor completo sin tocar la
    cuenta real. Por defecto usa el POST AJAX del cliente de lectura, que ya
    resuelve la renovación del `x-auth` tras cambiar de comunidad.
    """

    def __init__(
        self,
        *,
        dry_run: bool = True,
        transport: Callable[[str, dict[str, Any]], Any] | None = None,
        verified_only: bool = True,
    ) -> None:
        self.dry_run = bool(dry_run)
        self.verified_only = bool(verified_only)
        self._injected = transport is not None
        self._transport = transport or (lambda path, data: ajax_post(path, data))
        self.sent: list[dict[str, Any]] = []

    # -- núcleo -------------------------------------------------------------

    def _post_action(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = {k: v for k, v in data.items() if v is not None}
        unverified = path in UNVERIFIED_PATHS
        record = {"path": path, "data": dict(payload), "dry_run": self.dry_run}
        if unverified:
            record["unverified"] = True

        if self.dry_run:
            log.info("[dry-run] %s %s", path, payload)
            record["status"] = "dry_run"
            self.sent.append(record)
            return record

        if unverified and self.verified_only:
            record["status"] = "blocked_unverified"
            self.sent.append(record)
            raise UnverifiedAction(
                f"{path} tiene contrato sin confirmar; sondéalo con "
                "scripts/probe_offer_endpoints.py antes de habilitarlo",
                record=record,
            )

        # Con transporte inyectado no hay cuenta real detrás: es lo que usan los
        # tests para recorrer la rama de envío sin credenciales.
        if not self._injected and not (config.MISTER_TOKEN or config.MISTER_COOKIE):
            raise ActionError("sin credenciales Mister: no se puede escribir")

        log.info("POST %s %s", path, payload)
        try:
            resp = self._transport(path, payload)
        except Exception as exc:  # noqa: BLE001
            record["status"] = "error"
            record["error"] = str(exc)
            http_status, body = _error_response(exc)
            if http_status:
                record["http_status"] = http_status
            if body is not None:
                record["response"] = body
            self.sent.append(record)
            raise ActionError(f"{path} falló: {exc}", record=record) from exc

        status = ""
        if isinstance(resp, dict):
            status = str(resp.get("status") or "")
            record["response"] = resp
        elif resp not in (None, ""):
            record["response"] = resp
        record["status"] = status or "unknown"
        self.sent.append(record)

        if status and status != "ok":
            raise ActionError(f"{path} respondió status={status!r}", record=record)
        return record

    # -- pujas y ofertas ----------------------------------------------------

    def place_bid(
        self,
        *,
        player_id: str | int,
        amount: float,
        id_market: str | int | None = None,
        offeree_id: str | int = 0,
        action: str = BID_PLACE,
    ) -> dict[str, Any]:
        """
        Puja a un libre del mercado u oferta a un rival.

        `offeree_id` > 0 convierte la puja en una oferta: el dueño decide, y el
        sistema ya le ofrece el valor de mercado. Son cosas distintas y el
        ejecutor las cuenta por separado.
        """
        amount_int = int(round(float(amount)))
        if amount_int <= 0 and action != BID_REMOVE:
            raise ActionError(f"puja inválida para {player_id}: {amount}")
        try:
            mid = int(id_market) if id_market not in (None, "", 0, "0") else 0
        except (TypeError, ValueError):
            mid = 0
        if mid <= 0:
            raise ActionError(
                f"id_market ausente para {player_id}: sin listado la puja no se puede armar"
            )
        return self._post_action(
            "/ajax/bid",
            {
                "offeree_id": int(offeree_id or 0),
                "id_market": mid,
                "id_player": str(player_id),
                "action": action,
                "bid": amount_int,
            },
        )

    def update_bid(self, *, player_id, amount, id_market=None, offeree_id=0):
        return self.place_bid(
            player_id=player_id,
            amount=amount,
            id_market=id_market,
            offeree_id=offeree_id,
            action=BID_UPDATE,
        )

    def withdraw_bid(self, *, player_id, id_market=None, offeree_id=0):
        return self.place_bid(
            player_id=player_id,
            amount=0,
            id_market=id_market,
            offeree_id=offeree_id,
            action=BID_REMOVE,
        )

    # -- ventas -------------------------------------------------------------

    def list_for_sale(self, *, player_id: str | int, price: float) -> dict[str, Any]:
        """Pone el jugador en el mercado al precio dado. Reversible con `unlist`."""
        price_int = int(round(float(price)))
        if price_int <= 0:
            raise ActionError(f"precio de venta inválido para {player_id}: {price}")
        return self._post_action(
            "/ajax/sale",
            {"id_player": str(player_id), "action": SALE_LIST, "price": price_int},
        )

    def unlist(self, *, player_id: str | int) -> dict[str, Any]:
        return self._post_action(
            "/ajax/sale",
            {"id_player": str(player_id), "action": SALE_REMOVE},
        )

    def accept_offer(
        self,
        *,
        player_id: str | int,
        owner_id: str | int | None = None,
        bid_id: str | int | None = None,
        amount: float | None = None,
    ) -> dict[str, Any]:
        """
        Acepta una oferta. Es el check de la fila: POST /ajax/offer con
        id_bid y type=accept (views/ajax/market/offers-received.twig y el
        popup de traspaso directo). `action=accept` sin `type` la deja en
        decline.
        """
        del owner_id  # el formulario no manda al dueño
        if bid_id in (None, "", 0, "0"):
            raise ActionError(f"sin id_bid para aceptar la oferta de {player_id}")
        payload: dict[str, Any] = {"id_bid": str(bid_id), "type": OFFER_ACCEPT}
        if amount is not None:
            try:
                amount_int = int(round(float(amount)))
            except (TypeError, ValueError):
                amount_int = 0
            if amount_int > 0:
                payload["amount"] = amount_int
        record = self._post_action("/ajax/offer", payload)
        if _offer_marked_decline(record):
            raise ActionError(
                "Mister marcó la oferta como rechazada",
                record=record,
            )
        return record

    def decline_offer(
        self,
        *,
        player_id: str | int,
        owner_id: str | int | None = None,
        bid_id: str | int | None = None,
    ) -> dict[str, Any]:
        """
        Rechaza una oferta concreta. Es la X de la fila: mismo /ajax/offer,
        con type=decline e id_bid. No renueva el listado.
        """
        del owner_id
        if bid_id in (None, "", 0, "0"):
            raise ActionError(f"sin id_bid para rechazar la oferta de {player_id}")
        return self._post_action(
            "/ajax/offer",
            {"id_bid": str(bid_id), "type": OFFER_DECLINE},
        )

    def renew_listing(
        self,
        *,
        player_id: str | int,
        id_market: str | int | None = None,
    ) -> dict[str, Any]:
        """
        Botón circular (popup resale): solo id_market. Mister vuelve a poner
        el jugador en venta y rechaza solo las ofertas de ese listado.
        """
        try:
            mid = int(id_market) if id_market not in (None, "", 0, "0") else 0
        except (TypeError, ValueError):
            mid = 0
        if mid <= 0:
            raise ActionError(
                f"id_market ausente para {player_id}: no se puede renovar el listado"
            )
        return self._post_action("/ajax/resale", {"id_market": mid})

    def sell_to_system(self, *, player_id: str | int) -> dict[str, Any]:
        """
        Vende al sistema al valor de mercado. El jugador sale del equipo ya:
        no hay ciclo ni comprador, así que no tiene vuelta atrás.
        """
        return self._post_action("/ajax/sell", {"id_player": str(player_id)})

    # -- cláusulas ----------------------------------------------------------

    def pay_clause(
        self,
        *,
        player_id: str | int,
        owner_id: str | int | None = None,
        amount: float | None = None,
    ) -> dict[str, Any]:
        """
        Paga la cláusula de un jugador de un rival.

        Instantánea e irreversible: el jugador cambia de equipo y el dinero se
        va en el mismo POST. El formulario (`views/ajax/clause-pay.twig`) manda
        `id_player`, `id_uc` (dueño) e `id_giphy` vacío. El importe no viaja:
        Mister cobra `pre.clause.value`. El ejecutor reverifica blindaje e
        importe antes de llamar aquí.
        """
        if owner_id in (None, "", 0, "0"):
            raise ActionError(f"cláusula de {player_id}: falta id_uc del dueño")
        return self._post_action(
            "/ajax/clause-pay",
            {
                "id_player": str(player_id),
                "id_uc": str(owner_id),
                "id_giphy": "",
            },
        )

    def raise_own_clause(self, *, player_id: str | int, steps: int) -> dict[str, Any]:
        """Sube la cláusula de un jugador propio (defensa). Cuesta dinero."""
        return self._post_action(
            "/ajax/clause-set",
            {"id_player": str(player_id), "clause_range": int(steps)},
        )
