"""Constants for the Taipower AMI integration."""
from datetime import timedelta

DOMAIN = "taipower_ami"

CONF_BASE_URL = "base_url"
DEFAULT_BASE_URL = "http://localhost:8000"

# The Taipower AMI server publishes same-day 15-minute readings with roughly a
# 1.5-2 hour lag; poll every 15 minutes for the freshest slot.
UPDATE_INTERVAL = timedelta(minutes=15)

# Bills change once every two months; refresh the bill summary every N update
# cycles (N * 15 min = 6 h).
BILL_REFRESH_CYCLES = 24

# A cold server start includes launching Camoufox and solving Turnstile.
REQUEST_TIMEOUT = 300

# Days with no data are skipped, so an early default is harmless — it only
# costs fetch time. Configurable per entry via the options flow.
CONF_BACKFILL_START = "backfill_start_date"
DEFAULT_BACKFILL_START = "2024-01-01"
