"""
Sonda de los endpoints de escritura de Mister.

Los formularios de puja, venta y venta al sistema están en la caché del repo,
así que su contrato es conocido. Los de aceptar/rechazar oferta y pagar cláusula
no: solo aparecen sus callbacks en el JS. Este script localiza las plantillas y
lista los campos reales de cada formulario para no adivinar.

Sin credenciales solo analiza la caché. Con `--live` descarga las páginas.

Uso:
    python scripts/probe_offer_endpoints.py
    python scripts/probe_offer_endpoints.py --live
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CACHE_DIRS = [ROOT / "cache", ROOT / "cache" / "probe", ROOT / "cache" / "js"]

# Páginas de Mister que embeben las plantillas twig de los popups
LIVE_PAGES = ["/market", "/team", "/players"]

TWIG_ID = re.compile(r'id="twig-([a-z0-9\-]+)"')
DATA_AJAX = re.compile(r'data-ajax="([a-z0-9\-/]+)"')
CALLBACK = re.compile(r"function callback_([a-z0-9_]+)\s*\(")
INPUT_FIELD = re.compile(
    r'<input[^>]*?name="([^"]+)"[^>]*?(?:value="([^"]*)")?[^>]*?>', re.I
)

# Acciones cuyo contrato ya está confirmado en la caché del repo
CONFIRMED = {
    "bid": "/ajax/bid  ·  offeree_id, id_market, id_player, action, bid",
    "sale": "/ajax/sale  ·  id_player, action(sale|remove), price",
    "sell": "/ajax/sell  ·  id_player",
    "clause-set": "/ajax/clause-set  ·  id_player, clause_range, shield",
    "clause-pay": "/ajax/clause-pay  ·  id_player, id_uc, id_giphy",
}

# Lo que hay que confirmar en vivo antes de dejar que el ejecutor lo use
TO_PROBE = {
    "offer": "aceptar oferta recibida (callback_offer dice «Jugador vendido»)",
    "resale": "rechazar / relistar tras oferta (callback_resale)",
    "rescind": "despedir jugador",
}


def _iter_sources(live: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for directory in CACHE_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix not in (".html", ".js", ".twig"):
                continue
            try:
                out.append((str(path.relative_to(ROOT)), path.read_text(
                    encoding="utf-8", errors="replace"
                )))
            except OSError:
                continue
    if live:
        try:
            import mister_client
        except ImportError as exc:
            print(f"  no se puede importar mister_client: {exc}")
            return out
        for page in LIVE_PAGES:
            try:
                out.append((f"LIVE {page}", mister_client.fetch_html(page)))
            except Exception as exc:  # noqa: BLE001
                print(f"  {page} falló: {exc}")
    return out


def _template_body(text: str, twig_id: str) -> str | None:
    marker = f'id="twig-{twig_id}"'
    start = text.find(marker)
    if start < 0:
        return None
    end = text.find("</script>", start)
    return text[start : end if end > 0 else start + 4000]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="descargar páginas de Mister")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    sources = _iter_sources(args.live)
    print(f"Fuentes analizadas: {len(sources)}")

    twig_ids: dict[str, str] = {}
    ajax_actions: dict[str, str] = {}
    callbacks: dict[str, str] = {}
    for name, text in sources:
        for tid in TWIG_ID.findall(text):
            twig_ids.setdefault(tid, name)
        for act in DATA_AJAX.findall(text):
            ajax_actions.setdefault(act, name)
        for cb in CALLBACK.findall(text):
            callbacks.setdefault(cb, name)

    print("\n--- Contratos ya confirmados en la caché ---")
    for key, desc in CONFIRMED.items():
        seen = " (plantilla en caché)" if key in ajax_actions else ""
        print(f"  {key:12} {desc}{seen}")

    print("\n--- Pendientes de sondeo ---")
    for key, desc in TO_PROBE.items():
        body = None
        origin = twig_ids.get(key) or ajax_actions.get(key)
        if origin:
            for name, text in sources:
                if name != origin:
                    continue
                body = _template_body(text, key)
                break
        cb = f"callback_{key.replace('-', '_')}"
        cb_src = callbacks.get(key.replace("-", "_"))
        print(f"\n  [{key}] {desc}")
        print(f"    plantilla   {origin or 'NO ENCONTRADA'}")
        print(f"    callback    {cb_src or 'no'}{'  (' + cb + ')' if cb_src else ''}")
        if body:
            fields = [(n, v) for n, v in INPUT_FIELD.findall(body)]
            if fields:
                print("    campos del formulario:")
                for fname, fval in fields:
                    print(f"      - {fname:14} {fval or ''}")
            else:
                print("    sin <input> en la plantilla")
        else:
            print("    ACCIÓN: abrir el popup en Mister con DevTools y copiar el")
            print("            payload del POST antes de habilitarlo en el ejecutor.")

    print("\n--- Todos los data-ajax vistos ---")
    for act in sorted(ajax_actions):
        print(f"  {act:16} {ajax_actions[act]}")

    print("\n--- Todos los callback_* vistos (revelan endpoints ajax/<nombre>) ---")
    for cb in sorted(callbacks):
        print(f"  ajax/{cb.replace('_', '-'):16} {callbacks[cb]}")

    missing = [k for k in TO_PROBE if k not in twig_ids and k not in ajax_actions]
    if missing:
        print(f"\nSin contrato verificable: {', '.join(missing)}")
        print("El ejecutor los deja deshabilitados hasta confirmarlos en vivo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
