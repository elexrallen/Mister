# Mister Fantasy Advisor

Dashboard Jamstack + PWA para tomar mejores decisiones diarias en tu liga privada de **Mister Fantasy** (Mundo Deportivo). Cruza tu plantilla, mercado, rivales e **histórico de rendimiento multi-temporada** y genera recomendaciones competitivas.

## Qué incluye

- **Pipeline Python** (`src/data_engine.py`) que materializa `public/data/latest_data.json`
- **GitHub Action** diaria (07:00 UTC) + disparo manual
- **Dashboard estático** (HTML + Tailwind CDN + JS) en `/public`, listo para **GitHub Pages**
- **PWA instalable** en el móvil (manifest + service worker)

## Fuentes de datos (5 capas)

| Capa | Fuente | Qué aporta |
|------|--------|------------|
| 1 | Mister Fantasy (`MISTER_TOKEN`) o `src/mock_data.json` | Mercado, plantilla, rivales, saldos |
| 2 | `public/data/history/YYYY-MM-DD.json` | Δvalor, chollos, trading 3–5 días, liquidez |
| 3 | API-Football (`FOOTBALL_API_KEY`) o `src/performance_history.json` | PPG / minutos / fiabilidad de **temporadas previas** |
| 4 | **Externas Fantasy** (scrapers) | Estado, % titular, chollos, nota Sofascore best-effort |
| 5 | Mock / seed local | Demo inmediata sin secrets |

Sin secrets el proyecto **funciona al clonar** (mock + seed).

### Fuentes externas (scrapers)

El motor enriquece plantilla y mercado vía `src/external_data.py` + `src/scrapers/`:

| Prioridad | Fuente | Uso |
|-----------|--------|-----|
| Titularidad / estado | **Fútbol Fantasy** (primario) + **Jornada Perfecta** (refuerzo lesiones/dudas) | `availability`, `lineup_prob_ext`, chollos/recos |
| Nota / racha | **Comuniate** (fichas → `id_sofascore` + media) + **Sofascore API** (best-effort; a menudo 403 → `partial` vía Comuniate) | `sofascore_avg_5`, `points_streak` |
| Plantillas rivales | Mister `/users/{id}/…` | gaps, demanda de puja, `wait_risk` |
| Libres | Mister best-effort; si no hay señal clara → lista vacía (honesto) | `free_agents_top` / nota en UI |

- Matching de nombres con `thefuzz` (umbral ≥ 85; desempate por club).
- **Fail-soft**: timeouts cortos, try/except por fuente. Si scrape falla → `src/cache/external_latest.json` (TTL 12h) → `src/external_seed.json`.
- Cada mañana el JSON incluye `action_plan[]` (`buy_now` / `wait`+`wait_risk` / `avoid` / `sell`) y el dashboard muestra la **cola del día**.
- Los selectores HTML son frágiles y los sitios tienen ToS propios: úsalo bajo tu responsabilidad; el pipeline de Mister no se tumba si un scraper rompe.

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
├── src/
│   ├── data_engine.py
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

### 4) Si solo carga mock

1. Comprueba que el JWT no haya caducado (vuelve a copiar `token` + `x-auth`).
2. En Network, anota las URLs reales de **mercado / plantilla / clasificación** y dime los paths (`/ajax/...`) para añadirlos a `AJAX_CANDIDATES`.
3. Auth OK se confirma si `/ajax/balance` responde 200 con JSON.

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
   - Source: **GitHub Actions** (el workflow `deploy_pages.yml` despliega `public/`)
2. **Settings → Secrets and variables → Actions** → añade al menos:
   - `MISTER_TOKEN`
   - `MISTER_X_AUTH`
   - `MISTER_PHPSESSID`
   - (opcional) `MISTER_REFRESH_TOKEN`, `MISTER_LEAGUE_ID`, `MISTER_TEAM_ID`
3. **Actions → Daily data update → Run workflow** (primer snapshot live)
4. URL: `https://<usuario>.github.io/Mister/`

Cada mañana la Action de datos regenera el JSON y hace push; el workflow de Pages vuelve a publicar el sitio.

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

No hace falta tocar código ni redesplegar Pages a mano: el push del JSON ya actualiza la web.

### Desarrollo local (cuando cambies UI o scrapers)

```powershell
# Cargar .env y regenerar
py -3 src/data_engine.py
cd public; py -3 -m http.server 8080
```

Sube cambios de código con `git push`. El JSON lo sigue generando la Action (o tú en local y lo commiteas si quieres forzar un snapshot).

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
