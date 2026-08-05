# Taipower AMI add-on

Runs the Taipower AMI scraper HTTP API inside Home Assistant OS. It logs in to
the Taipower e-service portal with the Camoufox stealth browser (solving the
Cloudflare Turnstile automatically), keeps the session alive, and exposes:

- `GET /health` — liveness check
- `GET /customers` — every AMI electricity number (電號) on the account,
  discovered automatically
- `GET /fetch/latest` — today's + yesterday's 15-minute readings for every
  number (same-day data lags roughly 1.5–2 hours)
- `GET /fetch/history` — 15-minute readings for a date range (max 45
  days/call), for statistics backfills
- `GET /bill/summary` — current bill, full detail, and history per number
- `POST /fetch/15min` / `POST /fetch/15min/range` — file-writing fetches

## Requirements

**amd64 (x86-64) hardware only.** The Turnstile-Solver base image is not built
for ARM, so this add-on will not run on a Raspberry Pi.

## Configuration

| Option | Description |
| --- | --- |
| `username` | Taipower e-service account |
| `password` | Taipower e-service password |

Electricity numbers are not configured — every AMI number registered on the
account is discovered and served automatically.

## Pairing with the integration

Install the `taipower_ami` custom integration and point it at
`http://localhost:8000` (same host) or `http://<ha-ip>:8000`.
