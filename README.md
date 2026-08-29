# Mister Fantasy Advisor

Dashboard Jamstack + PWA para tomar mejores decisiones diarias en tu liga privada de **Mister Fantasy** (Mundo Deportivo). Cruza tu plantilla, mercado, rivales e **histórico de rendimiento multi-temporada** y genera recomendaciones competitivas.

## Qué incluye

- **Pipeline Python** (`src/data_engine.py`) que materializa `public/data/latest_data.json`
- **GitHub Action** diaria (06:00 UTC) + disparo manual + `repository_dispatch` desde la PWA
- **Dashboard estático** (HTML + Tailwind CDN + JS) en `/public`, listo para **GitHub Pages**
- **PWA instalable** en el móvil (manifest + service worker) con botón **Actualizar** (nube)

## Jerarquía de fuentes

**Mister es la autoridad de datos duros.** Todo lo que Mister publica (economía,
puntos por jornada, valores y su delta diario, rival y localía, calendario con
kickoffs, reglas de liga y capitán) se toma de Mister y no se contrasta con
terceros. Las fuentes externas solo cubren lo que Mister no da.

| Prioridad | Fuente | Qué aporta | Estado |
|-----------|--------|------------|--------|
| 1 | **Mister** (`MISTER_TOKEN`): `ajax/sw/players`, `/feed`, `ajax/sw/gameweek`, `ajax/sw/competition`, balances y plantillas rivales | Mercado, plantilla, saldos, cláusulas, `recent_gw_points`, `price_delta_1d`, `next_opponent_team_id`, jornada y kickoffs, reglas | Autoridad |
| 2 | **Fútbol Fantasy** | Probabilidad de titularidad (`gw_lineup_prob`), previa de alineaciones, lesionados y sancionados, producción por temporada (`ff_mister_avg`) | Autoridad complementaria |
| 3 | **FotMob** | Nota, minutos, goles y xG de los últimos 5 partidos | Opcional, acotado a plantilla + mercado + top del pool (once objetivo) |
| 4 | **Jornada Perfecta** | Dudas y `gw_*` | Respaldo: solo se ejecuta si la previa de FF sale `partial` o `fail` |
| 5 | `public/data/leagues/<slug>/history/YYYY-MM-DD.json` | Serie propia de precios y puntos por jornada | Derivada |
| 6 | API-Football o `src/performance_history.json` | PPG / minutos de temporadas previas | Derivada |
| 7 | Mock / seed local | Demo inmediata sin secrets | Fallback |

Sin secrets el proyecto **funciona al clonar** (mock + seed).

Sofascore y Comuniate se retiraron: la nota reciente la da FotMob, y la racha de
puntos (`points_streak`) y la señal de chollo (`is_chollo_ext`) se derivan del
propio `streak` y `prev_value` de Mister, sin peticiones extra ni bloqueos 403.

### Enriquecimiento externo (scrapers)

El motor enriquece plantilla y mercado vía `src/external_data.py` + `src/scrapers/`:

- Matching de nombres con `thefuzz` (umbral ≥ 85; desempate por club).
- **Fail-soft**: timeouts cortos, try/except por fuente. Si scrape falla → `src/cache/external_latest.json` (TTL 12h) → `src/external_seed.json`.
- **Ritmo y 429** (`src/scrapers/http_util.py`): un ciclo pide a FutbolFantasy las páginas de equipo, la previa y hasta 48 perfiles **por liga**, así que las peticiones al mismo host van espaciadas (0,4 s en FF), un 429 se reintenta respetando `Retry-After`, y a la tercera el host se da por caído durante 10 minutos y se deja de pedirle nada: seguir insistiendo solo alarga el bloqueo y devuelve datos a medias. El corte queda anotado en `meta.external.rate_limited`.
- Cada mañana el JSON incluye `action_plan[]` (`buy_now` / `wait`+`wait_risk` / `avoid` / `sell`) y el dashboard muestra la **cola del día** con la fase del playbook (ver [docs/daily-playbook.md](docs/daily-playbook.md)).
- Los selectores HTML son frágiles y los sitios tienen ToS propios: úsalo bajo tu responsabilidad; el pipeline de Mister no se tumba si un scraper rompe.

### Motor de jornada

Sobre esa base el pipeline calcula:

- `fdr` / `fdr_label` / `fdr_why` / `opponent_name` / `is_home` (`src/fixture_difficulty.py`): dificultad del rival en escala 1..5. La fuerza de cada equipo encadena clasificación real → prior de calidad de plantilla (valor del pool + media FF previa, disponible desde J1) → puntos fantasy que concede cada equipo por posición. El rival mueve hasta ±22% los puntos esperados y la localía ~5% por lado; nunca se devuelve un neutro plano si hay prior.
- `xpts` / `xpts_floor` / `xpts_why` (`src/expected_points.py`): puntos esperados de la jornada como `p_juega × producción_base × ajuste_fdr`, escalados por el `provider` de la liga.
- `recommended_xi` con **capitán** elegido por `xpts × (multiplicador − 1)` y desempate por probabilidad de jugar y rival, si la liga tiene capitán activo.
- `gw_target_xi`: once de máximo xPts **de todo el pool** (sin filtro de dueño ni caja), cobertura dual vs tu once y contexto de cruce. Es el hero de Hoy.
- `risky_slots[]` y `formation_switch`: huecos del once que un jugador con poca probabilidad de jugar convertiría en cero, y la formación alternativa que los evita.
- `meta.model_calibration` (`src/model_calibration.py`): sesgo y error medio del xPts contra los puntos reales de las jornadas ya cerradas, desglosado por tramo de probabilidad de jugar, más los mayores aciertos y desvíos del último ciclo. El snapshot diario guarda la predicción para poder juzgarla después.
- **Auditoría de rendimiento** (`src/performance_audit.py`): Spearman/lift del ranking, once recomendado vs alineado vs naive de precio, `buy_now` vs `avoid`, y salud del pipeline. Corre en el job diario (job summary) y en el workflow `Performance audit` con umbrales. Ver [docs/performance-audit.md](docs/performance-audit.md).
- Mercado según fase: en `confirmacion` / `visperas` / `dia_partido` el `priority_score_buy` dobla el peso del xPts y reduce a la mitad el de objetivo del board, porque a esas horas se ficha para puntuar el sábado y no para revender en tres semanas.
- `recommendations[]` y `squad_notes[]` desde `src/daily_playbook.py`.

### Motor competitivo

El JSON incluye `recommendations[]` según:

- Tu puesto en la tabla (proteger valor vs remontar)
- Carencias propias vs liquidez/gaps de rivales
- Libres TOP con alto PPG histórico aún sin fichar
- Chollos con track record vs “humo” de mercado

## Estructura

```
.
├── .github/workflows/daily_update.yml
├── .github/workflows/performance_audit.yml
├── workers/refresh-proxy/   # Cloudflare Worker: botón Actualizar → Actions
├── src/
│   ├── data_engine.py
│   ├── performance_audit.py
│   ├── config.py
│   ├── mister_client.py
│   ├── external_data.py
│   ├── external_seed.json
│   ├── scrapers/
│   ├── cache/          # regenerado (gitignored salvo .gitkeep)
│   ├── mock_data.json
│   └── performance_history.json
├── public/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── sw.js
│   ├── refresh-config.example.json
│   ├── manifest.webmanifest
│   ├── icons/
│   └── data/
│       ├── latest_data.json
│       └── history/
├── requirements.txt
├── README.md
└── .gitignore
```

## Uso local

```bash
# 1) Dependencias
pip install -r requirements.txt
# (en Windows, si hace falta: py -3 -m pip install -r requirements.txt)

# 2) Generar JSON
python src/data_engine.py
# o: py -3 src/data_engine.py

# 3) Servir el frontend por HTTP (necesario para fetch + PWA)
cd public
python -m http.server 8080
# Abre http://127.0.0.1:8080
```

> Abrir `index.html` con `file://` no carga el JSON ni registra el service worker.

## Conectar tu cuenta de Mister

Mister autentica las llamadas `/ajax/*` con **cookies + header `x-auth`** (no OAuth público).

### 1) Capturar credenciales (DevTools)

1. Entra en [mister.mundodeportivo.com](https://mister.mundodeportivo.com) e inicia sesión.
2. Abre DevTools → **Network** → filtra **Fetch/XHR**.
3. Recarga o entra al feed / mercado.
4. Abre una petición como **`balance`** (`POST /ajax/balance`).
5. En **Request Headers** copia:

| Dónde | Valor | Secret |
|-------|--------|--------|
| Cookie → `token=` | JWT (`eyJhbGciOi...`) | `MISTER_TOKEN` |
| Header → `x-auth` | hash hex | `MISTER_X_AUTH` |
| Cookie → `PHPSESSID=` | id de sesión | `MISTER_PHPSESSID` |
| Cookie → `refresh-token=` | JWT largo | `MISTER_REFRESH_TOKEN` (opcional) |

También puedes pegar la cookie entera en un solo secret `MISTER_COOKIE`.

> **Seguridad:** el JWT caduca (en tu captura `exp` ~ minutos/horas). Si lo pegaste en un chat, **cierra sesión y vuelve a entrar** para rotarlo. Nunca subas tokens al repo.

### 2) Probar en local (PowerShell)

```powershell
$env:MISTER_TOKEN = "eyJhbGciOiJFUzI1NiJ9...."          # solo el valor de token=, sin "token="
$env:MISTER_X_AUTH = "f51c27cbbbbaf9d1e723fd626556674a" # ejemplo; usa el tuyo nuevo
$env:MISTER_PHPSESSID = "224e51e35da59672affea0d2dbb36e08"
# opcional:
# $env:MISTER_REFRESH_TOKEN = "eyJ...."
# $env:MISTER_LEAGUE_ID = "...."
# $env:MISTER_TEAM_ID = "...."

py -3 src/data_engine.py
```

En el log deberías ver `Mister live OK` o, si un endpoint aún no matchea, fallback a mock. Los paths se prueban en `src/mister_client.py` (`AJAX_CANDIDATES`).

### 3) GitHub Secrets

Repo → **Settings → Secrets and variables → Actions**

| Secret | Obligatorio | Descripción |
|--------|-------------|-------------|
| `MISTER_TOKEN` | Recomendado | JWT de la cookie `token` |
| `MISTER_X_AUTH` | Recomendado | Header `x-auth` |
| `MISTER_PHPSESSID` | Recomendado | Cookie `PHPSESSID` |
| `MISTER_REFRESH_TOKEN` | No | Cookie `refresh-token` |
| `MISTER_COOKIE` | No | Cookie completa (alternativa a lo anterior) |
| `MISTER_LEAGUE_ID` | No | ID de liga (si tu endpoint lo pide) |
| `MISTER_TEAM_ID` | No | ID de equipo |
| `FOOTBALL_API_KEY` | No | [API-Football](https://www.api-football.com/) |

Luego: **Actions → Daily data update → Run workflow**.

### Actualizar desde la PWA (móvil, sin PC)

El botón **Actualizar** del header dispara el mismo workflow en la nube vía un Cloudflare Worker (no expone secrets de Mister).

Guía completa: [`workers/refresh-proxy/README.md`](workers/refresh-proxy/README.md).

Resumen:

1. Despliega el Worker y configura `GITHUB_TOKEN` (PAT Actions write) + `REFRESH_KEY`.
2. Copia `public/refresh-config.example.json` → `public/refresh-config.json` con la URL del Worker y la misma key.
3. Guarda ese archivo solo para pruebas locales. En producción crea el secret
   `REFRESH_CONFIG_JSON` con ese JSON en una sola línea. En la app, pulsa
   **Actualizar** y deja que compruebe durante varios minutos si aparece un
   snapshot nuevo.

También puedes lanzar el workflow a mano con input `league` (`all`, un slug o `id_community`), o vía `repository_dispatch` type `refresh-data`.

### 4) Si solo carga mock

1. Comprueba que el JWT no haya caducado (vuelve a copiar `token` + `x-auth`).
2. En Network, anota las URLs reales de **mercado / plantilla / clasificación** y dime los paths (`/ajax/...`) para añadirlos a `AJAX_CANDIDATES`.
3. Auth OK se confirma si `/ajax/balance` responde 200 con JSON.

## Multi-liga y normas Mister

El motor **descubre** las comunidades de tu cuenta (`_FG_user.communities`) y, por cada una, lee las **normas** (`provider`, `team_limit`, cláusulas, cesiones, ritmo de mercado). Overrides opcionales en `LEAGUE_OVERRIDES` (`src/config.py`) solo enriquecen ligas descubiertas (slug, `season_start`, default); **no reintroducen** una comunidad que ya no sale en Mister.

Tras un sync `--league all` (p. ej. el job diario):

- `public/data/leagues.json` se reescribe solo con las ligas del catálogo actual → el selector de la PWA muestra exactamente esas.
- Se borran carpetas huérfanas bajo `public/data/leagues/<slug>/`.
- Un sync parcial (`--league <slug>`) **conserva** el resto del índice; no limpia abandonadas.

Guía de aprendizaje: [`docs/mister-rules.md`](docs/mister-rules.md).

## Despliegue en GitHub + GitHub Pages

### Qué necesitas

| Pieza | Para qué |
|-------|----------|
| Cuenta GitHub + repo | Código + Pages + Actions |
| Secrets de Mister (`MISTER_TOKEN`, `MISTER_X_AUTH`, `MISTER_PHPSESSID`) | Datos live en la Action |
| Workflow **Deploy GitHub Pages** | Publica la carpeta `public/` |
| Renovar el JWT de vez en cuando | Mister caduca el `token`; sin él la Action falla o cae a mock |

> El JSON de `public/data/` incluye plantilla, saldo y rivales: quien tenga la URL de Pages puede verlo.

### Pasos (una sola vez)

```powershell
# En la carpeta del proyecto
git init
git add .
git commit -m "Initial commit: Mister Fantasy Advisor"

gh auth login          # browser / token
gh repo create Mister --source=. --remote=origin --push
```

Luego en GitHub:

1. **Settings → Pages → Build and deployment**
   - Source: **GitHub Actions** (el workflow `daily_update.yml` genera y despliega `public/` en el mismo run)
2. **Settings → Secrets and variables → Actions** → añade al menos:
   - `MISTER_TOKEN`
   - `MISTER_X_AUTH`
   - `MISTER_PHPSESSID`
   - (opcional) `MISTER_REFRESH_TOKEN`, `MISTER_LEAGUE_ID`, `MISTER_TEAM_ID`
3. **Actions → Daily data update → Run workflow** (primer snapshot live)
4. URL: `https://<usuario>.github.io/Mister/`

Cada mañana la Action de datos regenera el JSON y publica Pages en ese mismo run. El commit del snapshot queda como histórico best-effort y ya no controla el despliegue.

## Operación diaria (automatizada)

```
┌─────────────┐     cron 06:00 UTC      ┌──────────────────┐
│ GitHub      │ ───────────────────────►│ data_engine.py   │
│ Actions     │     (+ Run manual)      │ + secrets Mister │
└─────────────┘                         └────────┬─────────┘
                                                 │ escribe
                                                 ▼
                                        public/data/*.json
                                                 │
                                                 │ git commit + push
                                                 ▼
                                        GitHub Pages (/public)
                                                 │
                                                 ▼
                                        Tú abres la PWA / URL
```

### Tu rutina (2 minutos)

1. **Abrir la URL / PWA** y mirar la **Cola del día** (pujar / cláusula / vender / esperar).
2. Ejecutar en Mister las acciones que te convenzan.
3. Si la Action falla en rojo → casi seguro **token caducado** (paso siguiente).

### Mantener la auth viva (el único mantenimiento real)

Mister no ofrece OAuth público: el JWT de la cookie `token` **caduca**. La Action lo usa tal cual está en Secrets.

Cuando falle el workflow o veas datos viejos/mock:

1. Entra en Mister con el navegador.
2. DevTools → Network → `POST /ajax/balance`.
3. Copia `token`, `x-auth`, `PHPSESSID` (y `refresh-token` si aparece).
4. Actualiza los Secrets en GitHub.
5. **Actions → Daily data update → Run workflow**.

No hace falta tocar código ni lanzar un segundo deploy: el propio workflow diario/manual publica la web al terminar la regeneración.

### Desarrollo local (cuando cambies UI o scrapers)

```powershell
# Cargar .env y regenerar
py -3 src/data_engine.py
cd public; py -3 -m http.server 8080
```

Al iterar, regenera **una sola liga** (`--league laliga-patio`) en vez de las cuatro: encadenar ciclos completos es lo que acaba provocando los 429 de FutbolFantasy, y aunque el pipeline los aguanta, el JSON sale con menos jugadores emparejados.

Sube cambios de código con `git push`. El JSON lo sigue generando la Action, y el workflow auxiliar `Deploy GitHub Pages` queda solo para redeploys manuales puntuales del contenido ya presente en el repo.

### Disparo manual

Útil tras un mercado movido o tras renovar secrets:

**Actions → Daily data update → Run workflow**

Cron: `0 6 * * *` (06:00 UTC ≈ 08:00 en España en verano).

## Instalar en el móvil (PWA)

1. Abre la URL de GitHub Pages en el navegador del móvil.
2. **Android / Chrome:** menú → **Instalar app** (o el banner del dashboard).
3. **iOS / Safari:** Compartir → **Añadir a pantalla de inicio**.

La app abre en modo `standalone` y puede mostrar el último snapshot offline (service worker).

## Dashboard

- **KPIs:** saldo, valor, posición, TOP libres
- **Recomendaciones del día**
- **Pestaña 1:** Mercado (categoría, pujas, prioridad, PPG histórico)
- **Pestaña 2:** Plantilla vs carencias (alertas rojo/ámbar)
- **Pestaña 3:** Libres TOP + liquidez de rivales
- Filtros: búsqueda, posición, precio, forma, disponibilidad

## Extender APIs reales

- **Mister:** cliente en `src/mister_client.py` (cookies `token` + `x-auth` → `/ajax/*`). Añade paths reales en `AJAX_CANDIDATES` cuando los veas en DevTools.
- **API-Football:** con `FOOTBALL_API_KEY` válida se valida la cuota; puedes ampliar `fetch_api_football_enrichment()` para `/players` por temporada.

## Licencia

Uso personal / educativo. Respeta los términos de Mister Fantasy y de api-sports.
