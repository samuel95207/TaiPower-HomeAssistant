"""Sensors for the Taipower AMI integration — one device per 電號."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfMass, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TaipowerAmiCoordinator


def _cust(data: dict, custno: str) -> dict:
    return (data.get("customers") or {}).get(custno) or {}


def _bill(data: dict, custno: str) -> dict:
    return (data.get("bills") or {}).get(custno) or {}


def _current_bill(data: dict, custno: str) -> dict:
    return _bill(data, custno).get("current_bill") or {}


def _detail(data: dict, custno: str) -> dict:
    return _bill(data, custno).get("detail") or {}


def _details(data: dict, custno: str) -> dict:
    """All known bill details, keyed by bill month."""
    return _bill(data, custno).get("details") or {}


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True, kw_only=True)
class TaipowerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict, str], Any] = lambda data, custno: None
    attrs_fn: Callable[[dict, str], dict[str, Any]] | None = None


SENSORS: tuple[TaipowerSensorDescription, ...] = (
    TaipowerSensorDescription(
        key="current_power",
        name="Current power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d, n: (_cust(d, n).get("latest") or {}).get("power"),
        attrs_fn=lambda d, n: {
            "reading_date": (_cust(d, n).get("latest") or {}).get("date"),
            "reading_time": (_cust(d, n).get("latest") or {}).get("time"),
        },
    ),
    TaipowerSensorDescription(
        key="energy_today",
        name="Energy today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d, n: (_cust(d, n).get("today") or {}).get("kwh"),
    ),
    TaipowerSensorDescription(
        key="energy_yesterday",
        name="Energy yesterday",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        value_fn=lambda d, n: (_cust(d, n).get("yesterday") or {}).get("kwh"),
    ),
    TaipowerSensorDescription(
        key="bill_amount_due",
        name="Bill amount due",
        native_unit_of_measurement="TWD",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d, n: _current_bill(d, n).get("amount_due"),
        attrs_fn=lambda d, n: {
            "bill_month": _current_bill(d, n).get("bill_month"),
            "energy_charge": _detail(d, n).get("energy_charge"),
            "public_facility_charge": _detail(d, n).get("public_facility_charge"),
            "ebill_discount": _detail(d, n).get("ebill_discount"),
            # Past bills: amount, status and (where known) kWh and unit price.
            "history": [
                {
                    **row,
                    "billed_kwh": (_details(d, n).get(row["bill_month"]) or {}).get("billed_kwh"),
                    "avg_price_per_kwh": (
                        _details(d, n).get(row["bill_month"]) or {}
                    ).get("avg_price_per_kwh"),
                }
                for row in _bill(d, n).get("history", [])
            ],
        },
    ),
    TaipowerSensorDescription(
        key="bill_payment_status",
        name="Bill payment status",
        value_fn=lambda d, n: _current_bill(d, n).get("payment_status"),
    ),
    TaipowerSensorDescription(
        key="bill_due_date",
        name="Bill due date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d, n: _parse_date(_current_bill(d, n).get("due_date")),
    ),
    TaipowerSensorDescription(
        key="bill_billed_kwh",
        name="Bill billed energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d, n: _detail(d, n).get("billed_kwh"),
        attrs_fn=lambda d, n: {
            "billing_period_start": _detail(d, n).get("billing_period_start"),
            "billing_period_end": _detail(d, n).get("billing_period_end"),
            "comparison": _detail(d, n).get("comparison"),
            "same_building_avg_kwh": _detail(d, n).get("same_building_avg_kwh"),
        },
    ),
    TaipowerSensorDescription(
        key="bill_avg_price",
        name="Bill average price",
        native_unit_of_measurement="TWD/kWh",
        suggested_display_precision=2,
        value_fn=lambda d, n: _detail(d, n).get("avg_price_per_kwh"),
    ),
    TaipowerSensorDescription(
        key="bill_carbon",
        name="Bill carbon emissions",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        value_fn=lambda d, n: _detail(d, n).get("carbon_kg"),
    ),
    TaipowerSensorDescription(
        key="next_meter_read_date",
        name="Next meter read date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d, n: _parse_date(_detail(d, n).get("next_meter_read_date")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TaipowerAmiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TaipowerAmiSensor(coordinator, custno, description)
        for custno in coordinator.customer_numbers
        for description in SENSORS
    )


class TaipowerAmiSensor(CoordinatorEntity[TaipowerAmiCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TaipowerAmiCoordinator,
        custno: str,
        description: TaipowerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description: TaipowerSensorDescription = description
        self._custno = custno
        self._attr_unique_id = f"{custno}_{description.key}"
        alias = None
        if coordinator.data:
            alias = _cust(coordinator.data, custno).get("alias")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, custno)},
            name=f"Taipower AMI {alias or custno}",
            manufacturer="Taipower",
            model="AMI smart meter",
            serial_number=custno,
            configuration_url=coordinator.base_url,
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data, self._custno)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data, self._custno)

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        # Bill sensors stay unavailable until bill data has been fetched once.
        if self.entity_description.key.startswith(("bill_", "next_meter")):
            return bool(_bill(self.coordinator.data, self._custno))
        return self._custno in (self.coordinator.data.get("customers") or {})
