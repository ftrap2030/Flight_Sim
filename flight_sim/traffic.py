"""Other aeroplanes in the sky.

Traffic here is not simulated -- it is *evaluated*. Every aircraft's position
is a pure function of the seed, its route and the clock, read off the same
`navigation.plan` profile the player's own flight plan is costed with. Nothing
is integrated, nothing accumulates, and there is no traffic state anywhere on
`FlightState`.

Three things follow from that, and they are the whole reason it is built this
way rather than by running a dozen more Simulators:

* It costs nothing per frame. A dozen aeroplanes are a dozen interpolations
  along a profile that was computed once for the seed.
* Both builds put the same aeroplanes in the same places to the metre, so
  `parity_check` compares them exactly rather than within a tolerance.
* Saving, resuming and replaying work for free, because there is nothing to
  save: ask for the sky at T and you get the sky at T.

The one thing given up is that traffic cannot react -- nobody goes around
because you are slow on final. That is the honest trade, and it is the same
trade `failures.py` makes: what is modelled here changes numbers the flight
model already reads, and what is not modelled is absent rather than faked.

The timetable repeats on a cycle so the sky is never empty, however long a
session runs and whenever in the day it starts.
"""

import math

from . import aircraft as fleet
from . import atmosphere as atm
from . import navigation
from .terrain import _hash01

# The timetable repeats every hour of flight time. Long enough that the same
# aeroplane does not come round while you are watching it, short enough that a
# short hop still shares the sky with somebody.
CYCLE_S = 3600.0
# How many flights the region carries at once. The home airfields are five, so
# this is a busy but not absurd little network.
SCHEDULED_FLIGHTS = 14

# TCAS. Real boxes work in *time* to the closest point of approach; this works
# in range and height, which is an honest simplification and is why the bands
# are named for what they mean rather than claiming to be a TA or an RA.
PROXIMATE_NM, PROXIMATE_FT = 6.0, 1200.0
TRAFFIC_NM, TRAFFIC_FT = 3.5, 900.0
THREAT_NM, THREAT_FT = 1.5, 400.0

PROXIMATE, TRAFFIC, THREAT, DISTANT = "proximate", "traffic", "threat", "distant"

# Margin on top of the required field length. One, because
# `takeoff_length_ft` already carries the factor that turns a ground roll into
# a field length -- applying a second 25% on top of it double-counted, and left
# only four of the eleven types able to work between any two of these five
# runways. The estimate is the margin.
RUNWAY_MARGIN_FT = 1.0

_AIRLINES = ("ANF", "KBR", "CRW", "HRW", "VSP", "NLD")


def _callsign(seed, index):
    airline = _AIRLINES[int(_hash01(index, 11, seed + 7717) * len(_AIRLINES))]
    number = 100 + int(_hash01(index, 13, seed + 9127) * 800)
    return "{}{}".format(airline, number)


# Ground roll to lift-off is v_lof^2 / 2a; a real take-off *field* length also
# carries the rotation, the transition and the screen height, and is comfortably
# more than the roll. This is the multiplier between the two.
TAKEOFF_FIELD_FACTOR = 2.1


def takeoff_length_ft(craft, flap_setting=2):
    """Roughly the runway this type needs at the weight it flies at here.

    Not a published take-off field length -- those depend on temperature,
    pressure altitude, flap setting and which engine is assumed to fail -- but
    the physics that drives one, out of figures this fleet already publishes:
    lift off at 1.15 times the stall speed in the take-off configuration,
    having accelerated at what the thrust-to-weight ratio buys against rolling
    friction.

    The first version of this divided thrust by weight and added a constant,
    and gave the whole fleet 3,500 ft to within four percent -- so every runway
    accepted every type and the A380 was scheduled into a 5,400 ft strip. The
    term it was missing is the one that actually separates them: lift-off speed
    goes as the square root of wing loading, and the distance goes as its
    square, so the wing matters as much as the engines do.
    """
    # `start_mass_kg`, not MTOW: these are sixty-mile sectors, and a regional
    # hop dispatches nowhere near maximum weight. Asked at MTOW the answer was
    # that nothing in the fleet could use Harrow Deep's 5,400 ft -- which is
    # true of a full A320 and not true of the one that would actually fly it.
    mass_kg = craft.start_mass_kg
    stall_ms = craft.stall_speed_ias_ms(mass_kg, 1.0, flap_setting)
    lift_off_ms = 1.15 * stall_ms
    thrust_to_weight = craft.thrust_sl_n / (mass_kg * atm.G0)
    accel_ms2 = atm.G0 * max(thrust_to_weight - 0.02, 0.02)
    roll_m = lift_off_ms * lift_off_ms / (2.0 * accel_ms2)
    return roll_m / atm.M_PER_FT * TAKEOFF_FIELD_FACTOR


def types_for_runway(length_ft):
    """Which of the fleet could use a runway this long, longest-legged first."""
    usable = [
        craft for craft in fleet.FLEET
        if craft.carries_passengers
        and takeoff_length_ft(craft) * RUNWAY_MARGIN_FT <= length_ft
    ]
    return usable or [min(fleet.FLEET, key=takeoff_length_ft)]


class ScheduledFlight:
    """One repeating service: who, what, between where, leaving when."""

    def __init__(self, callsign, aircraft_key, origin, destination, departure_s):
        self.callsign = callsign
        self.aircraft_key = aircraft_key
        self.origin = origin
        self.destination = destination
        self.departure_s = departure_s

    @property
    def route_idents(self):
        return (self.origin.ident, self.destination.ident)

    def __repr__(self):
        return "<{} {} {}->{} at {:.0f}s>".format(
            self.callsign, self.aircraft_key, self.origin.ident,
            self.destination.ident, self.departure_s)


def schedule(seed, airfields):
    """The timetable for a seed: deterministic, varied, and flyable.

    Built from the same lattice hash the terrain uses, so the browser generates
    the identical timetable rather than one that merely looks similar.

    The type is chosen *first* and the airfields second, which is the way round
    that matters. Picking the pair first and then something that could use it
    gave a sky that was twelve A319neos out of fourteen, because Harrow Deep
    and Vesper Shelf are short enough to accept only the smallest type and they
    are two of the five. Choosing the aeroplane and then asking which fields can
    take it gives every type its turn, and still puts nothing anywhere it could
    not get out of.

    Departures are spaced evenly across the cycle with a hashed jitter inside
    each slot, rather than scattered at random. Fourteen random departures in an
    hour leave gaps: at one point the sky was empty for eight minutes and at
    another it held eight aeroplanes. An even flow with some slop in it is also
    what a timetable *is*.
    """
    fields = list(airfields.authored.fields)
    if len(fields) < 2:
        return []
    by_length = sorted(fields, key=lambda f: f.runway_length_ft, reverse=True)
    airliners = [craft for craft in fleet.FLEET if craft.carries_passengers]

    flights = []
    slot_s = CYCLE_S / SCHEDULED_FLIGHTS
    for index in range(SCHEDULED_FLIGHTS):
        # Step through the fleet from the hashed starting point until a type
        # turns up that has two fields it can actually work between.
        #
        # The first version fell back to "the two longest runways", which is
        # not the same thing: it put an A321XLR needing 8,983 ft into
        # Crowmarsh's 8,800, because the second-longest runway is only the
        # second longest and not necessarily long enough. Changing the *type*
        # keeps every service flyable, and it terminates because the smallest
        # aeroplane in the fleet always qualifies.
        first = int(_hash01(index, 7, seed + 3041) * len(airliners))
        craft, usable = None, []
        for offset in range(len(airliners)):
            candidate = airliners[(first + offset) % len(airliners)]
            needed = takeoff_length_ft(candidate) * RUNWAY_MARGIN_FT
            fits = [f for f in fields if f.runway_length_ft >= needed]
            if len(fits) >= 2:
                craft, usable = candidate, fits
                break
        if craft is None:
            # Not one type in the fleet can work between two of these fields.
            # Say nothing rather than invent a service that could not be flown.
            continue

        origin = usable[int(_hash01(index, 3, seed + 1013) * len(usable))]
        step = 1 + int(_hash01(index, 5, seed + 2027) * (len(usable) - 1))
        destination = usable[(usable.index(origin) + step) % len(usable)]

        departure_s = (index + _hash01(index, 17, seed + 4051)) * slot_s
        flights.append(ScheduledFlight(
            _callsign(seed, index), craft.key, origin, destination, departure_s))
    return flights


class FlightProfile:
    """A scheduled flight's whole vertical and lateral path, precomputed.

    The legs are the great-circle-free straight lines this world uses, and the
    vertical profile is `navigation.plan`'s three phases -- so a traffic
    aeroplane climbs at the rate the model says that type climbs at, and is at
    the altitude the flight plan would have charged it for.
    """

    def __init__(self, flight, plan, legs):
        self.flight = flight
        self.plan = plan
        self.legs = legs  # [(x0, y0, x1, y1, distance_nm, track_deg), ...]
        self.total_nm = sum(leg[4] for leg in legs)
        self.field_ft = flight.destination.elevation_ft
        self.origin_ft = flight.origin.elevation_ft

        self.climb_s = plan.climb_time_s
        self.cruise_s = plan.cruise_time_s
        self.descent_s = plan.descent_time_s
        self.duration_s = self.climb_s + self.cruise_s + self.descent_s
        self.climb_nm = plan.climb_distance_nm
        self.descent_nm = plan.descent_distance_nm
        self.cruise_nm = max(0.0, self.total_nm - self.climb_nm - self.descent_nm)
        self.cruise_ft = plan.cruise_ft

    def _along(self, distance_nm):
        """Position and track at a distance flown along the route."""
        remaining = distance_nm
        for x0, y0, x1, y1, leg_nm, track in self.legs:
            if remaining <= leg_nm or leg_nm <= 0.0:
                fraction = 0.0 if leg_nm <= 0.0 else remaining / leg_nm
                return (x0 + (x1 - x0) * fraction,
                        y0 + (y1 - y0) * fraction, track)
            remaining -= leg_nm
        x0, y0, x1, y1, _leg_nm, track = self.legs[-1]
        return (x1, y1, track)

    def at(self, age_s):
        """Where this flight is `age_s` after its departure, or None.

        None before it rolls and after it parks -- an aeroplane sitting on a
        stand is not traffic, and pretending otherwise would put a target on
        the display that never moves.
        """
        if age_s < 0.0 or age_s > self.duration_s:
            return None

        if age_s <= self.climb_s and self.climb_s > 0.0:
            fraction = age_s / self.climb_s
            distance = self.climb_nm * fraction
            altitude = self.origin_ft + (self.cruise_ft - self.origin_ft) * fraction
            phase = "climb"
        elif age_s <= self.climb_s + self.cruise_s:
            fraction = ((age_s - self.climb_s) / self.cruise_s
                        if self.cruise_s > 0.0 else 0.0)
            distance = self.climb_nm + self.cruise_nm * fraction
            altitude = self.cruise_ft
            phase = "cruise"
        else:
            fraction = ((age_s - self.climb_s - self.cruise_s) / self.descent_s
                        if self.descent_s > 0.0 else 1.0)
            distance = self.climb_nm + self.cruise_nm + self.descent_nm * fraction
            altitude = self.cruise_ft + (self.field_ft - self.cruise_ft) * fraction
            phase = "descent"

        x_nm, y_nm, track = self._along(distance)
        return Contact(
            callsign=self.flight.callsign,
            aircraft_key=self.flight.aircraft_key,
            x_nm=x_nm, y_nm=y_nm, altitude_ft=altitude,
            heading_deg=track, phase=phase,
            destination=self.flight.destination.ident,
        )


class Contact:
    """One aeroplane in the sky, as seen from outside it."""

    def __init__(self, callsign, aircraft_key, x_nm, y_nm, altitude_ft,
                 heading_deg, phase, destination):
        self.callsign = callsign
        self.aircraft_key = aircraft_key
        self.x_nm = x_nm
        self.y_nm = y_nm
        self.altitude_ft = altitude_ft
        self.heading_deg = heading_deg
        self.phase = phase
        self.destination = destination
        # Filled in by `relative_to`, because they are relative to a viewer.
        self.range_nm = None
        self.bearing_deg = None
        self.relative_altitude_ft = None
        self.band = DISTANT

    def relative_to(self, x_nm, y_nm, altitude_ft):
        self.range_nm = math.hypot(self.x_nm - x_nm, self.y_nm - y_nm)
        self.bearing_deg = math.degrees(
            math.atan2(self.x_nm - x_nm, self.y_nm - y_nm)) % 360.0
        self.relative_altitude_ft = self.altitude_ft - altitude_ft
        self.band = band_for(self.range_nm, self.relative_altitude_ft)
        return self


def band_for(range_nm, relative_altitude_ft):
    """How much this contact matters, which is display data with one owner.

    Both front ends colour and shape the symbol from this rather than each
    deciding for itself what counts as close -- the same reason `engines.egt_band`
    exists instead of two builds comparing against two sets of thresholds.
    """
    height = abs(relative_altitude_ft)
    if range_nm <= THREAT_NM and height <= THREAT_FT:
        return THREAT
    if range_nm <= TRAFFIC_NM and height <= TRAFFIC_FT:
        return TRAFFIC
    if range_nm <= PROXIMATE_NM and height <= PROXIMATE_FT:
        return PROXIMATE
    return DISTANT


def build_profiles(flights, plan_for):
    """Cost every scheduled flight once. `plan_for(key, waypoints) -> Plan`."""
    profiles = []
    for flight in flights:
        waypoints = [
            navigation.Waypoint.from_airfield(flight.origin),
            navigation.Waypoint.from_airfield(flight.destination),
        ]
        plan = plan_for(flight.aircraft_key, waypoints)
        if plan is None:
            continue
        legs = []
        previous = waypoints[0]
        for waypoint in waypoints[1:]:
            distance = waypoint.distance_nm(previous.x_nm, previous.y_nm)
            track = waypoint.bearing_from(previous.x_nm, previous.y_nm)
            legs.append((previous.x_nm, previous.y_nm,
                         waypoint.x_nm, waypoint.y_nm, distance, track))
            previous = waypoint
        if not legs or sum(leg[4] for leg in legs) <= 0.0:
            continue
        profiles.append(FlightProfile(flight, plan, legs))
    return profiles


def sky_at(profiles, elapsed_s):
    """Every aeroplane airborne at this moment.

    The timetable repeats, so a flight's age is taken modulo the cycle: the
    sky is never empty and never needs winding up before it is asked.
    """
    out = []
    for profile in profiles:
        age = (elapsed_s - profile.flight.departure_s) % CYCLE_S
        contact = profile.at(age)
        if contact is not None:
            out.append(contact)
    return out


def near(profiles, elapsed_s, x_nm, y_nm, altitude_ft, radius_nm=40.0):
    """The sky around a position, closest first."""
    contacts = []
    for contact in sky_at(profiles, elapsed_s):
        contact.relative_to(x_nm, y_nm, altitude_ft)
        if contact.range_nm <= radius_nm:
            contacts.append(contact)
    contacts.sort(key=lambda c: c.range_nm)
    return contacts
