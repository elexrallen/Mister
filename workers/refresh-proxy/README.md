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

Edita `public/refresh-config.json` solo para pruebas locales. Ese archivo debe
quedarse fuera de git; en producción GitHub Pages lo recibe desde un secret:

```json
{
  "url": "https://mister-refresh.<cuenta>.workers.dev",
  "key": "<el mismo REFRESH_KEY del Worker>"
}
```

Para que el botón funcione en **GitHub Pages** sin commitear la key, crea un secret
`REFRESH_CONFIG_JSON` en el repo con ese JSON en una sola línea. El workflow
`daily_update` lo inyecta justo antes de publicar Pages. El workflow
`deploy_pages` queda solo como redeploy manual de apoyo.

El `key` no es el token de Mister ni el PAT; solo autoriza disparar CI
(el Worker limita a 1 req / 2 min).

## 4. Secrets de Mister en Actions

Igual que el cron diario: `MISTER_TOKEN`, `MISTER_X_AUTH`, etc. en
Repo → Settings → Secrets and variables → Actions.

## 5. Probar

1. Abre la PWA en GitHub Pages.
2. Pulsa **Actualizar** en el chip de la cabecera.
3. En Actions debe aparecer un run disparado por `repository_dispatch` / `refresh-data`.
4. La app disparará el workflow y comprobará durante varios minutos si aparece
   un `generated_at` nuevo. Si GitHub Actions está en cola o Pages tarda en
   propagar, puede tardar más de 6 min sin que sea un fallo real.

Si falta `refresh-config.json`, el botón solo relee el JSON ya publicado.
