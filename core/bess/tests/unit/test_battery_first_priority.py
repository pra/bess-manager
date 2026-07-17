"""Tests for battery_first_priority: the opt-in mode that routes solar into
the battery ahead of home load, instead of only the post-home surplus.

Sourced from the battery_first_priority sensor (see ha_api_controller.py's
get_battery_first_priority_active), threaded through the DP as a plain bool
parameter -- these tests exercise that parameter directly.
"""

import pytest

from core.bess.decision_intelligence import classify_strategic_intent
from core.bess.dp_battery_algorithm import (
    _build_period_data,
    _state_transition,
    optimize_battery_schedule,
)
from core.bess.inverter_controller import InverterController
from core.bess.models import EnergyData
from core.bess.simulation.inverter_simulator import derive_control_command
from core.bess.simulation.verification import verify_plan_faithfulness
from core.bess.tests.helpers import (
    get_intent_distribution,
    make_battery_settings,
)

DT = 0.25
PRICES_BUY = [1.0]
PRICES_SELL = [0.8]


def test_idle_claims_solar_ahead_of_home_when_priority_active():
    """IDLE (passive) charging: with battery_first_priority, the battery's
    solar claim is not netted against home consumption first.

    solar=3.0, home=4.0, rate_throughput=2.5 kWh (max_charge=10kW*0.25h).
    Default: surplus=max(0,3-4)=0 -> no passive charging at all.
    Priority: battery claims min(solar=3.0, rate=2.5, room)=2.5 kWh.
    """
    bs = make_battery_settings(max_charge_power_kw=10.0, efficiency_charge=1.0)

    next_soe_default = _state_transition(
        5.0, 0.0, bs, DT, solar_production=3.0, home_consumption=4.0
    )
    assert next_soe_default == 5.0  # no solar left over for the battery

    next_soe_priority = _state_transition(
        5.0,
        0.0,
        bs,
        DT,
        solar_production=3.0,
        home_consumption=4.0,
        battery_first_priority=True,
    )
    assert round(next_soe_priority - 5.0, 4) == 2.5


def test_reward_and_flows_reflect_home_drawing_more_grid():
    """Under battery_first_priority, home's shortfall (no longer covered by
    solar the battery claimed) must show up as grid_to_home, not vanish."""
    bs = make_battery_settings(max_charge_power_kw=10.0, efficiency_charge=1.0)

    next_soe = _state_transition(
        5.0,
        0.0,
        bs,
        DT,
        solar_production=3.0,
        home_consumption=4.0,
        battery_first_priority=True,
    )
    period_data = _build_period_data(
        power=0.0,
        soe=5.0,
        next_soe=next_soe,
        period=0,
        home_consumption=4.0,
        battery_settings=bs,
        dt=DT,
        buy_price=PRICES_BUY,
        sell_price=PRICES_SELL,
        solar_production=3.0,
        new_cost_basis=bs.cycle_cost_per_kwh,
        currency="SEK",
        battery_first_priority=True,
    )
    energy = period_data.energy
    # Battery is rate/room-capped at 2.5 kWh (10kW*0.25h), not all 3.0 kWh of
    # solar -- the leftover 0.5 kWh solar covers part of home's 4.0 kWh, the
    # rest (3.5 kWh) comes from grid.
    assert round(energy.battery_charged, 4) == 2.5
    assert round(energy.solar_to_battery, 4) == 2.5
    assert round(energy.solar_to_home, 4) == 0.5
    assert round(energy.grid_to_home, 4) == 3.5
    assert round(energy.grid_to_battery, 4) == 0.0


def test_default_behavior_unchanged_without_flag():
    """battery_first_priority defaults to False -- existing home-first
    behavior must be bit-for-bit unchanged when the flag is omitted."""
    bs = make_battery_settings(max_charge_power_kw=10.0, efficiency_charge=1.0)
    next_soe_a = _state_transition(
        5.0, 0.0, bs, DT, solar_production=1.5, home_consumption=0.1
    )
    next_soe_b = _state_transition(
        5.0,
        0.0,
        bs,
        DT,
        solar_production=1.5,
        home_consumption=0.1,
        battery_first_priority=False,
    )
    assert next_soe_a == next_soe_b


def test_classify_intent_solar_storage_priority():
    """grid_to_home > 0 concurrent with battery charging is only physically
    possible under battery_first_priority -- classify_strategic_intent must
    label it SOLAR_STORAGE_PRIORITY, not SOLAR_STORAGE."""
    ed = EnergyData(
        solar_production=3.0,
        home_consumption=4.0,
        battery_charged=2.5,
        battery_discharged=0.0,
        grid_imported=4.0,
        grid_exported=0.0,
        battery_soe_start=5.0,
        battery_soe_end=7.5,
        battery_first_priority=True,
    )
    assert classify_strategic_intent(0.0, ed) == "SOLAR_STORAGE_PRIORITY"


def test_classify_intent_plain_solar_storage_without_grid_to_home():
    """Same battery_charged, but home fully covered by solar (no
    battery_first_priority in effect) -- must stay plain SOLAR_STORAGE."""
    ed = EnergyData(
        solar_production=5.0,
        home_consumption=1.0,
        battery_charged=2.5,
        battery_discharged=0.0,
        grid_imported=0.0,
        grid_exported=1.5,
        battery_soe_start=5.0,
        battery_soe_end=7.5,
    )
    assert classify_strategic_intent(0.0, ed) == "SOLAR_STORAGE"


def test_inverter_controller_maps_solar_storage_priority_to_battery_first():
    """SOLAR_STORAGE_PRIORITY must map to battery_first with grid_charge
    disabled -- the opt-in combination the default TOU mapping avoids."""
    assert (
        InverterController.INTENT_TO_MODE["SOLAR_STORAGE_PRIORITY"] == "battery_first"
    )
    control = InverterController.INTENT_TO_CONTROL["SOLAR_STORAGE_PRIORITY"]
    assert control["grid_charge"] is False
    assert control["discharge_rate"] == 0


def test_derive_control_command_handles_solar_storage_priority():
    """The simulator's control-command derivation (mirrors
    InverterController._map_intent_to_rates without needing a live
    controller instance) must not raise for the new intent -- it has an
    explicit raise-on-unknown fallback."""
    bs = make_battery_settings()
    command = derive_control_command("SOLAR_STORAGE_PRIORITY", 0.0, bs)
    assert command.battery_mode == "battery_first"
    assert command.grid_charge is False
    assert command.discharge_rate_pct == 0


@pytest.mark.slow
def test_consumption_spike_does_not_trigger_early_discharge():
    """A mid-day consumption spike (e.g. EV charging) under
    battery_first_priority must not cause the battery to discharge early --
    discharge timing stays governed by price alone. The spike should only
    show up as extra grid import for that hour, with the battery still
    charging/holding toward the day's genuine high-price period."""
    bs = make_battery_settings(max_charge_power_kw=5.0, max_discharge_power_kw=5.0)

    hours = 24
    solar = [0.0] * hours
    for h in range(8, 18):
        solar[h] = 4.0  # daytime solar

    consumption = [0.5] * hours
    consumption[13] = 8.0  # EV-charging-style spike at 13:00, still daytime

    buy_price = [0.5] * hours
    buy_price[19] = 3.0  # the one genuinely expensive hour (evening peak)
    sell_price = [0.3] * hours

    result_with_spike = optimize_battery_schedule(
        buy_price=buy_price,
        sell_price=sell_price,
        home_consumption=consumption,
        solar_production=solar,
        initial_soe=bs.min_soe_kwh,
        battery_settings=bs,
        period_duration_hours=1.0,
        battery_first_priority=True,
    )

    consumption_no_spike = list(consumption)
    consumption_no_spike[13] = 0.5
    result_no_spike = optimize_battery_schedule(
        buy_price=buy_price,
        sell_price=sell_price,
        home_consumption=consumption_no_spike,
        solar_production=solar,
        initial_soe=bs.min_soe_kwh,
        battery_settings=bs,
        period_duration_hours=1.0,
        battery_first_priority=True,
    )

    # The battery must still meaningfully discharge to cover the genuinely
    # expensive hour (19) regardless of whether the spike happened earlier
    # in the day -- the spike must not leave it starved for the real peak.
    peak_period = result_with_spike.period_data[19]
    assert peak_period.decision.strategic_intent in (
        "LOAD_SUPPORT",
        "BATTERY_EXPORT",
    ), (
        f"Expected the battery to discharge at the priced peak (hour 19), "
        f"got {peak_period.decision.strategic_intent}"
    )
    assert (
        peak_period.energy.battery_discharged > 0.1
    ), "Spike starved the battery of charge for the priced peak"

    # The spike is a same-day, one-off event -- it must not change how many
    # periods run in SOLAR_STORAGE_PRIORITY mode. The mode itself is sourced
    # from a dedicated sensor (see get_battery_first_priority_active),
    # structurally independent of consumption, so this should hold exactly.
    intents_with_spike = get_intent_distribution(result_with_spike)
    intents_no_spike = get_intent_distribution(result_no_spike)
    assert intents_with_spike.get("SOLAR_STORAGE_PRIORITY", 0) == intents_no_spike.get(
        "SOLAR_STORAGE_PRIORITY", 0
    ), "A consumption spike must not change how much battery-first charging occurs"


@pytest.mark.slow
def test_plan_faithfulness_r_equals_p_under_battery_first_priority():
    """REQUIRED per docs/agents/simulator.md: any DP/intent/control-mapping
    change must verify R == P for the affected scenarios."""
    bs = make_battery_settings(max_charge_power_kw=5.0, max_discharge_power_kw=5.0)

    hours = 24
    solar = [0.0] * hours
    for h in range(8, 18):
        solar[h] = 4.0
    consumption = [1.0] * hours
    buy_price = [0.5] * hours
    buy_price[19] = 3.0
    sell_price = [0.3] * hours

    planned_cost, realized_cost, per_period_deltas = verify_plan_faithfulness(
        buy_price=buy_price,
        sell_price=sell_price,
        solar=solar,
        home=consumption,
        initial_soe=bs.min_soe_kwh,
        settings=bs,
        dt=1.0,
        battery_first_priority=True,
    )
    tolerance = max(0.5, 0.01 * abs(planned_cost))
    assert abs(realized_cost - planned_cost) <= tolerance, (
        f"R != P under battery_first_priority: planned={planned_cost:.4f}, "
        f"realized={realized_cost:.4f}, deltas={per_period_deltas}"
    )
