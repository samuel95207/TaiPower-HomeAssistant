"""Config flow for the Taipower AMI integration."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BACKFILL_START,
    CONF_BASE_URL,
    DEFAULT_BACKFILL_START,
    DEFAULT_BASE_URL,
    DOMAIN,
)

DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str}
)


class TaipowerAmiOptionsFlow(OptionsFlow):
    """Options: pick a backfill start date; saving starts the backfill."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(
            CONF_BACKFILL_START, DEFAULT_BACKFILL_START
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKFILL_START, default=current
                    ): selector.DateSelector()
                }
            ),
        )


class TaipowerAmiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TaipowerAmiOptionsFlow:
        return TaipowerAmiOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200 or (await resp.json()).get("status") != "ok":
                        errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title="Taipower AMI",
                    data={CONF_BASE_URL: base_url},
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
