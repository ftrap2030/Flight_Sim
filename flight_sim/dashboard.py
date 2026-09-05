"""Markdown instrument panel.

Renders the readout as a clean markdown block: a warning strip, an ASCII
attitude indicator, and grouped instrument tables that mirror the way the real
displays are laid out (PFD on the left, systems on the right).
"""

from . import aircraft as fleet
from . import atmosphere as atm

HORIZON_WIDTH = 33


def _bar(value, maximum, width=20, fill="#", empty="."):
    filled = int(round(width * max(0.0, min(1.0, value / maximum))))
    return fill * filled + empty * (width - filled)


def _arrow(vs_fpm):
    if vs_fpm > 300:
        return "^^" if vs_fpm > 1500 else "^"
    if vs_fpm < -300:
        return "vv" if vs_fpm < -1500 else "v"
    return "="


SLIP_BALL_WIDTH = 13


def slip_ball(sideslip_deg, max_deg=15.0):
    """A turn-coordinator ball.

    The ball swings *opposite* to the sideslip, which is what gives the old
    instruction its meaning: step on the ball. Yaw the nose right and the ball
    goes left, and left rudder is what centres it.
    """
    centre = SLIP_BALL_WIDTH // 2
    offset = -sideslip_deg / max_deg * centre
    position = int(round(centre + max(-centre, min(centre, offset))))
    cells = ["-"] * SLIP_BALL_WIDTH
    cells[position] = "O"
    return "[" + "".join(cells) + "]"


def attitude_indicator(pitch_deg, bank_deg):
    """A small ASCII artificial horizon: the horizon line tilts with bank."""
    rows = []
    height = 7
    centre = height // 2
    # Each row is one 5-degree pitch band. The aircraft symbol is fixed and the
    # horizon moves, as on a real ADI: pitch up and the horizon drops below the
    # reticle, leaving more sky above it. Hence the negation.
    pitch_offset = -int(round(pitch_deg / 5.0))
    bank_shift = bank_deg / 90.0  # -1 .. 1 across the display

    for row in range(height):
        band = row - centre + pitch_offset
        line = []
        for col in range(HORIZON_WIDTH):
            # Where the tilted horizon sits in this column, in rows.
            x = (col - HORIZON_WIDTH / 2.0) / (HORIZON_WIDTH / 2.0)
            horizon_row = -x * bank_shift * 2.4
            if band < horizon_row - 0.5:
                line.append("`")  # sky
            elif band > horizon_row + 0.5:
                line.append(":")  # ground
            else:
                line.append("-")  # the horizon itself
        # Centre reticle
        if row == centre:
            mid = HORIZON_WIDTH // 2
            line[mid - 2] = "-"
            line[mid - 1] = "["
            line[mid] = "+"
            line[mid + 1] = "]"
            line[mid + 2] = "-"
        rows.append("".join(line))
    return rows


def render(sim, readout, title=None):
    """Full markdown dashboard for one moment of flight."""
    s = sim.state
    craft = sim.aircraft
    weather = sim.weather
    r = readout

    lines = []

    from . import weather as wx_module

    heading = title or "{} | {} | {} {} | T+{}".format(
        craft.name,
        weather.name,
        _local_clock(s.time_of_day_h),
        wx_module.light_phase(s.time_of_day_h).upper(),
        _clock(s.elapsed_s),
    )
    lines.append("### {}".format(heading))
    lines.append("")

    if r.warnings:
        lines.append("> **{}**".format("  |  ".join(r.warnings)))
        lines.append("")

    lines.append("```")
    for row in attitude_indicator(r.pitch_deg, r.bank_deg):
        lines.append(row)
    lines.append("")
    lines.append(
        "  IAS {:>5.0f} kt   ALT {:>7,.0f} ft   HDG {:>03.0f}".format(
            r.ias_kt, r.altitude_ft, r.heading_deg
        )
    )
    lines.append(
        "  MACH  {:>4.3f}     AGL {:>7,.0f} ft   V/S {:>+6,.0f} fpm {}".format(
            r.mach, r.agl_ft, r.vertical_speed_fpm, _arrow(r.vertical_speed_fpm)
        )
    )
    lines.append(
        "  SLIP  {}   b {:>+5.1f}   RUD {:>+5.1f} of {:.0f}".format(
            slip_ball(r.sideslip_deg),
            r.sideslip_deg,
            r.rudder_deg,
            r.rudder_limit_deg,
        )
    )
    lines.append("```")
    lines.append("")

    lines.append("| Flight | | Attitude | | Systems | |")
    lines.append("| --- | ---: | --- | ---: | --- | ---: |")
    lines.append(
        "| Airspeed (IAS) | **{:,.0f} kt** | Pitch | **{:+.1f}°** | Throttle | **{:.0f}%** |".format(
            r.ias_kt, r.pitch_deg, r.throttle_pct
        )
    )
    lines.append(
        "| True airspeed | {:,.0f} kt | Roll | {:+.1f}° | Thrust | {:,.0f} kN |".format(
            r.tas_kt, r.bank_deg, r.thrust_n / 1000.0
        )
    )
    lines.append(
        "| Ground speed | {:,.0f} kt | Sideslip (β) | **{:+.1f}°** | Fuel flow | {:,.0f} kg/h |".format(
            r.ground_speed_kt, r.sideslip_deg, r.fuel_flow_kgh
        )
    )
    lines.append(
        "| Mach | {:.3f} | Heading | {:03.0f}° | Fuel remaining | **{:,.0f} kg** |".format(
            r.mach, r.heading_deg, r.fuel_kg
        )
    )
    lines.append(
        "| Vertical speed | **{:+,.0f} fpm** | Track | {:03.0f}° | Fuel state | {:.1f}% |".format(
            r.vertical_speed_fpm, r.track_deg, r.fuel_pct
        )
    )
    lines.append(
        "| Altitude (MSL) | **{:,.0f} ft** | Angle of attack | {:+.1f}° | Gross weight | {:,.0f} kg |".format(
            r.altitude_ft, r.alpha_deg, r.mass_kg
        )
    )
    lines.append(
        "| Height (AGL) | {:,.0f} ft | Load factor | {:.2f} g | Configuration | {} |".format(
            r.agl_ft, r.load_factor, _config_text(s)
        )
    )
    lines.append(
        "| Terrain below | {:,.0f} ft | Stall speed | {:,.0f} kt | Turbulence | {} |".format(
            r.terrain_ft, r.stall_ias_kt, weather.turbulence_label()
        )
    )
    lines.append(
        "| Wind drift | {:+.1f}° | Rudder | {:+.1f}° / {:.0f}° | Engines | {} |".format(
            r.wind_drift_deg,
            r.rudder_deg,
            r.rudder_limit_deg,
            _engine_text(sim, r),
        )
    )
    lines.append(
        "| Wind here | {:.0f} kt / {:03.0f}° | Visibility | {} | Local time | {} |".format(
            r.wind_speed_kt,
            r.wind_dir_deg,
            _visibility_text(weather.visibility_sm),
            _local_clock(s.time_of_day_h),
        )
    )
    lines.append("")

    lines.append(
        "`FUEL [{}] {:.0f}%`  `THR [{}] {:.0f}%`".format(
            _bar(r.fuel_kg, craft.fuel_capacity_kg),
            r.fuel_pct,
            _bar(r.throttle_pct, 100.0),
            r.throttle_pct,
        )
    )
    nav_block = _navigation_text(r)
    if nav_block:
        lines.append(nav_block)
        lines.append("")

    approach_block = _approach_text(sim, r)
    if approach_block:
        lines.append(approach_block)
        lines.append("")

    lines.append("")
    lines.append(
        "*Highest terrain within 12 nm of the nose: **{:,.0f} ft** at {:.1f} nm "
        "({}). Visibility {}.*".format(
            r.terrain_ahead_ft,
            r.terrain_ahead_nm,
            r.terrain_ahead_name,
            _visibility_text(weather.visibility_sm),
        )
    )

    return "\n".join(lines)


def _navigation_text(r):
    """Where the destination is, and whether the fuel reaches it."""
    leg = r.leg
    if leg is None:
        return None
    side = "left" if leg.relative_bearing_deg < 0 else "right"
    turn = (
        "on the nose"
        if abs(leg.relative_bearing_deg) < 3
        else "{:.0f}° {}".format(abs(leg.relative_bearing_deg), side)
    )
    fuel_note = (
        "**{:,.0f} kg on arrival**".format(leg.fuel_on_arrival_kg)
        if leg.reachable
        else "**WILL NOT REACH IT — short by {:,.0f} kg**".format(
            abs(leg.fuel_on_arrival_kg)
        )
    )
    return (
        "**NAV — {}**  ·  {:.1f} nm  ·  bearing {:03.0f}° ({})  ·  "
        "ETA {}  ·  {}".format(
            leg.waypoint.ident or leg.waypoint.name,
            leg.distance_nm,
            leg.bearing_deg,
            turn,
            leg.eta_text(),
            fuel_note,
        )
    )


def _deviation_bar(value, full_scale, width=11):
    """A centred deviation needle: where you are against where you should be."""
    centre = width // 2
    offset = max(-1.0, min(1.0, value / full_scale))
    position = int(round(centre + offset * centre))
    cells = ["."] * width
    cells[centre] = "|"
    cells[position] = "#"
    return "".join(cells)


def _approach_text(sim, r):
    """The approach block: only shown when there is a runway to fly at."""
    approach = r.approach
    if approach is None or not approach.on_approach:
        return None

    field = approach.field
    above_below = "HIGH" if approach.glideslope_dev_ft > 0 else "LOW"
    left_right = "RIGHT" if approach.across_ft > 0 else "LEFT"
    lines = [
        "**APPROACH — {} ({}) runway {:02.0f}**  ·  {}  ·  elev {:,.0f} ft  "
        "·  {:,.0f} ft available".format(
            field.name,
            field.ident,
            approach.direction_deg / 10.0,
            "ILS" if field.has_ils else "VISUAL",
            field.elevation_ft,
            field.runway_length_ft,
        ),
        "",
        "```",
        "  RANGE   {:>5.1f} nm to threshold        Vref {:>5.0f} kt"
        " (you: {:>5.0f})".format(approach.distance_nm, r.vref_kt, r.ias_kt),
        "  G/S   [{}]  {:>+6.0f} ft {}".format(
            _deviation_bar(approach.glideslope_dev_ft, 400.0),
            approach.glideslope_dev_ft,
            above_below,
        ),
        "  LOC   [{}]  {:>+6.0f} ft {}".format(
            _deviation_bar(approach.across_ft, 1200.0),
            approach.across_ft,
            left_right,
        ),
        "```",
    ]
    return "\n".join(lines)


def _engine_text(sim, readout):
    total = sim.aircraft.engine_count
    running = readout.engines_running_count
    if running == total:
        return "{}/{} OK".format(running, total)
    return "**{}/{} — {} OUT**".format(running, total, total - running)


def _config_text(state):
    parts = ["FLAPS " + fleet.FLAP_NAMES[state.flaps]]
    parts.append("GEAR DN" if state.gear_down else "GEAR UP")
    if state.spoilers:
        parts.append("SPD BRK")
    if state.brakes > 0:
        parts.append("BRK {:.0f}%".format(state.brakes * 100))
    if state.reverse_thrust:
        parts.append("REV")
    return ", ".join(parts)


def _visibility_text(visibility_sm):
    if visibility_sm >= 10:
        return "{:.0f}+ sm".format(visibility_sm)
    if visibility_sm >= 1:
        return "{:.1f} sm".format(visibility_sm)
    return "{:,.0f} m".format(visibility_sm * 1609.34)


def _local_clock(hour_of_day):
    hours = int(hour_of_day) % 24
    minutes = int((hour_of_day % 1.0) * 60)
    return "{:02d}:{:02d}".format(hours, minutes)


def _clock(seconds):
    # Round rather than truncate: a 10-second tick accumulates to 9.999... over
    # 100 substeps, and truncation would show T+00:09.
    total = int(round(seconds))
    return "{:02d}:{:02d}".format(total // 60, total % 60)


def briefing(craft, weather, readout):
    """The pre-flight card shown once, when the simulation initialises."""
    lines = []
    lines.append("## Flight initialised")
    lines.append("")
    lines.append("**Aircraft** — {} · {}".format(craft.name, craft.engines))
    lines.append(
        "**Weight** — {:,.0f} kg gross, {:,.0f} kg fuel  ·  "
        "**Wing** — {:.1f} m², aspect ratio {:.1f}".format(
            craft.start_mass_kg,
            craft.start_fuel_kg,
            craft.wing_area_m2,
            craft.aspect_ratio,
        )
    )
    lines.append(
        "**Envelope** — Vmo {:.0f} kt / Mmo {:.2f}  ·  ceiling {:,.0f} ft  ·  "
        "cruise M{:.2f}".format(
            craft.vmo_kt, craft.mmo, craft.ceiling_ft, craft.cruise_mach
        )
    )
    lines.append(
        "**Weather** — {} · turbulence {} · visibility {}".format(
            weather.name,
            weather.turbulence_label(),
            _visibility_text(weather.visibility_sm),
        )
    )
    lines.append("")
    lines.append("> {}".format(weather.summary))
    lines.append("")
    lines.append("_{}_".format(craft.handling))
    return "\n".join(lines)


def fleet_menu():
    """Phase 1 aircraft selection card."""
    lines = ["## Phase 1 — Select your aircraft", ""]
    lines.append(
        "| # | Aircraft | Cruise | Ceiling | Vmo / Mmo | Roll rate |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for index, craft in enumerate(fleet.FLEET, start=1):
        lines.append(
            "| **{}** | **{}** | M{:.2f} ({:.0f} kt TAS) | {:,.0f} ft | {:.0f} kt / {:.2f} | {:.0f}°/s |".format(
                index,
                craft.name,
                craft.cruise_mach,
                craft.cruise_speed_kt,
                craft.ceiling_ft,
                craft.vmo_kt,
                craft.mmo,
                craft.roll_rate_deg_s,
            )
        )
    lines.append("")
    for index, craft in enumerate(fleet.FLEET, start=1):
        lines.append("**{}. {}** — {}".format(index, craft.name, craft.engines))
        lines.append("")
        lines.append("> {}".format(craft.handling))
        lines.append("")
    return "\n".join(lines)


def weather_menu():
    """Phase 1 weather selection card."""
    from . import weather as wx

    lines = ["## Phase 1 — Choose your weather", ""]
    lines.append("| # | Conditions | Turbulence | Wind | Visibility |")
    lines.append("| --- | --- | --- | --- | --- |")
    for index, profile in enumerate(wx.WEATHER_OPTIONS, start=1):
        wind = "{:.0f} kt / {:03.0f}°".format(profile.wind_speed_kt, profile.wind_dir_deg)
        if profile.gust_kt:
            wind += " G{:.0f}".format(profile.gust_kt)
        lines.append(
            "| **{}** | **{}** | {} | {} | {} |".format(
                index,
                profile.name,
                profile.turbulence_label(),
                wind,
                _visibility_text(profile.visibility_sm),
            )
        )
    lines.append("")
    for index, profile in enumerate(wx.WEATHER_OPTIONS, start=1):
        lines.append("**{}. {}** — {}".format(index, profile.name, profile.summary))
        lines.append("")
    return "\n".join(lines)
