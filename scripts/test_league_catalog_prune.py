"""
Regresión: catálogo multi-liga al abandonar comunidades en Mister.

- Con discovery no vacío, no se reinyectan LEAGUE_OVERRIDES ausentes.
- Sync --league all poda carpetas huérfanas bajo public/data/leagues/.
- Discovery vacío reutiliza leagues.json previo (no pisa con overrides viejos).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from data_engine import prune_orphan_league_dirs, write_leagues_index  # noqa: E402


def test_resolve_drops_abandoned_override() -> None:
    discovered = [
        {
            "id_community": "2500716",
            "name": "Liga del patio",
            "id_competition": 1,
        },
        {
            "id_community": "9999999",
            "name": "Otra liga",
            "id_competition": 1,
        },
    ]
    resolved = config.resolve_leagues(discovered)
    cids = {str(L["id_community"]) for L in resolved}
    assert "2500716" in cids
    assert "9999999" in cids
    # Overrides de Premier/Serie A no deben reinyectarse si no salen en discovery
    assert "2550556" not in cids, f"override reinyectado: {resolved}"
    assert "2550628" not in cids, f"override reinyectado: {resolved}"
    patio = next(L for L in resolved if L["id_community"] == "2500716")
    assert patio["slug"] == "laliga-patio"


def test_resolve_empty_uses_overrides() -> None:
    resolved = config.resolve_leagues([])
    cids = {str(L["id_community"]) for L in resolved}
    assert "2500716" in cids
    assert "2550556" in cids
    assert "2550628" in cids
    # ID de Premier anterior (pre-ascenso) no debe volver
    assert "906674" not in cids


def test_previous_leagues_as_discovered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        index_path = Path(tmp) / "leagues.json"
        index_path.write_text(
            json.dumps(
                {
                    "default_slug": "laliga-patio",
                    "leagues": [
                        {
                            "slug": "laliga-patio",
                            "name": "Liga del patio",
                            "id_community": "2500716",
                            "id_competition": 1,
                            "default": True,
                        },
                        {
                            "slug": "premier-league-d3g10-0556",
                            "name": "Premier League © - D3G10",
                            "id_community": "2550556",
                            "id_competition": 3,
                            "competition": "Premier League",
                        },
                        {
                            "slug": "serie-a-d2g3-0628",
                            "name": "Serie A © - D2G3",
                            "id_community": "2550628",
                            "id_competition": 10,
                            "competition": "Serie A",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        prev = config.LEAGUES_INDEX_PATH
        try:
            config.LEAGUES_INDEX_PATH = index_path
            rows = config.previous_leagues_as_discovered()
            cids = {str(r["id_community"]) for r in rows}
            assert cids == {"2500716", "2550556", "2550628"}
            # Como discovery: resolve conserva las 3 (y slugs del índice)
            resolved = config.resolve_leagues(rows)
            by_cid = {str(L["id_community"]): L for L in resolved}
            assert by_cid["2550556"]["slug"] == "premier-league-d3g10-0556"
            assert by_cid["2550628"]["slug"] == "serie-a-d2g3-0628"
            assert by_cid["2550628"]["competition"] == "Serie A"
        finally:
            config.LEAGUES_INDEX_PATH = prev


def test_prune_orphan_dirs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "leagues"
        (root / "keep-me").mkdir(parents=True)
        (root / "keep-me" / "latest_data.json").write_text("{}", encoding="utf-8")
        (root / "gone-league").mkdir(parents=True)
        (root / "gone-league" / "latest_data.json").write_text("{}", encoding="utf-8")
        prev_dir = config.LEAGUES_DIR
        try:
            config.LEAGUES_DIR = root
            prune_orphan_league_dirs({"keep-me"})
            assert (root / "keep-me").is_dir()
            assert not (root / "gone-league").exists()
        finally:
            config.LEAGUES_DIR = prev_dir


def test_write_index_all_no_merge_extras() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        leagues_dir = data / "leagues"
        leagues_dir.mkdir()
        (leagues_dir / "orphan-slug").mkdir()
        index_path = data / "leagues.json"
        index_path.write_text(
            json.dumps(
                {
                    "default_slug": "keep-slug",
                    "leagues": [
                        {"slug": "keep-slug", "name": "Keep"},
                        {"slug": "orphan-slug", "name": "Orphan"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        prev_index = config.LEAGUES_INDEX_PATH
        prev_leagues = config.LEAGUES_DIR
        prev_default = config.DEFAULT_LEAGUE_SLUG
        prev_effective = config.get_effective_leagues()
        try:
            config.LEAGUES_INDEX_PATH = index_path
            config.LEAGUES_DIR = leagues_dir
            config.set_effective_leagues(
                [
                    {
                        "slug": "keep-slug",
                        "name": "Keep",
                        "id_community": "1",
                        "default": True,
                    }
                ]
            )
            write_leagues_index(
                [
                    {
                        "slug": "keep-slug",
                        "name": "Keep",
                        "id_community": "1",
                        "default": True,
                    }
                ],
                merge=False,
            )
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            slugs = [e["slug"] for e in idx["leagues"]]
            assert slugs == ["keep-slug"], slugs
            assert not (leagues_dir / "orphan-slug").exists()
        finally:
            config.LEAGUES_INDEX_PATH = prev_index
            config.LEAGUES_DIR = prev_leagues
            config.DEFAULT_LEAGUE_SLUG = prev_default
            config.set_effective_leagues(prev_effective)


def main() -> int:
    test_resolve_drops_abandoned_override()
    test_resolve_empty_uses_overrides()
    test_previous_leagues_as_discovered()
    test_prune_orphan_dirs()
    test_write_index_all_no_merge_extras()
    print("OK — league catalog prune")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
