"""The Taipower AMI integration."""
from __future__ import annotations

from datetime import date

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import CONF_BACKFILL_START, DEFAULT_BACKFILL_START, DOMAIN
from .coordinator import TaipowerAmiCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_BACKFILL = "backfill"
BACKFILL_SCHEMA = vol.Schema(
    {vol.Optional("start_date", default=DEFAULT_BACKFILL_START): cv.date}
)


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Saving the options form starts a backfill from the chosen date."""
    start = entry.options.get(CONF_BACKFILL_START)
    coordinator: TaipowerAmiCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if start and coordinator:
        entry.async_create_background_task(
            hass,
            coordinator.async_backfill(date.fromisoformat(start)),
            name=f"{DOMAIN}_backfill_options",
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = TaipowerAmiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_backfill(call: ServiceCall) -> None:
        start = call.data["start_date"]
        if isinstance(start, str):
            start = date.fromisoformat(start)
        for coord in hass.data[DOMAIN].values():
            entry.async_create_background_task(
                hass, coord.async_backfill(start), name=f"{DOMAIN}_backfill"
            )

    if not hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        hass.services.async_register(
            DOMAIN, SERVICE_BACKFILL, handle_backfill, schema=BACKFILL_SCHEMA
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
