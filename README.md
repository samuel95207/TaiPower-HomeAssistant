# Taipower AMI scraper with Turnstile automation

Log in to [Taipower ebpps2](https://service.taipower.com.tw/ebpps2/login) and
download AMI electricity-usage data — 15-minute readings (same-day, ~2 h
lag), bills, and history — then feed it all into **Home Assistant**: a
per-電號 device with sensors, delay-corrected Energy-dashboard statistics,
and full history backfill. Ships as an HA add-on + HACS integration (see
[Home Assistant](#home-assistant)).

The login page is protected by Cloudflare Turnstile. This project logs in
**automatically** using the [Camoufox](https://camoufox.com/) stealth browser
and keeps one session alive across requests.

## Project layout

```
.
├── src/
│   ├── taipower_ami/           # Reusable Python package
│   │   ├── __init__.py
│   │   ├── auth.py             # Login / Camoufox session helpers
│   │   ├── scraper.py          # Dashboard scraping helpers
│   │   ├── fetcher.py          # 15-minute data fetcher (dynamic enkey)
│   │   ├── bills.py            # e-bill overview/detail/history scraper
│   │   ├── session.py          # Persistent browser session worker
│   │   ├── api.py              # FastAPI HTTP server
│   │   └── client.py           # Python client to call the API
│   ├── run_scraper.py          # CLI dashboard scraper
│   └── run_fetcher.py          # CLI 15-minute fetcher
├── docker/
│   ├── Dockerfile              # Camoufox image (amd64 + aarch64)
│   ├── entrypoint.sh
│   └── docker-compose.yml
├── custom_components/
│   └── taipower_ami/           # Home Assistant custom integration (HACS)
├── ha-addon/
│   └── taipower_ami/           # Home Assistant add-on (amd64 + aarch64)
├── brands/
│   └── custom_integrations/    # Logo assets ready for home-assistant/brands
├── hacs.json                   # HACS metadata
├── repository.yaml             # HA add-on repository metadata
├── .env                        # Credentials (not committed)
├── .gitignore
├── pyproject.toml
├── Pipfile
└── README.md
```

## Quick start (Docker — recommended)

A Docker image with Camoufox preinstalled is the easiest way to run this
headlessly. It builds for amd64 and aarch64.

### Build and run with Docker Compose

```bash
# 1. Make sure .env exists with USER and PASSWORD.
# 2. Build and start the scraper container.
docker compose -f docker/docker-compose.yml up --build

# Results appear in ./data.
```

### Run the HTTP API server

```bash
docker compose -f docker/docker-compose.yml up --build -d
# Override the command to start the API server:
docker compose -f docker/docker-compose.yml run --rm \
  -p 8000:8000 \
  taipower-ami \
  uvicorn taipower_ami.api:app --host 0.0.0.0 --port 8000
```

Then call it from another Python script on your host:

```python
from taipower_ami.client import TaipowerClient

client = TaipowerClient("http://localhost:8000")
print(client.health())
print(client.fetch_15min("2026-08-03"))
```

### Manual Docker run

```bash
docker build -f docker/Dockerfile -t taipower-ami .

# Dashboard scraper
docker run --rm \
  -v "$PWD/.env:/app/.env:ro" \
  -v "$PWD/data:/app/data" \
  taipower-ami

# 15-minute fetcher
docker run --rm \
  -v "$PWD/.env:/app/.env:ro" \
  -v "$PWD/data:/app/data" \
  taipower-ami \
  python3 src/run_fetcher.py --date 2026-08-03
```

## Usage as a Python package

You can import the package directly from `src/`:

```python
from datetime import date
from taipower_ami.auth import CamoufoxSession, load_credentials, login
from taipower_ami.fetcher import fetch_15min_api

user, password = load_credentials()
with CamoufoxSession(headless=True) as cf:
    page = cf.context.new_page()
    if login(page, user, password):
        points = fetch_15min_api(page, date(2026, 8, 3))
        print(len(points), "15-min readings")
```

## Local run (macOS)

You can also run the scripts directly, but automatic Turnstile solving needs
Camoufox installed; without it the scraper opens a visible browser for you to
tick the checkbox yourself.

### Requirements

- Python 3.10+
- `pipenv` or a virtualenv with `playwright`, `python-dotenv`, and `camoufox`

### Install

```bash
pipenv install
pipenv run playwright install chromium
```

### Run the main dashboard scraper

```bash
pipenv run python src/run_scraper.py --browser-type camoufox
```

If automatic solving fails, the script falls back to an interactive browser
window where you click the Turnstile checkbox yourself.

### Run with Camoufox locally

```bash
pipenv run pip install camoufox
pipenv run camoufox fetch
pipenv run python src/run_scraper.py --browser-type camoufox
```

## Fetch 15-minute data for a specific day

Use [src/run_fetcher.py](src/run_fetcher.py). It logs in with Camoufox and calls
Taipower's internal `fifteenlist` API.

```bash
# Yesterday (default)
docker run --rm \
  -v "$PWD/.env:/app/.env:ro" \
  -v "$PWD/data:/app/data" \
  taipower-ami \
  python3 src/run_fetcher.py

# Specific date
docker run --rm \
  -v "$PWD/.env:/app/.env:ro" \
  -v "$PWD/data:/app/data" \
  taipower-ami \
  python3 src/run_fetcher.py --date 2026-08-03
```

Output: `data/15min_YYYY-MM-DD.json` with 96 15-minute power readings.

## Credentials

Create a `.env` file in the project root:

```dotenv
USER="your_taipower_username"
PASSWORD="your_taipower_password"
```

Electricity numbers (電號) are **not** configured — every AMI number on the
account is discovered automatically (`GET /customers`).

## CLI options

### run_scraper.py

| Flag | Description |
|------|-------------|
| `--setup` | Force a visible browser login flow and save the session. |
| `--browser-type camoufox` | Use Camoufox for automatic Turnstile solving. |
| `--browser-channel chrome` | Use a system Google Chrome with Playwright. |
| `--no-persistent` | Use a fresh browser context each run. |
| `--no-interactive-fallback` | Exit if automated login fails instead of opening a visible browser. |
| `--out DIR` | Output directory (default: `data`). |

### run_fetcher.py

| Flag | Description |
|------|-------------|
| `--date YYYY-MM-DD` | Date to fetch (default: yesterday). |
| `--out DIR` | Output directory (default: `data`). |

## HTTP API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check. |
| GET | `/customers` | Every AMI electricity number (電號) on the account, auto-discovered. |
| GET | `/fetch/latest` | Today's + yesterday's 15-min data for **every** number, latest reading, kWh totals. Designed for frequent polling; same-day data lags ~1.5–2 h. |
| GET | `/fetch/history` | 15-min data for a date range (max 45 days/call), for backfills. `?start_date=&end_date=&electricity_number=`. |
| GET | `/bill/summary` | Bills per number: current, full detail (charges, billed kWh, dates, comparison) and history. Cached 6 h; `?refresh=true` forces a re-scrape. |
| POST | `/scrape/dashboard` | Log in and scrape dashboard summary. |
| POST | `/fetch/15min` | Fetch 15-minute data for one day (writes a file). |
| POST | `/fetch/15min/range` | Fetch 15-minute data across a date range (writes files). |

The server keeps one persistent Camoufox session alive on a dedicated worker
thread: only the first request (or an expired session) pays the
Turnstile-solving cost, so frequent polling stays cheap. The AMI `enkey`
rotates, so it is rediscovered from the logged-in navigation on every fetch.

Example:

```bash
curl http://localhost:8000/fetch/latest
curl -X POST http://localhost:8000/fetch/15min \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-08-03"}'
```

## Home Assistant

Two pieces, both installable from this GitHub repository through the HA GUI —
no SSH needed. The **add-on** runs the scraper server on the HA machine; the
**integration** turns its data into devices, sensors, and Energy-dashboard
statistics.

### 1. Install the add-on (the server) — amd64 and aarch64

1. **Settings → Add-ons → Add-on store**
2. ⋮ (top-right) → **Repositories** → add
   `https://github.com/samuel95207/TaiPower-HomeAssistant`
3. The store now shows a **Taipower AMI Add-ons** section → open
   **Taipower AMI** → **Install** (first build downloads the browser image,
   takes a few minutes)
4. **Configuration** tab → set `username` / `password` (your Taipower
   e-service login — nothing else; electricity numbers are discovered
   automatically) → **Save**
5. **Info** tab → **Start** (enable *Start on boot* and *Watchdog*)

The API listens on port 8000. The add-on builds from source on **amd64 and
aarch64** (x86 boxes, ARM NUCs, Raspberry Pi 4/5 with 64-bit HA OS) — it
uses Camoufox directly instead of the upstream amd64-only
`theyka/turnstile_solver` image (which pins
`google-chrome-stable_current_amd64.deb`). Alternative: run the same server
as a plain Docker container anywhere (`./run_server.sh`) and point the
integration at that URL.

### 2. Install the integration — via HACS

1. **HACS → ⋮ → Custom repositories** → add
   `https://github.com/samuel95207/TaiPower-HomeAssistant`, type
   **Integration**
2. Search "Taipower AMI" in HACS → **Download**, then restart Home Assistant
3. **Settings → Devices & services → Add integration** → "Taipower AMI"
4. Server URL: `http://localhost:8000` when using the add-on (or the Docker
   host's URL)

Without HACS: copy `custom_components/taipower_ami/` into
`<ha-config>/custom_components/` and restart.

### What you get

- **One device per electricity number (電號)** — every AMI number on the
  account is discovered automatically; each gets its own device with ~10
  sensors: current power (≈2 h behind real time — Taipower's publication lag;
  the true reading time is in the `reading_date`/`reading_time` attributes),
  energy today/yesterday, bill amount due, payment status, due date, billed
  kWh, average price, carbon emissions, next meter-read date.
- **Two statistics per number, backdated to true reading times** so the ~2 h
  lag never lands data in the wrong hour:
  - `taipower_ami:<電號>_energy` — hourly kWh, Energy-dashboard ready
    (Settings → Dashboards → Energy → **Add consumption**)
  - `taipower_ami:<電號>_power` — hourly mean/min/max kW, for
    statistics-graph cards (a normal power sensor can't be backdated)
- **Self-healing statistics** — every 15-minute poll rewrites today's and
  yesterday's hours rather than only appending new ones, so an hour that was
  skipped earlier (Taipower had not published all four of its 15-minute slots
  yet, or a fetch failed) is corrected as soon as the data arrives. Sums are
  re-anchored on the last row before the window, so the running total stays
  one continuous chain. Gaps older than the live window (e.g. HA was off for
  days) are detected and re-fetched automatically.
- **History backfill** — Settings → Devices & services → Taipower AMI →
  **Configure** → pick a start date → saving fetches all available 15-minute
  history and rebuilds both statistics (a notification appears when done).
  Also available as the `taipower_ami.backfill` service. Data exists from the
  AMI meter's installation date; earlier days are skipped automatically.
  Expect ~1–2 s per fetched day.
- **Updates through the GUI** — new versions pushed to this repo appear in
  the add-on store and HACS like any other update.

### Logo

The add-on shows the Taipower logo out of the box. The integration's logo
(also used by HACS) must come from
[home-assistant/brands](https://github.com/home-assistant/brands) — submit
the ready-made assets in `brands/custom_integrations/taipower_ami/` there as
a PR under `custom_integrations/taipower_ami/`.

## Troubleshooting

### "機器人驗證錯誤" / login rejected

Turnstile must be solved in the *same* browser context that submits the login
form — Cloudflare binds the challenge to the solving context, so tokens minted
elsewhere (e.g. by a standalone solver service) are rejected. Camoufox does
this correctly; logins succeed on roughly 7 of 8 first attempts and `login()`
retries three times.

### Persistent profile lock

If you see a `ProcessSingleton` error because Chrome is already running, use
`--no-persistent` to skip the saved profile.

### macOS sandboxed terminal blocks browser downloads

If local browser downloads fail inside the VS Code sandbox, use the Docker
workflow above instead.
