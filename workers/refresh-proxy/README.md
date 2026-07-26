# Setup: botón «Actualizar» en la PWA (sin PC encendido)
#
# La PWA llama a este Worker → GitHub repository_dispatch → daily_update.yml
# regenera public/data con los secrets MISTER_* ya configurados en Actions.

## 1. PAT de GitHub

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained.
2. Repo: solo `Mister`. Permiso: **Actions → Read and write**.
3. Copia el token (solo se muestra una vez).

## 2. Desplegar el Worker

```bash
cd workers/refresh-proxy
npx --yes wrangler@4 login
npx --yes wrangler@4 secret put GITHUB_TOKEN   # pega el PAT
npx --yes wrangler@4 secret put REFRESH_KEY    # inventa una clave larga
# opcional si el repo no es elexrallen/Mister:
# npx --yes wrangler@4 secret put GITHUB_REPO  # owner/Mister
npx --yes wrangler@4 deploy
```

Anota la URL que imprime Wrangler (`https://mister-refresh.<cuenta>.workers.dev`).

## 3. Config de la PWA

```bash
cp public/refresh-config.example.json public/refresh-config.json
```

Edita `public/refresh-config.json`:

```json
{
  "url": "https://mister-refresh.<cuenta>.workers.dev",
  "key": "<el mismo REFRESH_KEY del Worker>"
}
```

Haz commit y push de `refresh-config.json` (el `key` no es el token de Mister ni el PAT;
solo autoriza disparar CI; el Worker limita a 1 req / 2 min).

## 4. Secrets de Mister en Actions

Igual que el cron diario: `MISTER_TOKEN`, `MISTER_X_AUTH`, etc. en
Repo → Settings → Secrets and variables → Actions.

## 5. Probar

1. Abre la PWA en GitHub Pages.
2. Pulsa **Actualizar** en el chip de la cabecera.
3. En Actions debe aparecer un run disparado por `repository_dispatch` / `refresh-data`.
4. En 2–6 min la hora «Actualizado» cambia sola.

Si falta `refresh-config.json`, el botón solo relee el JSON ya publicado.
