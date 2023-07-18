"""Support for generic water heater units."""
import logging

from homeassistant.components.water_heater import (
    SUPPORT_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    WaterHeaterEntity,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    TEMP_FAHRENHEIT,
)
from homeassistant.core import DOMAIN as HA_DOMAIN, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

try:
    from homeassistant.util.unit_conversion import TemperatureConverter as convert
except ImportError or ModuleNotFoundError:
    from homeassistant.util.temperature import convert as convert

from . import CONF_HEATER, CONF_SENSOR, CONF_TARGET_TEMP, CONF_TEMP_DELTA, CONF_TEMP_MIN, CONF_TEMP_MAX

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS_HEATER = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE
DEFAULT_NAME = "Generic Water Heater"


async def async_setup_platform(
    hass, hass_config, async_add_entities, discovery_info=None
):
    """Set up the generic water_heater devices."""
    entities = []

    for config in discovery_info:
        name = config[CONF_NAME]
        heater_entity_id = config[CONF_HEATER]
        sensor_entity_id = config[CONF_SENSOR]
        target_temp = config.get(CONF_TARGET_TEMP)
        temp_delta = config.get(CONF_TEMP_DELTA)
        min_temp = config.get(CONF_TEMP_MIN)
        max_temp = config.get(CONF_TEMP_MAX)
        unit = hass.config.units.temperature_unit

        entities.append(
            GenericWaterHeater(
                name, heater_entity_id, sensor_entity_id, target_temp, temp_delta, min_temp, max_temp, unit
            )
        )

    async_add_entities(entities)


class GenericWaterHeater(WaterHeaterEntity, RestoreEntity):
    """Representation of a generic water_heater device."""

    def __init__(
        self, name, heater_entity_id, sensor_entity_id, target_temp, temp_delta, min_temp, max_temp, unit
    ):
        """Initialize the water_heater device."""
        self._attr_name = name
        self.heater_entity_id = heater_entity_id
        self.sensor_entity_id = sensor_entity_id
        self._support_flags = SUPPORT_FLAGS_HEATER
        self._target_temperature = target_temp
        self._temperature_delta = temp_delta
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._unit_of_measurement = unit
        self._current_operation = STATE_ON
        self._current_temperature = None
        self._operation_list = [
            STATE_ON,
            STATE_OFF,
        ]
        self._attr_available = False
        self._attr_should_poll = False

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def current_temperature(self):
        """Return current temperature."""
        return self._current_temperature

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_operation(self):
        """Return current operation ie. on, off."""
        return self._current_operation

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return self._operation_list

    @property
    def min_temp(self):
        """Return the minimum targetable temperature."""
        """If the min temperature is not set on the config, returns the HA default for Water Heaters."""
        if not self._min_temp:
            self._min_temp = convert(DEFAULT_MIN_TEMP, TEMP_FAHRENHEIT, self._unit_of_measurement)
        return self._min_temp

    @property
    def max_temp(self):
        """Return the maximum targetable temperature."""
        """If the max temperature is not set on the config, returns the HA default for Water Heaters."""
        if not self._max_temp:
            self._max_temp = convert(DEFAULT_MAX_TEMP, TEMP_FAHRENHEIT, self._unit_of_measurement)
        return self._max_temp

    async def async_set_temperature(self, **kwargs):
        """Set new target temperatures."""
        self._target_temperature = kwargs.get(ATTR_TEMPERATURE)
        await self._async_control_heating()

    async def async_set_operation_mode(self, operation_mode):
        """Set new operation mode."""
        self._current_operation = operation_mode
        await self._async_control_heating()

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.sensor_entity_id], self._async_sensor_changed
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.heater_entity_id], self._async_switch_changed
            )
        )

        old_state = await self.async_get_last_state()
        if old_state is not None:
            if old_state.attributes.get(ATTR_TEMPERATURE) is not None:
                self._target_temperature = float(old_state.attributes.get(ATTR_TEMPERATURE))
            self._current_operation = old_state.state

        temp_sensor = self.hass.states.get(self.sensor_entity_id)
        if temp_sensor and temp_sensor.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            self._current_temperature = float(temp_sensor.state)

        heater_switch = self.hass.states.get(self.heater_entity_id)
        if heater_switch and heater_switch.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            self._attr_available = True
        self.async_write_ha_state()

    async def _async_sensor_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # Failsafe
            _LOGGER.warning(
                "No Temperature information, entering Failsafe, turning off heater %s",
                self.heater_entity_id,
            )
            await self._async_heater_turn_off()
            self._current_temperature = None
        else:
            self._current_temperature = float(new_state.state)

        await self._async_control_heating()

    @callback
    def _async_switch_changed(self, event):
        """Handle heater switch state changes."""
        new_state = event.data.get("new_state")
        _LOGGER.debug(f"New switch state = {new_state}")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._attr_available = False
        else:
            self._attr_available = True
            _LOGGER.debug("%s became Available", self.name)
            if new_state.state == STATE_ON and self._current_operation == STATE_OFF:
                self._current_operation = STATE_ON
                _LOGGER.debug("STATE_ON")
            elif new_state.state == STATE_OFF and self._current_operation == STATE_ON:
                self._current_operation = STATE_OFF
                _LOGGER.debug("STATE_OFF")

        self.async_write_ha_state()

    async def _async_control_heating(self):
        """Check if we need to turn heating on or off."""
        if self._current_temperature is None:
            pass
        elif self._current_operation == STATE_OFF:
            await self._async_heater_turn_off()
        elif (
            abs(self._current_temperature - self._target_temperature) > self._temperature_delta
        ):
            if self._current_temperature < self._target_temperature:
                await self._async_heater_turn_on()
            else:
                await self._async_heater_turn_off()
        self.async_write_ha_state()

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on."""
        heater = self.hass.states.get(self.heater_entity_id)
        if heater is None or heater.state == STATE_ON:
            return

        _LOGGER.debug("Turning on heater %s", self.heater_entity_id)
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(
            HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
        )

    async def _async_heater_turn_off(self):
        """Turn heater toggleable device off."""
        heater = self.hass.states.get(self.heater_entity_id)
        if heater is None or heater.state == STATE_OFF:
            return

        _LOGGER.debug("Turning off heater %s", self.heater_entity_id)
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(
            HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
        )
