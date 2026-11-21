# Rudrapur Air

A free, public daily air bulletin for Rudrapur, Uttarakhand, in English and Hindi. Twice a day it:

1. fetches the Copernicus CAMS PM2.5 forecast for Rudrapur and for the Kashipur monitor's location (Open-Meteo, no key),
2. fetches the Kashipur official monitor (UKPCB, Govt Girls Inter College) from OpenAQ,
3. fetches satellite fire detections for North India from NASA FIRMS,
4. corrects Rudrapur's forecast by how far the forecast was off at Kashipur over the last 7 days, and by today's readings,
5. **saves every day to `data/daily.csv`** and every hour to `data/hourly/YYYY-MM.csv`,
6. builds the page in `docs/index.html`.

The archive starts on 10 Oct 2026. Over time it becomes the record you need to show whether anything improved.

## Put it online (one-time, about 15 minutes)

You need a free GitHub account.

1. Create a **public** repository, for example `rudrapur-air`.
2. Upload everything in this folder **except `keys.env`**. It must never go online.
3. Repository **Settings → Secrets and variables → Actions → New repository secret**. Add two:
   - `OPENAQ_KEY`: your OpenAQ key
   - `FIRMS_KEY`: your NASA FIRMS key
4. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
5. **Actions → Update Rudrapur Air → Run workflow.** After about a minute, the page is live at `https://<your-username>.github.io/rudrapur-air/`.
6. Optional: under **Settings → Secrets and variables → Actions → Variables**, add `SITE_URL` with that address. The WhatsApp message will then include the link.

After that it runs by itself at 06:00 and 16:00 IST. GitHub pauses scheduled jobs in a repository with no activity for 60 days; the daily commits count as activity, but if it ever stops, run it once by hand from the Actions tab.

## Run it on your own computer

```
python rudrapur_air.py                       # normal update
python rudrapur_air.py --backfill 2026-10-10 # rebuild the archive from a date
```

Keys are read from environment variables, or from a `keys.env` file in this folder or the folder above it (`OPENAQ_KEY=...`, `FIRMS_KEY=...`). Only the Python standard library is used.

## Files

| Path | What it is |
|---|---|
| `rudrapur_air.py` | Fetches, corrects, archives and renders |
| `template.html` | The page layout and wording (English and Hindi) |
| `data/daily.csv` | One row per day: estimate, category, model, Kashipur monitor, correction factor, fires, night mixing height, wind, rain, dust |
| `data/hourly/` | Hour-by-hour estimate, model and monitor values |
| `docs/` | The published site (page plus a copy of `daily.csv`) |
| `.github/workflows/update.yml` | The twice-daily job |

## Limits

- Rudrapur has no official monitor. The figures are a corrected forecast for the town, about 40 km wide, not a street reading.
- The forecast misses short spikes such as Diwali fireworks, and often overstates dust here.
- If the Kashipur monitor goes quiet, the last good correction is reused.
- Categories follow India's CPCB AQI bands for 24-hour PM2.5; the hourly strip uses the same bands as a guide.
