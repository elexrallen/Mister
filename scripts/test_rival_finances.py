"""Ledger de rivales: fichas, feed y techo de puja (¼ del VM)."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rival_finances import (  # noqa: E402
    build_rival_finances,
    load_finance_snapshot,
    parse_es_date,
    parse_feed_ajax_cards,
    parse_feed_prizes,
    parse_feed_transfers,
    parse_player_profile,
    profile_is_initial_held,
    rival_bid_cap,
    run_rival_finances,
    snapshot_needs_bootstrap,
    sorteo_owner_id,
    start_mode_for_league,
    vm_at_date,
)

from data_engine import estimate_rival_liquidity  # noqa: E402


def test_bid_cap_formula_patio() -> None:
    # Validación de campo 29/8/2026: 17.940.654 + 88.359.000 × 0,25
    assert rival_bid_cap(17_940_654, 88_359_000, 4) == 40_030_404
    # Saldo negativo: −15.864.844 + 99.063.000 × 0,25
    assert rival_bid_cap(-15_864_844, 99_063_000, 4) == 8_900_906


def test_parse_es_date_sept() -> None:
    assert parse_es_date("24 jul 2026") == date(2026, 7, 24)
    assert parse_es_date("1 sept 2025") == date(2025, 9, 1)
    assert parse_es_date("2026-07-24") == date(2026, 7, 24)


def test_huijsen_is_initial() -> None:
    raw = {
        "status": "ok",
        "data": {
            "player": {
                "id": 63788,
                "name": "Dean Huijsen",
                "owner": {"id": 15399759, "name": "Adriano"},
                "transfer": {"date": False, "origin": None, "price": None},
            },
            "owners": [],
            "values_chart": {
                "points": [
                    {"value": 9_670_000, "date": "24 jul 2026"},
                    {"value": 9_830_000, "date": "29 ago 2026"},
                ]
            },
        },
    }
    prof = parse_player_profile(raw)
    assert prof is not None
    assert profile_is_initial_held(prof)
    assert sorteo_owner_id(prof) == "15399759"
    assert vm_at_date(prof["values_chart"], date(2026, 7, 24)) == 9_670_000


def test_vlachodimos_is_market() -> None:
    raw = {
        "data": {
            "player": {
                "id": 65111,
                "name": "Odysseas Vlachodimos",
                "owner": {"id": 15399168, "name": "Emilio"},
                "transfer": {"date": "17 ago 2026", "origin": "market", "price": 6_678_000},
            },
            "owners": [
                {
                    "from": "Mister",
                    "to": "Emilio",
                    "id": 539615593,
                    "id_player": 65111,
                    "id_uc_from": 0,
                    "id_uc_to": 15399168,
                    "price": 6_678_000,
                    "date": "17 ago 2026",
                    "type": "normal",
                    "transferType": "Fichaje",
                }
            ],
        }
    }
    prof = parse_player_profile(raw)
    assert prof is not None
    assert not profile_is_initial_held(prof)
    assert sorteo_owner_id(prof) is None
    assert prof["owners"][0]["type"] == "normal"
    assert prof["owners"][0]["from_uc"] == "0"
    assert prof["owners"][0]["price"] == 6_678_000


def test_aubameyang_is_clause() -> None:
    raw = {
        "data": {
            "player": {
                "id": 36410,
                "name": "Pierre-Emerick Aubameyang",
                "owner": {"id": 15399715, "name": "Jponce13"},
                "transfer": {"date": "29 ago 2026", "origin": "clause", "price": 17_640_000},
            },
            "owners": [
                {
                    "from": "Emilio",
                    "to": "Jponce13",
                    "id": 543067066,
                    "id_uc_from": 15399168,
                    "id_uc_to": 15399715,
                    "price": 17_640_000,
                    "date": "29 ago 2026",
                    "type": "clause",
                    "transferType": "Cláusula",
                },
                {
                    "from": "Jony",
                    "to": "Emilio",
                    "id": 542303420,
                    "id_uc_from": 15399149,
                    "id_uc_to": 15399168,
                    "price": 8_820_000,
                    "date": "26 ago 2026",
                    "type": "clause",
                    "transferType": "Cláusula",
                },
                {
                    "from": "Emilio",
                    "to": "Jony",
                    "id": 541920699,
                    "id_uc_from": 15399168,
                    "id_uc_to": 15399149,
                    "price": 5_880_000,
                    "date": "25 ago 2026",
                    "type": "clause",
                    "transferType": "Cláusula",
                },
            ],
        }
    }
    prof = parse_player_profile(raw)
    assert prof is not None
    assert not profile_is_initial_held(prof)
    assert sorteo_owner_id(prof) == "15399168"
    assert prof["owners"][0]["type"] == "clause"
    assert prof["owners"][0]["price"] == 17_640_000


FEED_HTML = """
<div id="feed-954970377" class="card card-transfer" data-comments="0">
  <ul>
    <li>
      <div class="title">
        <strong>Pierre-Emerick Aubameyang</strong> cambia de <em>Emilio</em> a <em>Jponce13</em>
        por pago de cláusula
      </div>
      <div class="player-avatar" data-id_player="36410"></div>
      <a href="users/15399168/emilio"></a>
      <a href="users/15399715/jponce13"></a>
      <div class="price">17.640.000</div>
    </li>
  </ul>
</div>
<div id="feed-954857107" class="card card-gameweek_end_pools" data-comments="0">
  <a href="users/15402265/ruben"></a>
  <div class="played green">+2.100.000</div>
</div>
<div id="feed-954857081" class="card card-gameweek_end" data-comments="0">
  <ul>
    <li>
      <a href="users/15402265/ruben">
        <div class="played green">+1.650.000</div>
      </a>
    </li>
    <li>
      <a href="users/15399715/jponce13">
        <div class="played green">+1.600.000</div>
      </a>
    </li>
  </ul>
</div>
<div id="feed-111111" class="card card-transfer">
  <ul>
    <li>
      <div class="title">
        <strong>Isco</strong> cambia de <em>Mister</em> a <em>Emilio</em>
      </div>
      <div class="player-avatar" data-id_player="111"></div>
      <a href="users/15399168/emilio"></a>
      <div class="price">11.765.002</div>
    </li>
  </ul>
</div>
<div id="feed-955647908" class="card card-payment" data-comments="0">
  <div class="name">Manuel</div>
  <div class="played green">+3.200.000</div>
</div>
"""


def test_parse_feed_transfer_and_prize() -> None:
    txs = parse_feed_transfers(FEED_HTML)
    assert any(t["player_id"] == "36410" and t["price"] == 17_640_000 for t in txs)
    clause = next(t for t in txs if t["player_id"] == "36410")
    assert clause["from_uc"] == "15399168"
    assert clause["to_uc"] == "15399715"
    assert clause["type"] == "clause"
    isco = next(t for t in txs if t["player_id"] == "111")
    assert isco["from_uc"] is None
    assert isco["to_uc"] == "15399168"
    assert isco["price"] == 11_765_002

    prizes = parse_feed_prizes(FEED_HTML)
    by_id = {p["id"]: p for p in prizes}
    assert by_id["prize-954857081-15402265"]["amount"] == 1_650_000
    assert by_id["prize-954857081-15399715"]["amount"] == 1_600_000
    pool = by_id["prize-pool-954857107-15402265"]
    assert pool["amount"] == 2_100_000
    assert pool["kind"] == "pools"
    admin = by_id["prize-admin-955647908-manuel"]
    assert admin["amount"] == 3_200_000
    assert admin["kind"] == "admin"
    assert admin["name"] == "Manuel"


def test_parse_feed_ajax_cards_cases() -> None:
    cards = [
        {
            "id": 954970377,
            "category": "transfer",
            "created": "2026-08-29 00:00:00",
            "data": [
                {
                    "id_transfer": 543067066,
                    "id_uc_from": 15399168,
                    "id_uc_to": 15399715,
                    "type": "clause",
                    "price": 17_640_000,
                    "from": "Emilio",
                    "to": "Jponce13",
                    "id": 36410,
                }
            ],
        },
        {
            "id": 1,
            "category": "transfer",
            "data": [
                {
                    "id_transfer": 99,
                    "id_uc_from": 0,
                    "id_uc_to": 15402265,
                    "type": "normal",
                    "price": 2_408_880,
                    "from": "Mister",
                    "to": "Ruben",
                    "id": 64382,
                }
            ],
        },
        {
            "id": 2,
            "category": "transfer",
            "data": [
                {
                    "id_transfer": 100,
                    "id_uc_from": 15399848,
                    "id_uc_to": 0,
                    "type": "normal",
                    "price": 5_360_600,
                    "from": "Manuel",
                    "to": "Mister",
                    "id": 68248,
                }
            ],
        },
        {
            "id": 954857081,
            "category": "gameweek_end",
            "data": {
                "id_gameweek": 3968,
                "ranking": {
                    "dettach": False,
                    "ranking": {
                        "positions": [
                            {"idUc": 15402265, "payment": 1_650_000},
                            {"idUc": 15399715, "payment": 1_600_000},
                        ]
                    },
                },
            },
        },
        {"id": 9, "category": "gameweek_end_pools", "data": {
            "id_gameweek": 4044,
            "gameweek": 3,
            "table": [
                {"id": 15399697, "name": "PaitoPau", "hits": 7, "amount": 2_100_000},
                {"id": 15399113, "name": "Francisco", "hits": 5, "amount": 1_500_000},
                {"id": 15399131, "name": "Abel", "hits": 4, "amount": 1_200_000},
                {"id": 15399168, "name": "Emilio", "hits": 4, "amount": 1_200_000},
                {"id": 15399759, "name": "Adriano", "hits": 0, "amount": None},
            ],
        }},
        {"id": 10, "category": "player_transfer", "data": [{"id": 1}]},
        {"id": 11, "category": "clauses_drops", "data": [{"id": 2}]},
        {
            "id": 955647908,
            "category": "payment",
            "data": {
                "reason": "Por llorón",
                "payments": [
                    {"name": "Manuel", "amount": 3_200_000, "sign": "+", "class": "green"},
                    {"name": "Abel", "amount": 500_000, "sign": "-", "class": "red"},
                ],
            },
        },
        {"id": 12, "category": "admin", "data": {"key": "market_speed", "value": 2}},
    ]
    txs, prizes = parse_feed_ajax_cards(cards)
    assert len(txs) == 3
    clause = next(t for t in txs if t["player_id"] == "36410")
    assert clause["id"] == "543067066"
    assert clause["type"] == "clause"
    assert clause["price"] == 17_640_000
    buy = next(t for t in txs if t["player_id"] == "64382")
    assert buy["from_uc"] is None
    assert buy["to_uc"] == "15402265"
    sell = next(t for t in txs if t["player_id"] == "68248")
    assert sell["from_uc"] == "15399848"
    assert sell["to_uc"] is None
    assert {p["uc"]: p["amount"] for p in prizes if p.get("kind") not in ("pools", "admin")} == {
        "15402265": 1_650_000,
        "15399715": 1_600_000,
    }
    assert prizes[0]["id"] == "prize-gw3968-15402265"
    assert prizes[0]["gameweek_id"] == "3968"
    pool_prizes = {p["uc"]: p for p in prizes if p.get("kind") == "pools"}
    assert pool_prizes["15399697"]["amount"] == 2_100_000
    assert pool_prizes["15399113"]["amount"] == 1_500_000
    assert pool_prizes["15399131"]["amount"] == 1_200_000
    assert pool_prizes["15399168"]["amount"] == 1_200_000
    assert "15399759" not in pool_prizes
    assert pool_prizes["15399697"]["id"] == "prize-pool-gw4044-15399697"
    assert pool_prizes["15399697"]["gameweek_id"] == "4044"
    admin_prizes = {p["name"]: p for p in prizes if p.get("kind") == "admin"}
    assert admin_prizes["Manuel"]["amount"] == 3_200_000
    assert admin_prizes["Manuel"]["id"] == "prize-admin-955647908-manuel"
    assert admin_prizes["Manuel"]["reason"] == "Por llorón"
    assert admin_prizes["Abel"]["amount"] == -500_000

    dup_cards = cards + [
        {
            "id": 953510932,
            "category": "gameweek_end",
            "data": {
                "id_gameweek": 3968,
                "ranking": {
                    "ranking": {
                        "positions": [
                            {"idUc": 15402265, "payment": 1_650_000},
                            {"idUc": 15399715, "payment": 1_600_000},
                        ]
                    },
                },
            },
        }
    ]
    _, prizes_dedup = parse_feed_ajax_cards(dup_cards)
    gw_prizes = [p for p in prizes_dedup if p.get("kind") not in ("pools", "admin")]
    assert len(gw_prizes) == 2
    assert {p["uc"]: p["amount"] for p in gw_prizes} == {
        "15402265": 1_650_000,
        "15399715": 1_600_000,
    }
    pool_dup = [p for p in prizes_dedup if p.get("kind") == "pools"]
    assert len(pool_dup) == 4


def test_ledger_initial_cash_and_bid_cap() -> None:
    sorteo = date(2026, 7, 24)
    profiles = [
        parse_player_profile(
            {
                "data": {
                    "player": {
                        "id": 1,
                        "name": "Inicial",
                        "owner": {"id": 10, "name": "Rival"},
                        "transfer": {"origin": None, "price": None},
                    },
                    "owners": [],
                    "values_chart": {"points": [{"value": 10_000_000, "date": "24 jul 2026"}]},
                }
            }
        ),
        parse_player_profile(
            {
                "data": {
                    "player": {
                        "id": 2,
                        "name": "Fichaje",
                        "owner": {"id": 10, "name": "Rival"},
                        "transfer": {"origin": "market", "price": 5_000_000},
                    },
                    "owners": [
                        {
                            "from": "Mister",
                            "to": "Rival",
                            "id": 99,
                            "id_uc_from": 0,
                            "id_uc_to": 10,
                            "price": 5_000_000,
                            "date": "1 ago 2026",
                            "type": "normal",
                        }
                    ],
                }
            }
        ),
    ]
    profiles = [p for p in profiles if p]
    rivals = [{"team_id": "10", "manager": "Rival", "squad_value": 40_000_000}]
    out, meta = build_rival_finances(
        profiles=profiles,
        rivals=rivals,
        me_uc="99",
        me_balance=0,
        me_squad_value=0,
        feed_prizes=[{"uc": "10", "amount": 1_650_000}],
        starting_budget=50_000_000,
        sorteo_date=sorteo,
        start_mode="random_minus_vm",
        max_debt_level=4,
    )
    row = out[0]
    # 50M − 10M VM sorteo − 5M compra + 1,65M premio
    assert row["liquidity_estimated"] == 36_650_000
    assert row["bid_cap_estimated"] == 36_650_000 + 10_000_000
    assert row["initial_players_count"] == 1
    assert meta["source"] == "player_profiles+feed"


def test_estimate_rival_liquidity_keeps_bid_cap() -> None:
    row = estimate_rival_liquidity(
        {
            "team_id": "10",
            "liquidity_estimated": 36_650_000,
            "bid_cap_estimated": 46_650_000,
            "squad_value": 40_000_000,
            "recent_net": -5_000_000,
            "activity": "media",
        }
    )
    assert row["liquidity_estimated"] == 36_650_000
    assert row["bid_cap_estimated"] == 46_650_000
    assert row["activity"] == "media"


def test_contest_start_mode_is_cash() -> None:
    assert start_mode_for_league("lfm", "contest", None) == "cash"
    assert start_mode_for_league("classic", "normal", "random_minus_vm") == "random_minus_vm"


def _boot_profiles() -> list[dict]:
    return [
        p
        for p in (
            parse_player_profile(
                {
                    "data": {
                        "player": {
                            "id": 1,
                            "name": "Inicial",
                            "owner": {"id": 10, "name": "Rival"},
                            "transfer": {"origin": None, "price": None},
                        },
                        "owners": [],
                        "values_chart": {"points": [{"value": 10_000_000, "date": "24 jul 2026"}]},
                    }
                }
            ),
            parse_player_profile(
                {
                    "data": {
                        "player": {
                            "id": 2,
                            "name": "Fichaje",
                            "owner": {"id": 10, "name": "Rival"},
                            "transfer": {"origin": "market", "price": 5_000_000},
                        },
                        "owners": [
                            {
                                "from": "Mister",
                                "to": "Rival",
                                "id": 99,
                                "id_uc_from": 0,
                                "id_uc_to": 10,
                                "price": 5_000_000,
                                "date": "1 ago 2026",
                                "type": "normal",
                            }
                        ],
                    }
                }
            ),
        )
        if p
    ]


def test_incremental_feed_does_not_double_count() -> None:
    sorteo = date(2026, 7, 24)
    rivals = [{"team_id": "10", "manager": "Rival", "squad_value": 40_000_000}]
    feed_old = [
        {
            "id": "feed-old-1",
            "player_id": "2",
            "from_uc": None,
            "to_uc": "10",
            "price": 5_000_000,
            "type": "normal",
        }
    ]
    prizes = [{"id": "prize-1-10", "uc": "10", "amount": 1_650_000}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out1, meta1 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=_boot_profiles(),
            feed_transfers=feed_old,
            feed_prizes=prizes,
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            persist=True,
            root=root,
        )
        assert meta1["update_mode"] == "bootstrap"
        cash1 = out1[0]["liquidity_estimated"]
        # 50M − 10M − 5M + 1.65M (el feed_old duplica la compra de ficha y se ignora)
        assert cash1 == 36_650_000

        snap = load_finance_snapshot("test-liga", root=root)
        assert snap is not None
        feed_new = feed_old + [
            {
                "id": "feed-new-1",
                "player_id": "3",
                "from_uc": None,
                "to_uc": "10",
                "price": 2_000_000,
                "type": "normal",
            }
        ]
        out2, meta2 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=[],
            feed_transfers=feed_new,
            feed_prizes=prizes,
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            snapshot=snap,
            persist=True,
            root=root,
        )
        assert meta2["update_mode"] == "feed_incremental"
        assert meta2["new_transfers"] == 1
        assert meta2["new_prizes"] == 0
        assert out2[0]["liquidity_estimated"] == cash1 - 2_000_000


def test_quiniela_and_gameweek_prizes_stack() -> None:
    sorteo = date(2026, 7, 24)
    rivals = [{"team_id": "10", "manager": "Rival", "squad_value": 40_000_000}]
    prizes = [
        {
            "id": "prize-gw4044-10",
            "uc": "10",
            "amount": 2_250_000,
            "gameweek_id": "4044",
            "kind": "gameweek",
        },
        {
            "id": "prize-pool-gw4044-10",
            "uc": "10",
            "amount": 1_200_000,
            "gameweek_id": "4044",
            "kind": "pools",
        },
    ]
    out, _meta = build_rival_finances(
        profiles=_boot_profiles(),
        rivals=rivals,
        me_uc="99",
        me_balance=0,
        me_squad_value=0,
        feed_prizes=prizes,
        starting_budget=50_000_000,
        sorteo_date=sorteo,
        start_mode="random_minus_vm",
        max_debt_level=4,
    )
    # 50M − 10M VM − 5M compra + 2.25M jornada + 1.2M quiniela
    assert out[0]["liquidity_estimated"] == 38_450_000


def test_incremental_applies_quiniela_after_gameweek_prize() -> None:
    sorteo = date(2026, 7, 24)
    rivals = [{"team_id": "10", "manager": "Rival", "squad_value": 40_000_000}]
    gw_prize = {
        "id": "prize-gw4044-10",
        "uc": "10",
        "amount": 2_250_000,
        "gameweek_id": "4044",
        "kind": "gameweek",
    }
    pool_prize = {
        "id": "prize-pool-gw4044-10",
        "uc": "10",
        "amount": 1_200_000,
        "gameweek_id": "4044",
        "kind": "pools",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out1, meta1 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=_boot_profiles(),
            feed_prizes=[gw_prize],
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            persist=True,
            root=root,
        )
        assert meta1["update_mode"] == "bootstrap"
        cash1 = out1[0]["liquidity_estimated"]
        assert cash1 == 37_250_000

        snap = load_finance_snapshot("test-liga", root=root)
        assert snap is not None
        out2, meta2 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=[],
            feed_prizes=[gw_prize, pool_prize],
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            snapshot=snap,
            persist=True,
            root=root,
        )
        assert meta2["update_mode"] == "feed_incremental"
        assert meta2["new_prizes"] == 1
        assert out2[0]["liquidity_estimated"] == cash1 + 1_200_000


def test_admin_payment_resolves_name_and_stacks() -> None:
    sorteo = date(2026, 7, 24)
    rivals = [{"team_id": "10", "manager": "Manuel", "squad_value": 40_000_000}]
    prizes = [
        {
            "id": "prize-admin-955647908-manuel",
            "name": "Manuel",
            "amount": 3_200_000,
            "kind": "admin",
            "source": "feed_admin_payment",
        }
    ]
    out, _meta = build_rival_finances(
        profiles=_boot_profiles(),
        rivals=rivals,
        me_uc="99",
        me_balance=0,
        me_squad_value=0,
        feed_prizes=prizes,
        starting_budget=50_000_000,
        sorteo_date=sorteo,
        start_mode="random_minus_vm",
        max_debt_level=4,
    )
    # 50M − 10M VM − 5M compra + 3.2M admin
    assert out[0]["liquidity_estimated"] == 38_200_000


def test_incremental_applies_admin_payment() -> None:
    sorteo = date(2026, 7, 24)
    rivals = [{"team_id": "10", "manager": "Manuel", "squad_value": 40_000_000}]
    admin_prize = {
        "id": "prize-admin-955647908-manuel",
        "name": "Manuel",
        "amount": 3_200_000,
        "kind": "admin",
        "source": "feed_admin_payment",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out1, meta1 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=_boot_profiles(),
            feed_prizes=[],
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            persist=True,
            root=root,
        )
        assert meta1["update_mode"] == "bootstrap"
        cash1 = out1[0]["liquidity_estimated"]
        assert cash1 == 35_000_000

        snap = load_finance_snapshot("test-liga", root=root)
        assert snap is not None
        out2, meta2 = run_rival_finances(
            community_id="test-liga",
            rivals=rivals,
            me_uc="99",
            profiles=[],
            feed_prizes=[admin_prize],
            starting_budget=50_000_000,
            sorteo_date=sorteo,
            start_mode="random_minus_vm",
            max_debt_level=4,
            snapshot=snap,
            persist=True,
            root=root,
        )
        assert meta2["update_mode"] == "feed_incremental"
        assert meta2["new_prizes"] == 1
        assert out2[0]["liquidity_estimated"] == cash1 + 3_200_000


def test_stale_snapshot_forces_bootstrap() -> None:
    now = datetime.now(timezone.utc)
    snap = {
        "version": 1,
        "cash": {"10": 1},
        "sorteo_date": "2026-07-24",
        "start_mode": "random_minus_vm",
        "starting_budget": 50_000_000,
        "updated_at": (now - timedelta(days=30)).isoformat(),
    }
    need, reason = snapshot_needs_bootstrap(
        snap,
        rivals=[{"team_id": "10"}],
        me_uc=None,
        sorteo_date=date(2026, 7, 24),
        start_mode="random_minus_vm",
        starting_budget=50_000_000,
        now=now,
        max_age_days=25,
    )
    assert need is True
    assert reason == "stale"


def test_new_manager_forces_bootstrap() -> None:
    now = datetime.now(timezone.utc)
    snap = {
        "version": 1,
        "cash": {"10": 1},
        "sorteo_date": "2026-07-24",
        "start_mode": "random_minus_vm",
        "starting_budget": 50_000_000,
        "updated_at": now.isoformat(),
    }
    need, reason = snapshot_needs_bootstrap(
        snap,
        rivals=[{"team_id": "10"}, {"team_id": "11"}],
        me_uc=None,
        sorteo_date=date(2026, 7, 24),
        start_mode="random_minus_vm",
        starting_budget=50_000_000,
        now=now,
    )
    assert need is True
    assert reason == "new_manager"


def test_probe_json_if_present() -> None:
    """Si hay sondas en cache, confirma tipos reales (Huijsen / Vlachodimos / Auba)."""
    probe = ROOT / "cache" / "probe"
    mapping = {
        "player_profile_63788.json": ("initial", "63788"),
        "player_profile_65111.json": ("market", "65111"),
        "player_profile_36410.json": ("clause", "36410"),
    }
    any_found = False
    for fname, (kind, pid) in mapping.items():
        path = probe / fname
        if not path.exists():
            continue
        any_found = True
        raw = json.loads(path.read_text(encoding="utf-8"))
        prof = parse_player_profile(raw)
        assert prof is not None
        assert prof["player_id"] == pid
        if kind == "initial":
            assert profile_is_initial_held(prof)
            assert not prof["owners"]
        elif kind == "market":
            assert prof["owners"]
            assert prof["owners"][0]["type"] == "normal"
            assert sorteo_owner_id(prof) is None
        else:
            assert prof["owners"][0]["type"] == "clause"
            assert sorteo_owner_id(prof) == "15399168"
    if not any_found:
        print("skip probe json (no cache/probe)")


def main() -> None:
    tests = [
        test_bid_cap_formula_patio,
        test_parse_es_date_sept,
        test_huijsen_is_initial,
        test_vlachodimos_is_market,
        test_aubameyang_is_clause,
        test_parse_feed_transfer_and_prize,
        test_parse_feed_ajax_cards_cases,
        test_ledger_initial_cash_and_bid_cap,
        test_estimate_rival_liquidity_keeps_bid_cap,
        test_contest_start_mode_is_cash,
        test_incremental_feed_does_not_double_count,
        test_quiniela_and_gameweek_prizes_stack,
        test_incremental_applies_quiniela_after_gameweek_prize,
        test_admin_payment_resolves_name_and_stacks,
        test_incremental_applies_admin_payment,
        test_stale_snapshot_forces_bootstrap,
        test_new_manager_forces_bootstrap,
        test_probe_json_if_present,
    ]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} tests ok")


if __name__ == "__main__":
    main()
