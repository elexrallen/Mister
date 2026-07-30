"""Parser de mercado: precio de puja del botón, no solo VM del underName."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mister_client import parse_market_players  # noqa: E402
from target_board import _normalize_player  # noqa: E402


HTML = """
<li>
  <div class="player-row">
    <a class="btn btn-sw-link player" href="players/29166/joan-garcia">
      <div class="icons">
        <img class='team-logo' width='20' height='20' src='https://cdn/teams/3.png'>
        <div class='player-position ' data-position='1'></div>
        <div class="points">0</div>
      </div>
      <div class="player-avatar" data-id_player="29166"></div>
      <div class="info">
        <div class="name">J. Garcia</div>
        <div class="underName"><span class="euro">€</span> 17.975.000</div>
      </div>
    </a>
    <div class="player-btns">
      <button class="btn btn-popup btn-bid btn--md btn--tertiary"
        data-id_owner="15399131"
        data-id_player="29166"
        data-popup="bid"
        data-style="tertiary"
        data-text="25.798.194"
        data-preload="player-community-info">
        25.798.194
      </button>
    </div>
  </div>
</li>
"""


def main() -> None:
    players = parse_market_players(HTML)
    assert len(players) == 1, players
    p = players[0]
    assert p["id"] == "29166"
    assert int(p["market_value"]) == 17_975_000, p
    assert int(p["price"]) == 25_798_194, p
    assert int(p["min_bid"]) == 25_798_194, p
    assert p.get("listed_by_rival") is True
    assert str(p.get("owner_id")) == "15399131"

    n = _normalize_player(
        {
            **p,
            "seller": "market",
            "on_daily_market": True,
            "ff_mister_avg": 6.6,
            "ff_mister_points": 198,
            "lineup_prob": 90,
        },
        owned=False,
        price_series=None,
    )
    assert n is not None
    assert float(n["price"]) == 25_798_194.0, n["price"]
    print("ok: ask 25.798.194 (VM 17.975.000) en plantilla perfecta")


if __name__ == "__main__":
    main()
