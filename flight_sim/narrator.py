"""The cinematic description engine.

Prose is *composed*, not selected: each altitude band and weather profile owns
separate corpora for sky, terrain, motion, sensory and threat clauses. One
clause is weighted-sampled from each relevant corpus, live instrument values are
spliced in, and the result is assembled with varied connectives.

A deque of recently used clause IDs suppresses repetition across turns, so a
long flight does not loop back on itself after five commands.

Altitude bands follow the brief, and select on height above *ground*, not sea
level -- which is what actually determines whether you are looking at a horizon
or at rock:

    > 2,000 ft AGL   panoramic high-altitude vista
    1,000-2,000 ft   the landscape resolves, ridges rushing underneath
    < 1,000 ft       imminent terrain
"""

import math
import random
from collections import deque

from . import weather as wx

HIGH = "high"
LOW = "low"
CRITICAL = "critical"

MEMORY = 16  # how many recently used clauses to suppress


def band_for(agl_ft):
    if agl_ft > 2000.0:
        return HIGH
    if agl_ft >= 1000.0:
        return LOW
    return CRITICAL


class Narrator:
    def __init__(self, seed=20260905):
        self._rng = random.Random(seed ^ 0x5EED)
        self._recent = deque(maxlen=MEMORY)

    def _pick(self, pool):
        """Weighted-random pick that avoids recently used clauses."""
        if not pool:
            return ""
        fresh = [item for item in pool if item[0] not in self._recent]
        candidates = fresh if fresh else list(pool)
        key, template = self._rng.choice(candidates)
        self._recent.append(key)
        return template

    def describe(self, sim, readout):
        """Compose the outside view for the current moment."""
        ctx = _context(sim, readout)
        band = band_for(readout.agl_ft)
        weather_key = sim.weather.key
        obscured = sim.weather.visibility_sm < 1.5

        parts = []

        sky_pool = SKY[band].get(weather_key, SKY[band]["clear"])
        parts.append(self._pick(sky_pool))

        # On the runway, or on final: the outside view stops being scenery and
        # becomes a set of cues, so those registers replace the terrain clause.
        if sim.state.on_ground:
            parts.append(self._pick(ROLLOUT))
        elif readout.approach is not None and readout.approach.on_approach:
            parts.append(self._pick(APPROACH))
        else:
            terrain_pool = TERRAIN_OBSCURED[band] if obscured else TERRAIN[band]
            parts.append(self._pick(terrain_pool))

        parts.append(self._pick(_motion_pool(readout)))
        parts.append(self._pick(SENSORY[weather_key]))

        light = wx.light_phase(sim.state.time_of_day_h)
        if light != "day" and band != CRITICAL:
            parts.append(self._pick(LIGHT[light]))

        threat = _threat_pool(sim, readout, band)
        if threat:
            parts.append(self._pick(threat))

        rendered = []
        for template in parts:
            if not template:
                continue
            try:
                rendered.append(template.format(**ctx))
            except (KeyError, IndexError, ValueError):
                # A malformed template must never take the simulation down.
                continue
        return "\n\n".join(rendered)

    def ending(self, sim, readout):
        """The final, terminal description."""
        ctx = _context(sim, readout)
        status = sim.state.status
        if status == "landed":
            grade = (sim.state.touchdown or {}).get("grade", "normal landing")
            pool = ENDING_LANDED.get(grade, ENDING_LANDED["normal landing"])
        elif status == "overrun":
            pool = ENDING_OVERRUN
        elif status == "crashed_terrain":
            # A wrecked arrival at an airfield reads nothing like a mountainside.
            touchdown = sim.state.touchdown
            if touchdown and not touchdown.get("survivable", True):
                pool = ENDING_BOTCHED_LANDING
            else:
                pool = ENDING_TERRAIN
        elif status == "structural_failure":
            pool = ENDING_STRUCTURAL
        else:
            pool = ENDING_PILOT
        return self._pick(pool).format(**ctx)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _context(sim, r):
    s = sim.state
    ridge_delta = r.terrain_ahead_ft - r.altitude_ft
    return {
        "aircraft": sim.aircraft.name,
        "alt": r.altitude_ft,
        "fl": r.altitude_ft / 100.0,
        "agl": r.agl_ft,
        "ias": r.ias_kt,
        "tas": r.tas_kt,
        "gs": r.ground_speed_kt,
        "mach": r.mach,
        "vs": r.vertical_speed_fpm,
        "vs_abs": abs(r.vertical_speed_fpm),
        "pitch": r.pitch_deg,
        "pitch_abs": abs(r.pitch_deg),
        "bank": r.bank_deg,
        "bank_abs": abs(r.bank_deg),
        "heading": r.heading_deg,
        "track": r.track_deg,
        "drift_abs": abs(r.drift_deg),
        "terrain_ft": r.terrain_ft,
        "ridge_ft": r.terrain_ahead_ft,
        "ridge_nm": r.terrain_ahead_nm,
        "ridge_name": r.terrain_ahead_name,
        "ridge_delta_abs": abs(ridge_delta),
        "feature": sim.terrain.feature_name(s.x_nm, s.y_nm),
        "fuel_pct": r.fuel_pct,
        "load": r.load_factor,
        "alpha": r.alpha_deg,
        "stall_ias": r.stall_ias_kt,
        "vis": _visibility_phrase(sim.weather.visibility_sm),
        "cloud_base": sim.weather.cloud_base_ft,
        "cloud_tops": sim.weather.cloud_tops_ft,
        "wind_kt": sim.weather.wind_speed_kt,
        "wind_dir": sim.weather.wind_dir_deg,
        "bank_word": _bank_word(r.bank_deg),
        "turn_side": "left" if r.bank_deg < 0 else "right",
        "away_side": "right" if r.bank_deg < 0 else "left",
        "vs_word": _vs_word(r.vertical_speed_fpm),
        "impact_ias": s.impact_ias_kt,
        "impact_vs": abs(s.impact_vs_fpm),
        "impact_elev": s.impact_elevation_ft,
        "impact_feature": s.impact_feature or r.terrain_ahead_name,
        "field": (s.touchdown or {}).get("field_name", "the airfield"),
        "sink": (s.touchdown or {}).get("sink_rate_fpm", 0.0),
        "td_ias": (s.touchdown or {}).get("ias_kt", 0.0),
        "vref_pct": (s.touchdown or {}).get("speed_ratio", 1.0) * 100.0,
        "centreline": abs((s.touchdown or {}).get("centreline_ft", 0.0)),
        "crab": abs((s.touchdown or {}).get("crab_deg", 0.0)),
        "runway_left": (s.touchdown or {}).get("remaining_ft", 0.0),
        "td_reason": (s.touchdown or {}).get("reason", ""),
        "hour": "{:02d}:{:02d}".format(
            int(s.time_of_day_h) % 24, int((s.time_of_day_h % 1.0) * 60)
        ),
        "light": wx.light_phase(s.time_of_day_h),
        "sun_elev": wx.solar_elevation_deg(s.time_of_day_h),
        "vref": r.vref_kt,
        "rotor": sim._mechanical_turbulence,
        "wave_fpm": sim._orographic_fpm,
        "wave_abs": abs(sim._orographic_fpm),
    }


def _visibility_phrase(visibility_sm):
    if visibility_sm >= 30:
        return "limitless"
    if visibility_sm >= 10:
        return "better than ten miles"
    if visibility_sm >= 3:
        return "a few miles"
    if visibility_sm >= 1:
        return "barely a mile"
    return "a few hundred metres"


def _bank_word(bank_deg):
    magnitude = abs(bank_deg)
    if magnitude < 2:
        return "wings dead level"
    if magnitude < 12:
        return "a shallow {:.0f} degrees of bank".format(magnitude)
    if magnitude < 28:
        return "a steady {:.0f} degrees of bank".format(magnitude)
    if magnitude < 45:
        return "a hard {:.0f} degrees of bank".format(magnitude)
    return "a brutal {:.0f} degrees of bank".format(magnitude)


def _vs_word(vs_fpm):
    if vs_fpm > 1500:
        return "climbing hard"
    if vs_fpm > 300:
        return "climbing"
    if vs_fpm < -1500:
        return "descending fast"
    if vs_fpm < -300:
        return "descending"
    return "holding level"


def _motion_pool(r):
    if r.stalled:
        return MOTION["stalled"]
    if abs(r.bank_deg) > 12:
        return MOTION["turning"]
    if r.vertical_speed_fpm > 500:
        return MOTION["climbing"]
    if r.vertical_speed_fpm < -500:
        return MOTION["descending"]
    return MOTION["level"]


def _threat_pool(sim, r, band):
    warnings = set(r.warnings)
    if "STALL" in warnings:
        return THREAT["stall"]
    if "TERRAIN -- PULL UP" in warnings:
        return THREAT["pull_up"]
    if "OVERSPEED" in warnings:
        return THREAT["overspeed"]
    if "ENGINES OUT" in warnings:
        return THREAT["engines_out"]
    if "TERRAIN" in warnings or "TERRAIN AHEAD" in warnings:
        return THREAT["terrain"]
    if "STALL WARNING" in warnings or "LOW SPEED" in warnings:
        return THREAT["low_speed"]
    if "OVERSTRESS" in warnings:
        return THREAT["overstress"]
    if sim._orographic_fpm < -700.0 and r.agl_ft < 6000.0:
        return THREAT["downdraught"]
    if sim._mechanical_turbulence > 0.45:
        return THREAT["rotor"]
    if "LOW FUEL" in warnings:
        return THREAT["low_fuel"]
    return None


# ---------------------------------------------------------------------------
# Clause corpora
# ---------------------------------------------------------------------------

SKY = {
    HIGH: {
        "clear": [
            ("h_c1", "The world opens out. From {alt:,.0f} feet the horizon is a "
                     "single unbroken arc, ruled across the windscreen and stained "
                     "the pale blue-white of high air, and above it the sky deepens "
                     "through cobalt toward something close to indigo at the top of "
                     "the glass. Sunlight comes in hard and unfiltered, throwing the "
                     "shadow of the window frames in sharp bars across the coaming."),
            ("h_c2", "Cirrus lies in long combed streaks far above, so thin the blue "
                     "shows straight through it. Below and ahead, the land is a "
                     "topographic relief map in ochre and slate, every drainage and "
                     "ridge line picked out by the low angle of the light, and the "
                     "curvature of the earth is just perceptible at the edges of the "
                     "windscreen."),
            ("h_c3", "Visibility is {vis}. A shelf of flat-topped cumulus stands off "
                     "to the {away_side}, each one casting a hard oval shadow onto the "
                     "valley floor beneath it, and the shadows crawl across the ground "
                     "at exactly the speed of the clouds that made them."),
            ("h_c4", "At {fl:.0f} flight level the air has the particular stillness of "
                     "altitude. The horizon holds absolutely steady in the glass, a "
                     "knife-edge between the hazed grey-gold of the lower atmosphere "
                     "and the clean blue above, and the whole vista has the silent, "
                     "enormous quality of something seen from orbit."),
            ("h_c5", "Ahead, the ridge lines stack away toward the horizon in "
                     "successive planes, each one paler than the last as the "
                     "intervening air scatters the light out of it — six ranges deep "
                     "before they fade entirely into the atmospheric haze."),
            ("h_c6", "The sun sits off the {away_side} quarter, low enough to gild the "
                     "top surface of the wing and turn the leading edge into a line of "
                     "white fire. Beyond it the land runs away in enormous, unhurried "
                     "detail, rivers showing as bright threads of reflected sky."),
        ],
        "crosswind": [
            ("h_x1", "Torn stratocumulus streams past at your level, ragged banners of "
                     "it shredding downwind, and the gaps between them open and close "
                     "on the landscape below like a shutter. The whole cloud deck is "
                     "visibly *moving* — forty-five knots of it, all in one direction."),
            ("h_x2", "The horizon is sharp but the air between is not: everything is "
                     "streaked and combed downwind, the cloud bases smeared out into "
                     "long grey wedges. Below, on the high ground, you can see the wind "
                     "itself written in the snowfields — plumes trailing off every crest."),
            ("h_x3", "A hard, scoured light. The wind has swept the atmosphere clean, "
                     "so visibility is {vis} and the ridges show in almost cruel "
                     "detail, but every cloud in sight is deformed, leaning, running "
                     "for the horizon."),
            ("h_x4", "Lenticular clouds stand motionless in the lee of the high peaks, "
                     "smooth as turned metal, stacked two and three deep — the visible "
                     "signature of a mountain wave, and a warning of what the air is "
                     "doing between here and the ground."),
        ],
        "stormy": [
            ("h_s1", "The sky ahead has gone architectural. A wall of cumulonimbus "
                     "climbs out of the murk and keeps climbing, past your altitude, "
                     "past {cloud_tops:,.0f} feet, flattening finally into an anvil "
                     "that spreads out over the top of the world. It is lit from "
                     "within, irregularly, like something with a pulse."),
            ("h_s2", "Rain hammers the windscreen in horizontal sheets and the "
                     "wipers lose the argument. Between beats you get a strobing, "
                     "fragmentary view: black cloud, grey rain shaft, a bruised "
                     "yellow-green light underneath it all, and then nothing again."),
            ("h_s3", "Lightning goes off somewhere inside the cell off the {away_side} "
                     "and for a quarter of a second the entire interior of the cloud "
                     "is illuminated — cathedral-sized vaults and canyons of vapour, "
                     "gone before the eye can fix on them. The thunder arrives through "
                     "the airframe rather than the ears."),
            ("h_s4", "You are threading a canyon between two cells. Vertical walls of "
                     "cloud on either side, close enough to see the boil and churn of "
                     "them, and the gap ahead is narrowing. The light in here is the "
                     "colour of an old bruise."),
        ],
        "foggy": [
            ("h_f1", "You are on top. Below the aircraft the fog lies in a single "
                     "unbroken sheet from horizon to horizon, dead flat, glowing white "
                     "in the sun — an inverted world where the only things above the "
                     "surface are the highest peaks, breaking through as scattered "
                     "black islands."),
            ("h_f2", "The fog sea beneath is so smooth and so complete that the sense "
                     "of altitude collapses; without the AGL readout there would be no "
                     "way to know whether that surface is a hundred feet below the "
                     "wheels or seven thousand. The horizon is a hard white line."),
            ("h_f3", "Above, the sky is a flawless blue and the sun is brilliant. "
                     "Below, at {cloud_tops:,.0f} feet, the fog top rolls in slow "
                     "billows like something breathing. Every valley, every road, "
                     "every ridge under 7,000 feet has simply ceased to exist."),
            ("h_f4", "Peaks stand up out of the white in ones and twos, casting long "
                     "blue shadows across the fog surface itself. They are the only "
                     "reference you have — and each one marks rock you cannot see the "
                     "base of."),
        ],
    },
    LOW: {
        "clear": [
            ("l_c1", "The horizon has climbed the windscreen and the vista is gone. "
                     "You are *in* the landscape now, not above it — the ground has "
                     "resolved into individual trees, individual boulders, the sharp "
                     "black geometry of shadow in every gully."),
            ("l_c2", "Detail floods in. At {agl:,.0f} feet above the ground you can "
                     "read the texture of the rock: shattered grey scree fanning out "
                     "below the crags, dark stands of pine crowding the valley floor, "
                     "a thread of white water at the very bottom."),
            ("l_c3", "The light comes at you sideways now, raking across the terrain "
                     "and throwing every ridge into hard relief. What was a smooth "
                     "brown map from altitude is revealed as something violent — "
                     "broken, folded, fissured rock."),
            ("l_c4", "Sky occupies only the top third of the windscreen. The rest is "
                     "moving earth, and it is moving *fast* — {gs:,.0f} knots of "
                     "ground speed reads as an abstraction on the panel and as a "
                     "physical assault through the glass."),
        ],
        "crosswind": [
            ("l_x1", "Down here the wind has teeth. The valley is funnelling it, and "
                     "you can see the effect on the ground — whole hillsides of trees "
                     "combed over, dust lifting off the scree in long streamers, the "
                     "surface of a tarn ripped white."),
            ("l_x2", "The aircraft is flying visibly sideways relative to the valley "
                     "floor, {drift_abs:.0f} degrees of drift crabbed into the wind, "
                     "so the terrain slides across the windscreen at an angle that "
                     "makes no sense to the inner ear."),
            ("l_x3", "Rotor turbulence off the ridge to the {away_side} arrives in "
                     "hard, irregular slams. Between them the view is spectacular: "
                     "raw rock, wind-scoured, every crag standing out with unnatural "
                     "clarity in the scoured air."),
        ],
        "stormy": [
            ("l_s1", "Rain and rock. The terrain appears out of the murk in fragments "
                     "— a wet black crag, a waterfall blown to vapour halfway down its "
                     "drop, a slope of streaming scree — and each fragment is gone "
                     "before you have finished registering it."),
            ("l_s2", "The valley below is a grey cauldron. Cloud is snagged on every "
                     "ridge and tearing loose in ropes, the rain is coming through "
                     "horizontally, and lightning keeps printing the whole scene onto "
                     "your retinas in searing monochrome."),
            ("l_s3", "A downdraught takes the aircraft bodily. For three seconds the "
                     "ground comes up at you through the rain with no input of your "
                     "own, and the ridge on the {away_side} rises past the window like "
                     "a lift going the other way."),
        ],
        "foggy": [
            ("l_f1", "You are inside it now. The world outside the glass is a "
                     "featureless, luminous grey — no horizon, no ground, no sky, no "
                     "sense of speed at all except the numbers on the panel."),
            ("l_f2", "Something dark passes below and to the {away_side}, close, and "
                     "is swallowed again before it resolves into anything. Rock. It "
                     "was rock."),
            ("l_f3", "The fog thins for a heartbeat and you see a hillside — wet, "
                     "black, streaked with runnels, filling the entire {away_side} "
                     "window — and then the grey closes over it again."),
        ],
    },
    CRITICAL: {
        "clear": [
            ("c_c1", "The ground is *right there*. Individual rocks flick past under "
                     "the nose, close enough to judge their size, and the sky is a "
                     "narrow strip above a windscreen full of rushing earth."),
            ("c_c2", "At {agl:,.0f} feet the terrain no longer scrolls — it strobes. "
                     "Ridge, gully, ridge, gully, and the shadow of the aircraft "
                     "rippling over all of it, keeping pace, very close."),
            ("c_c3", "You can see the wind moving the grass. That is how low you are."),
        ],
        "crosswind": [
            ("c_x1", "Ground rush and crosswind together: the terrain is tearing past "
                     "beneath at an angle, the aircraft crabbed {drift_abs:.0f} degrees "
                     "into the wind, and every gust puts the wingtip somewhere you did "
                     "not put it."),
            ("c_x2", "The valley walls are close enough on both sides to feel, and the "
                     "wind is pouring over the {away_side} ridge in a standing rotor "
                     "that is trying, physically, to roll you into the other one."),
        ],
        "stormy": [
            ("c_s1", "Black rock, white water, grey rain, all of it a few hundred feet "
                     "beneath the belly and none of it visible for more than a second "
                     "at a time. This is no place to be."),
            ("c_s2", "The rain is coming off the windscreen in solid ropes and there is "
                     "something enormous and dark filling the lower windows. The "
                     "altimeter is unwinding."),
        ],
        "foggy": [
            ("c_f1", "Grey. Only grey. And somewhere in it, very close now, several "
                     "thousand feet of vertical rock that you will not see until it "
                     "is the entire windscreen."),
            ("c_f2", "The radio altimeter is calling and there is nothing outside the "
                     "glass at all — no shape, no shadow, no horizon. Flying on faith "
                     "and instruments, {agl:,.0f} feet above ground you cannot see."),
        ],
    },
}


TERRAIN = {
    HIGH: [
        ("th1", "Ahead, {ridge_name} stands out of the haze — a serrated wall of rock "
                "cresting {ridge_ft:,.0f} feet, {ridge_nm:.1f} miles off the nose, its "
                "sunward faces bright and its north slopes still holding shadow."),
        ("th2", "The country below is a drowned maze of ridges and valleys, drainage "
                "running away in every direction like cracks in old glaze. Directly "
                "beneath, the ground lies at {terrain_ft:,.0f} feet — {agl:,.0f} feet "
                "of clean air under the keel."),
        ("th3", "{ridge_name} makes the horizon ahead, {ridge_ft:,.0f} feet of it, "
                "{ridge_nm:.1f} miles out. From here it is scenery. It will not stay "
                "scenery."),
        ("th4", "Snow lies in the high corries below, dirty white against grey rock, "
                "and the shadows of the peaks stretch eastward across the valley "
                "floors in long dark wedges {terrain_ft:,.0f} feet beneath you."),
        ("th5", "A river system unwinds beneath the aircraft, braided and silver, "
                "threading between shoulders of rock. The scale only becomes clear "
                "when a ridge slides under the nose and you realise it is two thousand "
                "feet of cliff."),
    ],
    LOW: [
        ("tl1", "{ridge_name} is coming. {ridge_ft:,.0f} feet of jagged rock, "
                "{ridge_nm:.1f} miles off the nose and closing at {gs:,.0f} knots over "
                "the ground — the crest of it a broken saw-edge against the sky."),
        ("tl2", "The valley walls have risen on both sides. Scree slopes, fractured "
                "buttresses, dark timber crowding the lower slopes, and the floor of "
                "it all rushing beneath at {terrain_ft:,.0f} feet — {agl:,.0f} feet "
                "under the wheels and the numbers are what matter now."),
        ("tl3", "A ridge crosses your track ahead, {ridge_ft:,.0f} feet at the crest, "
                "and the ground between here and there is not flat: it steps up in "
                "terraces, each one a little closer to the belly of the aircraft."),
        ("tl4", "The terrain underneath has become genuinely violent — a chaos of "
                "gullies and spurs, no two hundred yards of it at the same elevation, "
                "and all of it going past the windows at appalling speed."),
        ("tl5", "Below, a glacial valley runs roughly along your track, its floor "
                "{terrain_ft:,.0f} feet up and its walls climbing well above the "
                "aircraft on both sides. You are flying *in* it now, not over it."),
    ],
    CRITICAL: [
        ("tc1", "{ridge_name} fills the windscreen. {ridge_ft:,.0f} feet of rock, "
                "{ridge_nm:.1f} miles, and you are at {alt:,.0f}."),
        ("tc2", "Ground clearance {agl:,.0f} feet. The terrain ahead rises to "
                "{ridge_ft:,.0f} feet within {ridge_nm:.1f} miles. That is not a view. "
                "That is an arithmetic problem with very little time in it."),
        ("tc3", "Rock going past at eye level on both sides. The valley floor is "
                "{terrain_ft:,.0f} feet and the walls are higher than the wingtips."),
        ("tc4", "There is a saddle in the ridge ahead, slightly {away_side} of the "
                "nose — lower than the peaks on either side, and possibly the only "
                "way through that does not involve climbing."),
    ],
}


TERRAIN_OBSCURED = {
    HIGH: [
        ("oh1", "Somewhere below the fog top, {ridge_name} rises to {ridge_ft:,.0f} "
                "feet. You cannot see it. The terrain display insists it is there, "
                "{ridge_nm:.1f} miles ahead."),
        ("oh2", "The white surface below gives up nothing. Under it, the ground sits "
                "at {terrain_ft:,.0f} feet, and the only honest instrument you have "
                "about any of it is the radio altimeter."),
        ("oh3", "A single black summit breaks the fog off the {away_side}, and the "
                "chart says its neighbours — {ridge_name} among them, {ridge_ft:,.0f} "
                "feet — are just as tall and entirely submerged."),
    ],
    LOW: [
        ("ol1", "Nothing. Luminous grey in every direction, and {ridge_name} "
                "somewhere in it at {ridge_ft:,.0f} feet, {ridge_nm:.1f} miles ahead, "
                "invisible and entirely real."),
        ("ol2", "The ground is {agl:,.0f} feet below and you have never seen it. "
                "Occasionally something darker moves through the grey and is gone."),
        ("ol3", "Fog packs the valley to the brim. The terrain readout says "
                "{terrain_ft:,.0f} feet beneath the aircraft; the windscreen says "
                "there is no world out there at all."),
    ],
    CRITICAL: [
        ("oc1", "{agl:,.0f} feet above ground you cannot see, closing on "
                "{ridge_name} at {ridge_ft:,.0f} feet. The grey outside is completely "
                "featureless right up until the moment it will not be."),
        ("oc2", "The GPWS is talking. Outside there is nothing to look at — no "
                "shadow, no shape, no horizon — and the rock is {ridge_nm:.1f} miles "
                "ahead and higher than you are."),
    ],
}


MOTION = {
    "level": [
        ("m_l1", "The aircraft sits steady with {bank_word}, {ias:,.0f} knots "
                 "indicated, holding {alt:,.0f} feet as though it were nailed there."),
        ("m_l2", "Straight and level. {ias:,.0f} knots, Mach {mach:.3f}, "
                 "{vs:+,.0f} feet a minute — which is to say, none."),
        ("m_l3", "Trimmed and stable at {alt:,.0f} feet. The horizon holds its place "
                 "in the windscreen and the engines sit at a steady drone."),
    ],
    "climbing": [
        ("m_u1", "The nose is up {pitch_abs:.1f} degrees and the vertical speed "
                 "indicator is showing {vs:+,.0f} feet a minute. The horizon slides "
                 "down the glass and more sky comes in over the top of it."),
        ("m_u2", "Climbing at {vs_abs:,.0f} feet a minute. You can feel it in the "
                 "seat — that steady, patient press of a large aeroplane converting "
                 "thrust into height, {ias:,.0f} knots on the dial and falling slowly."),
        ("m_u3", "{pitch_abs:.1f} degrees nose-up, {vs:+,.0f} fpm, the altimeter "
                 "winding steadily and the ground beginning, at last, to let go."),
    ],
    "descending": [
        ("m_d1", "The nose is down {pitch_abs:.1f} degrees and the ground is coming "
                 "up at {vs_abs:,.0f} feet a minute. Airspeed builds: {ias:,.0f} knots "
                 "and rising, the airframe noise rising with it."),
        ("m_d2", "Descending {vs_abs:,.0f} feet a minute. The horizon climbs the "
                 "windscreen and the terrain grows — not gradually, but with the "
                 "unnerving accelerating swell of something approaching fast."),
        ("m_d3", "Going down at {vs_abs:,.0f} feet a minute, {ias:,.0f} knots, the altimeter "
                 "unwinding through {alt:,.0f} feet."),
    ],
    "turning": [
        ("m_t1", "{bank_word}. The aircraft comes round to the {turn_side}, the "
                 "horizon canting across the glass, {load:.2f} g settling you into "
                 "the seat and the whole landscape rotating beneath the {turn_side} "
                 "wingtip."),
        ("m_t2", "In the turn: {bank_word}, heading through {heading:03.0f} degrees. "
                 "The {away_side} wing points at the sky, the {turn_side} wing at the "
                 "ground, and the terrain swings past like a slow carousel."),
        ("m_t3", "Rolled into {bank_word} and coming {turn_side}. Pulling {load:.2f} "
                 "g, {ias:,.0f} knots, and the nose is scribing steadily across the "
                 "ridge line ahead."),
    ],
    "stalled": [
        ("m_s1", "The wing has stopped flying. The buffet comes up through the "
                 "airframe as a coarse, irregular shudder, the nose is trying to drop "
                 "and there is no crispness left in the controls at all."),
        ("m_s2", "Stalled. Angle of attack {alpha:.1f} degrees, well past the break, "
                 "and the aeroplane is mushing — descending fast with the nose high, "
                 "which is exactly the worst of both worlds."),
    ],
}


LIGHT = {
    "dawn": [
        ("li_d1", "The light is only just arriving. The eastern horizon is a "
                  "band of cold orange under a sky still holding its stars, and "
                  "the land below is entirely blue -- shadow filled, without "
                  "detail, the valleys still an hour from sunrise."),
        ("li_d2", "First light catches the tops. Only the highest ground is lit, "
                  "in a thin band of pink along the crests, and everything below "
                  "it is a single flat blue-grey nothing."),
    ],
    "golden": [
        ("li_g1", "The sun is low and the light comes in almost level, gilding "
                  "the upper surfaces and casting shadows miles long across the "
                  "valley floors. Every ridge stands out as though drawn in ink."),
        ("li_g2", "Long light. The sun sits {sun_elev:.0f} degrees above the "
                  "horizon and everything it touches has gone amber; everything "
                  "it does not is deep violet."),
    ],
    "dusk": [
        ("li_k1", "The light is going. The valleys have already lost theirs and "
                  "are filling with blue shadow from the floor up, while the "
                  "peaks hold a last orange for a few minutes more."),
        ("li_k2", "Sunset. The horizon burns along a narrow band and the sky "
                  "above it grades from copper through green to a darkening "
                  "indigo at the top of the windscreen."),
    ],
    "night": [
        ("li_n1", "It is {hour}, and dark. Outside there is nothing but the "
                  "faint red glow of the panel on the glass and, very "
                  "occasionally, a scatter of lights somewhere far below that "
                  "might be a town or might be a farm."),
        ("li_n2", "Night. The horizon is gone -- not obscured, simply absent -- "
                  "and the only attitude reference in the world is the "
                  "instrument in front of you. The terrain below is a black "
                  "absence you have to take the radio altimeter's word for."),
        ("li_n3", "Starlight, and nothing else. The sky is dense with it above, "
                  "and beneath the aircraft the land is a void that swallows the "
                  "landing lights whole."),
    ],
}


SENSORY = {
    "clear": [
        ("s_c1", "Inside, it is almost serene: the steady white noise of the "
                 "airflow, the engines a smooth background pressure, sunlight moving "
                 "slowly across the glareshield."),
        ("s_c2", "The airframe is quiet and utterly steady. Only the faint hiss of "
                 "the air conditioning and the deep, distant note of the fans."),
        ("s_c3", "The controls feel light and precise, the aircraft answering "
                 "immediately and then simply staying where it was put."),
    ],
    "crosswind": [
        ("s_x1", "The aircraft rides the gusts in a continuous small argument — a "
                 "yaw, a correction, a wing dropping and being picked up again. Your "
                 "hand never quite leaves the sidestick."),
        ("s_x2", "Every few seconds a gust arrives as a solid thump through the "
                 "floor, and the drift readout twitches. Holding the track takes "
                 "constant, unglamorous work."),
        ("s_x3", "There is a low buffeting rumble through the airframe, and the "
                 "wingtip is describing small restless circles against the horizon."),
    ],
    "stormy": [
        ("s_s1", "The airframe is being beaten. Turbulence comes through the seat as "
                 "sharp vertical hammer-blows, loose objects lifting and slamming, "
                 "the instrument panel blurring at each hit."),
        ("s_s2", "Rain on the windscreen sounds like gravel. St Elmo's fire crawls "
                 "blue and spidery across the glass, and the whole cockpit smells "
                 "faintly of ozone."),
        ("s_s3", "The vertical speed indicator has stopped meaning anything — it "
                 "swings a thousand feet a minute either side of where you have the "
                 "aeroplane pointed, and the altimeter chases it."),
    ],
    "foggy": [
        ("s_f1", "Dead calm. The aircraft flies as though on rails, and the total "
                 "absence of visual reference makes the smoothness feel less like "
                 "peace and more like sensory deprivation."),
        ("s_f2", "It is very quiet. No turbulence, no horizon, nothing to look at — "
                 "just the soft glow of the panel and the numbers, which are now the "
                 "only description of the world you have."),
        ("s_f3", "Condensation beads and runs on the outside of the glass. The "
                 "landing lights, if you tried them, would only reflect back."),
    ],
}


THREAT = {
    "stall": [
        ("x_st1", "**STALL. STALL.** The synthetic voice cuts through everything. "
                  "Lower the nose and add power — now — or this ends in the rocks."),
        ("x_st2", "The stick shaker is going and the aeroplane is falling out of the "
                  "sky nose-high. Unload the wing: pitch down, throttle up."),
    ],
    "pull_up": [
        ("x_pu1", "**TERRAIN. TERRAIN. PULL UP. PULL UP.** {agl:,.0f} feet and "
                  "closing. This is the last warning you get."),
        ("x_pu2", "**PULL UP.** The GPWS is screaming, the ground is inside five "
                  "hundred feet, and every second spent deciding is a hundred feet of "
                  "altitude you no longer have."),
    ],
    "terrain": [
        ("x_tr1", "**TERRAIN AHEAD.** {ridge_name} is {ridge_delta_abs:,.0f} feet "
                  "above your present altitude, {ridge_nm:.1f} miles out. Climb or turn."),
        ("x_tr2", "The terrain caution is lit. At this heading and this altitude, the "
                  "rock ahead wins."),
    ],
    "overspeed": [
        ("x_os1", "**OVERSPEED.** The clacker is going. {ias:,.0f} knots, Mach "
                  "{mach:.3f} — past the barber pole and into structural territory. "
                  "Reduce thrust and raise the nose."),
        ("x_os2", "The airframe has taken on a hard, high-frequency buzz that was not "
                  "there a moment ago. You are beyond Vmo."),
    ],
    "low_speed": [
        ("x_ls1", "Speed is decaying: {ias:,.0f} knots against a stall speed of "
                  "{stall_ias:,.0f}. The controls have gone soft and sloppy."),
        ("x_ls2", "The low-speed cue is climbing the speed tape toward the bug. "
                  "Angle of attack {alpha:.1f} degrees and rising. Lower the nose or "
                  "add thrust before the wing decides for you."),
    ],
    "overstress": [
        ("x_ov1", "**{load:.2f} g.** The airframe is complaining audibly — a deep "
                  "groan through the wing root. Ease off the bank."),
    ],
    "engines_out": [
        ("x_eo1", "**Both engines have flamed out.** Fuel exhausted. The noise is "
                  "gone and what is left is wind — you are flying a very large "
                  "glider, and the only currency you have left is altitude."),
    ],
    "rotor": [
        ("x_ro1", "The air breaks up. Wind pouring over the ridge upwind is "
                  "shedding into the lee and the aircraft is being thrown "
                  "around inside it -- hard, irregular slams through the "
                  "airframe with no rhythm you can anticipate."),
        ("x_ro2", "Mechanical turbulence, and it is vicious. The wing is being "
                  "hit unevenly, the horizon jerking, and holding an altitude "
                  "down here has become a full-time occupation."),
    ],
    "downdraught": [
        ("x_dw1", "**Sink.** The air on this side of the ridge is going down at "
                  "{wave_abs:,.0f} feet a minute and taking you with it. Power "
                  "and a turn toward lower ground, before the mountain finishes "
                  "the arithmetic."),
    ],
    "low_fuel": [
        ("x_lf1", "Fuel at {fuel_pct:.1f}%. The low-level lights are on and the "
                  "clock is now a real constraint."),
    ],
}


ENDING_TERRAIN = [
    ("e_t1", "The ridge does not so much approach as *arrive*. For a fraction of a "
             "second the windscreen is filled entirely with rock — every fracture and "
             "lichen stain of it, close enough to count — and then {impact_feature} "
             "takes the aircraft at {impact_ias:,.0f} knots and {impact_vs:,.0f} feet "
             "a minute of descent.\n\n"
             "There is a white flash of impact, a shockwave of pulverised granite and "
             "burning fuel that rolls outward along the slope, and then a long "
             "diminishing rattle of debris down the scree.\n\n"
             "Elevation of impact: **{impact_elev:,.0f} feet**. The mountain is "
             "unmarked by the following morning except for a black scar and a smell "
             "of kerosene that the wind takes three days to clear.\n\n"
             "**— SIMULATION ENDED: CONTROLLED FLIGHT INTO TERRAIN —**"),
    ("e_t2", "There is no time for the GPWS to finish its sentence.\n\n"
             "{impact_feature} comes up out of the murk at {impact_ias:,.0f} knots — a "
             "wall of wet black rock that goes from abstraction to absolute certainty "
             "in under a second — and the {aircraft} strikes it {impact_elev:,.0f} "
             "feet above sea level, descending at {impact_vs:,.0f} feet a minute.\n\n"
             "The wing goes first, then everything else. The fireball climbs the "
             "gully, gutters against the wet stone, and goes out. Afterwards, the "
             "silence up there is enormous.\n\n"
             "**— SIMULATION ENDED: CONTROLLED FLIGHT INTO TERRAIN —**"),
]

ENDING_STRUCTURAL = [
    ("e_s1", "The airframe gives up all at once.\n\n"
             "At {impact_ias:,.0f} knots and {load:.2f} g, something structural lets "
             "go — a spar, a wing root, it hardly matters which. The wing folds "
             "upward with a sound felt rather than heard, the aircraft departs "
             "controlled flight instantly, and the horizon begins to rotate in a way "
             "that no input will stop.\n\n"
             "The remains of the {aircraft} come down across the slopes of "
             "{impact_feature} over the better part of a mile.\n\n"
             "**— SIMULATION ENDED: STRUCTURAL FAILURE —**"),
]

ENDING_LANDED = {
    "greaser": [
        ("td_g1", "You never feel it.\n\nThe runway comes up, the sink stops, and "
                 "somewhere in the last few feet the wheels simply start turning "
                 "— **{sink:,.0f} feet a minute**, which is to say almost "
                 "nothing at all. The nose settles. Somebody in the back "
                 "applauds, and for once they are right to.\n\n"
                 "**{field}** — {centreline:,.0f} ft off the centreline, "
                 "{runway_left:,.0f} ft of runway still ahead.\n\n"
                 "**— SIMULATION ENDED: LANDED —**"),
        ("td_g2", "A greaser.\n\nThe main gear kisses the concrete at "
                 "**{sink:,.0f} fpm** and the transition from flying to rolling "
                 "happens without a seam in it. The spoilers deploy, the nose "
                 "comes down, and the {aircraft} is a ground vehicle again.\n\n"
                 "**{field}** — {runway_left:,.0f} ft remaining.\n\n"
                 "**— SIMULATION ENDED: LANDED —**"),
    ],
    "normal landing": [
        ("td_n1", "The wheels find the runway.\n\n**{sink:,.0f} feet a minute**, "
                 "{td_ias:,.0f} knots, {centreline:,.0f} ft off the centreline "
                 "— a clean, unremarkable arrival, which is the highest praise "
                 "there is for a landing. The airframe settles onto its gear, "
                 "the spoilers come up, and the deceleration presses you gently "
                 "into the straps.\n\n**{field}**, {runway_left:,.0f} ft "
                 "remaining.\n\n**— SIMULATION ENDED: LANDED —**"),
        ("td_n2", "Down, and properly done.\n\nA solid, positive touchdown at "
                 "**{sink:,.0f} fpm** and {vref_pct:.0f}% of Vref. The nose "
                 "lowers, reverse thrust builds to a roar behind you, and the "
                 "runway lights slow from a blur to a procession.\n\n"
                 "**{field}** — {runway_left:,.0f} ft of runway to spare.\n\n"
                 "**— SIMULATION ENDED: LANDED —**"),
    ],
    "firm landing": [
        ("td_f1", "It arrives.\n\n**{sink:,.0f} feet a minute** is firm — the "
                 "kind of touchdown that goes through the airframe as a single "
                 "hard thump and makes the overhead bins complain. Nothing is "
                 "broken. Nobody is impressed.\n\n**{field}**, "
                 "{runway_left:,.0f} ft remaining.\n\n"
                 "**— SIMULATION ENDED: LANDED —**"),
    ],
    "hard landing": [
        ("td_h1", "You arrive rather than land.\n\n**{sink:,.0f} feet a minute** "
                 "into the concrete: the gear compresses to its stops, the whole "
                 "aeroplane bangs and rings, and loose articles leave the "
                 "shelves. The oleos survive it. The engineers will want to look "
                 "at them anyway, and a hard-landing report writes itself.\n\n"
                 "**{field}** — {runway_left:,.0f} ft remaining.\n\n"
                 "**— SIMULATION ENDED: LANDED (HARD) —**"),
    ],
    "runway excursion": [
        ("td_e1", "You are down, but not where you meant to be.\n\nThe wheels "
                 "meet the ground **{centreline:,.0f} feet** off the centreline "
                 "— off the paving, onto the graded surface beside it. The ride "
                 "goes instantly from smooth to a violent rumble, mud and grass "
                 "fountaining past the windows, and the aircraft slews before it "
                 "comes to rest.\n\nIntact. Off the runway, and going nowhere "
                 "under its own power.\n\n"
                 "**— SIMULATION ENDED: RUNWAY EXCURSION —**"),
    ],
}

ENDING_OVERRUN = [
    ("o_1", "The far end arrives before the aircraft stops.\n\nThe last of the "
            "runway goes under the nose still doing better than a hundred knots, "
            "and then there is no more concrete: the {aircraft} runs off the end "
            "into the overrun, the gear ploughing furrows through soft ground "
            "until something folds and the nose drops.\n\nYou touched down "
            "{td_ias:,.0f} knots at {vref_pct:.0f}% of Vref with "
            "{runway_left:,.0f} feet in front of you. It was never going to be "
            "enough.\n\n**— SIMULATION ENDED: RUNWAY OVERRUN —**"),
]

ENDING_BOTCHED_LANDING = [
    ("b_1", "It comes apart on the runway.\n\n{td_reason}.\n\nThe {aircraft} "
            "slews, drops a wing, and grinds to a halt in a spreading cloud of "
            "dust and fuel vapour a long way from where the flight plan said it "
            "would stop. **{field}** will be closed for some time.\n\n"
            "**— SIMULATION ENDED: LANDING ACCIDENT —**"),
]

ENDING_PILOT = [
    ("e_p1", "You bring the thrust levers back and the simulation freezes around you "
             "— the {aircraft} suspended at {alt:,.0f} feet, {ias:,.0f} knots, the "
             "ridges of {impact_feature} holding their shadows below.\n\n"
             "**— SIMULATION ENDED —**"),
]


# ---------------------------------------------------------------------------
# Corpus expansion
# ---------------------------------------------------------------------------
#
# Kept as extensions rather than folded into the literals above so the shape of
# each corpus stays readable at the top of the file. The weighting is
# deliberate: the low-altitude and critical bands were thinnest and are where a
# pilot spends the most attention, so they get the most new writing.

SKY[HIGH]["clear"].extend([
    ("h_c7", "Contrails from something far above cross the sky in two dead "
             "straight lines, already fraying at the edges. Below them the air "
             "is glass-clear all the way down to a landscape that looks less "
             "like country than like a relief model of it."),
    ("h_c8", "A river of cumulus runs along a valley thirty miles off, marking "
             "where the warm air is rising, each cloud anchored over its own "
             "patch of ground and drifting only slowly. Everything else is "
             "empty blue."),
    ("h_c9", "The horizon is so sharp it looks cut. At {alt:,.0f} feet the "
             "atmosphere has thinned to the point where distance stops adding "
             "haze, and forty miles of country arrives with the same clarity "
             "as four."),
])

SKY[HIGH]["crosswind"].extend([
    ("h_x5", "Cloud shadows race across the ground far faster than they have "
             "any right to — the whole sky is on the move, and the landscape "
             "beneath it is being strobed by it."),
    ("h_x6", "Wave cloud stands in rank after motionless rank downwind of the "
             "range, each bar smooth and lens-shaped and utterly still while "
             "sixty knots of air pours through it."),
    ("h_x7", "Dust is up over the dry country to the {away_side}, a brown pall "
             "leaning away from the wind, and where it thins you can see the "
             "wind lines combed into the sand beneath."),
])

SKY[HIGH]["stormy"].extend([
    ("h_s5", "The cell ahead has an overshooting top — a dome of cloud punched "
             "up through its own anvil by an updraught strong enough to beat "
             "the stratosphere. Whatever is happening inside it, you want no "
             "part of."),
    ("h_s6", "Beneath the anvil the light has failed to a dim green, and the "
             "rain shafts hang from the cloud base in visible grey columns, "
             "leaning as they fall."),
    ("h_s7", "Static crackles continuously in the headset. Between the "
             "discharges the windscreen carries a shifting blue filigree of St "
             "Elmo's fire that reassembles itself every time it is wiped away "
             "by the rain."),
])

SKY[HIGH]["foggy"].extend([
    ("h_f5", "The fog surface below has a slow swell to it, like a sea "
             "photographed at a very long exposure, and where it laps against "
             "the higher ground it is piled into a soft white surf."),
    ("h_f6", "Your own shadow sits on the cloud top far below, ringed by a "
             "faint circular rainbow that keeps perfect station with the "
             "aircraft. A glory. It will follow you the whole way."),
    ("h_f7", "Above the fog the air is astonishingly clear and completely "
             "empty. There is no ground, no horizon in the usual sense, and no "
             "reference at all except that flat white plain and the sun."),
])

SKY[LOW]["clear"].extend([
    ("l_c5", "Individual trees now. Individual field walls. The ground has "
             "stopped being a texture and become a place, with the particular "
             "untidy specificity of somewhere real."),
    ("l_c6", "Sunlight comes off a river directly below in a single unbearable "
             "flash and is gone, and for a second afterwards there is a purple "
             "shape burnt into the middle of the windscreen."),
    ("l_c7", "The shadow of the aircraft runs along the slope to the "
             "{away_side}, small and sharp and keeping exact pace, rippling as "
             "it crosses gullies."),
    ("l_c8", "At {agl:,.0f} feet the scale finally lands. That is not a hill: "
             "it is two thousand feet of mountainside, and you are looking up "
             "at the top of it."),
])

SKY[LOW]["crosswind"].extend([
    ("l_x4", "A plume of spray tears off the top of a waterfall on the "
             "{away_side} wall and never reaches the bottom — the wind takes it "
             "sideways and disperses it into nothing halfway down."),
    ("l_x5", "Trees on the exposed shoulder are all leaning the same way, "
             "permanently, shaped by years of this. The living ones have given "
             "up growing into the wind entirely."),
    ("l_x6", "The aircraft crabs along the valley at {drift_abs:.0f} degrees to "
             "its own track, so the far wall appears to slide past the nose at "
             "an angle that makes no sense until you remember why."),
    ("l_x7", "Every gust arrives first as a visible dark patch racing across "
             "the grass below and then, a beat later, as a thump through the "
             "airframe."),
])

SKY[LOW]["stormy"].extend([
    ("l_s4", "Cloud is tearing across the ridge line above at a speed that "
             "makes the mountain itself seem to be moving. Rain hammers, "
             "eases, hammers again."),
    ("l_s5", "Lightning strikes the high ground somewhere off the "
             "{away_side}, close enough that the flash and the crack arrive "
             "together, and for an instant the whole valley is lit flat white "
             "with no shadows in it at all."),
    ("l_s6", "The valley floor is a torrent. Every gully is running, the river "
             "is brown and over its banks, and the rock faces are sheeted with "
             "water that the wind lifts back off them in smoke."),
    ("l_s7", "Hail, briefly — a rattling roar across the windscreen and the "
             "leading edges, loud enough to drown the engines, and then rain "
             "again as suddenly as it started."),
])

SKY[LOW]["foggy"].extend([
    ("l_f4", "The fog is thinner in patches here and thicker in others, so the "
             "world arrives in fragments: a wall of rock, gone; a stand of "
             "trees below the wingtip, gone; grey."),
    ("l_f5", "The landing lights make it worse. All they do is light the fog "
             "itself, throwing back a solid luminous wall a few feet ahead of "
             "the aircraft."),
    ("l_f6", "Somewhere in this there is a valley with sides. You are flying "
             "between them on the strength of a terrain readout and an "
             "assumption."),
    ("l_f7", "Water streams backwards across the windows in fine horizontal "
             "threads. Beyond them, at {agl:,.0f} feet above ground, there is "
             "nothing to see and no way to know that until you can."),
])

SKY[CRITICAL]["clear"].extend([
    ("c_c4", "Every rock below has a shadow and you can see the shape of each "
             "one. That is the wrong amount of detail to be able to make out "
             "from an airliner."),
    ("c_c5", "The ground fills the windscreen from edge to edge and the sky is "
             "a strip along the top. Whatever happens next happens in the next "
             "few seconds."),
    ("c_c6", "A hillside goes past the {away_side} window close enough that "
             "the individual trees separate out and flick by one at a time."),
    ("c_c7", "The shadow of the aircraft is no longer a shape running along "
             "the ground somewhere below. It is directly beneath, and it is the "
             "same size as the aircraft."),
    ("c_c8", "Birds. Below and to the {away_side}, scattering. You are in "
             "their airspace now, and this is not where a hundred and forty "
             "tonnes of aeroplane belongs."),
])

SKY[CRITICAL]["crosswind"].extend([
    ("c_x3", "Rotor off the ridge slams the aircraft sideways and the valley "
             "wall jumps in the window. At {agl:,.0f} feet there is no room to "
             "absorb that."),
    ("c_x4", "The wind is pouring over the ridge above and coming down the "
             "face on the far side — the same face the aircraft is flying "
             "alongside — and it is taking you with it."),
    ("c_x5", "Grass, scree and a hard blue sky, all of it going past at an "
             "angle because the aeroplane is crabbed {drift_abs:.0f} degrees "
             "into a wind that will not let it point where it is going."),
    ("c_x6", "The wingtip on the {turn_side} is closer to the slope than "
             "anything about this situation should permit."),
    ("c_x7", "Every correction is being undone by the next gust before you "
             "have finished making it. This is not flying any more, it is "
             "arguing."),
])

SKY[CRITICAL]["stormy"].extend([
    ("c_s3", "Black rock in the lightning flashes, gone again in the dark "
             "between them, and no way to tell how far away any of it is."),
    ("c_s4", "The rain is coming off the windscreen faster than the wipers can "
             "clear it, and what little arrives through the gaps is dark, "
             "close and moving."),
    ("c_s5", "A downdraught takes the aircraft and the altimeter unwinds "
             "through numbers there is no ground clearance left to give away."),
    ("c_s6", "The whole airframe is being shaken hard enough that the "
             "instruments are difficult to read, at the exact moment when "
             "reading them is the only thing that matters."),
    ("c_s7", "Something enormous and darker than the rain fills the lower "
             "windows. It does not resolve into anything before it is gone. "
             "That is not reassuring; it means it was close."),
])

SKY[CRITICAL]["foggy"].extend([
    ("c_f3", "Grey. The radio altimeter is counting down and the grey does not "
             "change at all, which is the worst thing it could do."),
    ("c_f4", "There is no outside world. There is a windscreen with nothing "
             "behind it and a number falling toward zero."),
    ("c_f5", "For one second the fog opens and there is wet black rock, filling "
             "everything, close. Then it closes again and you are left with the "
             "memory of it and no idea whether it is still there."),
    ("c_f6", "{agl:,.0f} feet. Whatever is out there is out there whether you "
             "can see it or not, and it is not moving."),
    ("c_f7", "The instruments are the only thing in the universe that knows "
             "where the ground is. Believe them."),
])

TERRAIN[HIGH].extend([
    ("th6", "Cloud shadow and sunlight move across the high ground in slow "
            "patches, and where the light lands the rock goes from grey to a "
            "warm ochre and back again."),
    ("th7", "A lake sits in a hollow below, so still that it holds a perfect "
            "inverted copy of the peaks around it, and the two ranges meet at "
            "a shoreline that is impossible to place."),
    ("th8", "The country is on the turn ahead: the rolling ground gives out "
            "and something serious begins, {ridge_name} at {ridge_ft:,.0f} "
            "feet standing at the head of it like a gate."),
    ("th9", "Old glacial valleys run parallel below, all scooped to the same "
            "U-section by the same ice, all pointing the same way — a "
            "signature left ten thousand years ago and still perfectly legible "
            "from up here."),
])

TERRAIN[LOW].extend([
    ("tl6", "The valley narrows ahead. The walls converge and the gap between "
            "them is not obviously wider than the wingspan, which is the sort "
            "of judgement best made early."),
    ("tl7", "A spur juts into the valley from the {away_side}, forcing the "
            "track around it, and the ground rises to {ridge_ft:,.0f} feet "
            "immediately behind."),
    ("tl8", "Terraces of old moraine step down the valley side, each one "
            "sharp-edged and level, and the aircraft crosses them one after "
            "another with the AGL readout stepping down in sympathy."),
    ("tl9", "A road threads the valley floor {terrain_ft:,.0f} feet below, "
            "switchbacking up the far side in a dozen hairpins, and a vehicle "
            "on it is a bright moving dot small enough to lose."),
])

TERRAIN[CRITICAL].extend([
    ("tc5", "The gap ahead is a notch in the ridge, and it is narrow, and it "
            "is the only thing lower than {ridge_ft:,.0f} feet anywhere in the "
            "windscreen."),
    ("tc6", "Ground either side, ground below, and a ridge across the front at "
            "{ridge_nm:.1f} miles. The options are running out in every "
            "direction but up."),
    ("tc7", "Rock, close enough to see the water running down it. The "
            "clearance is {agl:,.0f} feet and the trend is the wrong way."),
    ("tc8", "You are below the tops. The horizon is not the horizon any more, "
            "it is the ridge line, and it is above you on both sides."),
])

TERRAIN_OBSCURED[HIGH].extend([
    ("oh4", "Under all that white there are valleys and there are mountains, "
            "and from here they look identical: a flat, calm, entirely "
            "featureless surface with nine thousand feet of difference hidden "
            "underneath it."),
    ("oh5", "{ridge_name} is the only thing that has managed to break through, "
            "a black wedge {ridge_nm:.1f} miles ahead standing out of the "
            "cloud like a rock at low tide. Its neighbours are down there too."),
    ("oh6", "The terrain display is drawing a picture the windscreen refuses "
            "to confirm. One of them is right, and it is not the windscreen."),
])

TERRAIN_OBSCURED[LOW].extend([
    ("ol4", "Down here the fog and the ground have the same colour and the "
            "same texture, and the transition between them will not announce "
            "itself."),
    ("ol5", "{ridge_ft:,.0f} feet of rock, {ridge_nm:.1f} miles ahead, "
            "completely invisible. The instruments are not guessing; they know."),
    ("ol6", "Something passes below the wing — a darker patch in the grey, "
            "there and gone. Tree tops, probably. Probably."),
])

TERRAIN_OBSCURED[CRITICAL].extend([
    ("oc3", "{agl:,.0f} feet, and the only difference between flying and not "
            "flying is a number on a screen."),
    ("oc4", "The grey outside is completely uniform, and will stay completely "
            "uniform, right up until it is rock."),
    ("oc5", "The GPWS is the only thing in the aircraft that can see, and it "
            "does not like what it can see."),
])

MOTION["level"].extend([
    ("m_l4", "Hands off, near enough. The aircraft is doing exactly what it was "
             "last asked to do and shows no interest in doing anything else."),
    ("m_l5", "{ias:,.0f} knots, {alt:,.0f} feet, heading {heading:03.0f}. Three "
             "numbers, all of them steady, and the small satisfaction of an "
             "aeroplane in trim."),
    ("m_l6", "The engines have settled into that particular unvarying note that "
             "means nothing needs attention, and the airframe is quiet around "
             "it."),
])

MOTION["climbing"].extend([
    ("m_u4", "Up. {vs:+,.0f} feet a minute, and the altimeter needle sweeping "
             "round with the steady patience of an aeroplane doing the one "
             "thing it most wants to do."),
    ("m_u5", "The ground is letting go. Detail flattens, shadows shorten, and "
             "what was terrain becomes topography."),
    ("m_u6", "Nose {pitch_abs:.1f} degrees up, {ias:,.0f} knots and bleeding "
             "slowly — every foot of altitude is being bought with airspeed, "
             "which is the only currency there is."),
])

MOTION["descending"].extend([
    ("m_d4", "Down at {vs_abs:,.0f} a minute. Detail returns to the ground the "
             "way focus returns to a lens, all at once and faster than "
             "expected."),
    ("m_d5", "The nose is down and the aircraft has gone quiet in the way that "
             "means it is accelerating: {ias:,.0f} knots and climbing, engines "
             "back, wind noise taking over."),
    ("m_d6", "Descending through {alt:,.0f} feet. The ground clearance is "
             "{agl:,.0f} feet and that is the number that matters now, not the "
             "altimeter."),
])

MOTION["turning"].extend([
    ("m_t4", "Coming {turn_side} with {bank_word}. The wingtip traces a slow "
             "arc across the terrain and the heading unwinds through "
             "{heading:03.0f}."),
    ("m_t5", "In the turn the seat pushes up at {load:.2f} g and the horizon "
             "sits across the windscreen at an angle. The {turn_side} wing "
             "points at ground you would rather it did not."),
    ("m_t6", "The aircraft is banked {bank_abs:.0f} degrees and carving round, "
             "and the whole landscape is rotating slowly about a point "
             "somewhere off the {turn_side} wingtip."),
])

MOTION["stalled"].extend([
    ("m_s3", "The controls have gone dead in your hands. The aircraft is "
             "descending in a mush, nose high, buffeting, and none of the "
             "usual inputs mean anything until the wing is flying again."),
    ("m_s4", "Angle of attack {alpha:.1f} degrees. The wing gave up somewhere "
             "back there and everything since has been argument with physics."),
    ("m_s5", "Stalled and sinking. The only thing that will fix this is "
             "lowering the nose, and the only thing that costs is height."),
])

SENSORY["clear"].extend([
    ("s_c4", "Warm sun through the side window, the smell of the air "
             "conditioning, and the deep unvarying note of two engines doing "
             "nothing difficult."),
    ("s_c5", "A cup on the coaming has not moved in ten minutes. That is the "
             "kind of day it is."),
    ("s_c6", "The airframe makes small sounds as it settles — a tick from the "
             "structure somewhere aft, the faint sigh of the packs — and "
             "nothing else."),
])

SENSORY["crosswind"].extend([
    ("s_x4", "The aircraft never quite settles. There is always a small "
             "correction going in, and always another one needed a second "
             "later."),
    ("s_x5", "A gust hits hard enough to lift the charts off the pedestal, and "
             "the wing drops fifteen degrees before it is caught."),
    ("s_x6", "The controls are alive under your hand, loading and unloading as "
             "the air changes its mind."),
])

SENSORY["stormy"].extend([
    ("s_s4", "The airframe is groaning. Not creaking: groaning, a long low "
             "structural complaint from somewhere in the wing root every time "
             "the aircraft is hit."),
    ("s_s5", "The lightning is close enough now that the flash and the noise "
             "arrive as a single event, felt as much through the seat as heard."),
    ("s_s6", "You have both hands on the controls and are getting nowhere. The "
             "aeroplane is going where the air puts it and your inputs are "
             "suggestions."),
])

SENSORY["foggy"].extend([
    ("s_f4", "The silence is the strange part. Extreme danger is supposed to "
             "be loud, and this is the quietest flying you have ever done."),
    ("s_f5", "Your eyes keep going to the windscreen out of habit and keep "
             "finding nothing, and each time it takes a moment to drag them "
             "back to where the information actually is."),
    ("s_f6", "The panel glow is the brightest thing in the world. Beyond the "
             "glass there is a grey that could be six inches away or six "
             "miles."),
])

THREAT["stall"].extend([
    ("x_st3", "**STALL.** The stick shaker is a physical hammering through the "
              "controls. Nose down, wings level, thrust up — and accept the "
              "height it costs, because the alternative costs all of it."),
])
THREAT["pull_up"].extend([
    ("x_pu3", "**PULL UP.** Maximum thrust, wings level, pull. Not a turn — a "
              "turn costs lift you do not have. Straight up, now."),
])
THREAT["terrain"].extend([
    ("x_tr3", "The terrain ahead is higher than you are and {ridge_nm:.1f} "
              "miles away at {gs:,.0f} knots over the ground. That is under two "
              "minutes to have made a decision in."),
])
THREAT["overspeed"].extend([
    ("x_os3", "**OVERSPEED.** {ias:,.0f} knots against a limit of rather less "
              "than that. Thrust back, nose up, gently — a sharp pull at this "
              "speed is its own emergency."),
])
THREAT["low_speed"].extend([
    ("x_ls3", "The speed tape is shrinking toward the amber. {ias:,.0f} knots "
              "with the wing letting go at {stall_ias:,.0f}, and the controls "
              "have gone vague."),
])
THREAT["overstress"].extend([
    ("x_ov2", "**{load:.2f} g.** The wings are visibly bowed and the airframe "
              "is telling you about it. Unload."),
])
THREAT["engines_out"].extend([
    ("x_eo2", "**Silence.** Both engines out, the windmilling rumble the only "
              "thing left, and every foot of altitude now buys about two miles "
              "of glide. Spend it deliberately."),
])
THREAT["rotor"].extend([
    ("x_ro3", "The lee of the ridge is a washing machine. Nothing about the "
              "next thirty seconds is going to be smooth, and the ground is "
              "{agl:,.0f} feet below."),
])
THREAT["downdraught"].extend([
    ("x_dw2", "The air is going down at {wave_abs:,.0f} feet a minute and it "
              "does not care what the throttles are doing. Turn toward the "
              "lower ground and fly out of it sideways — you will not outclimb "
              "it."),
])
THREAT["low_fuel"].extend([
    ("x_lf2", "{fuel_pct:.1f}% remaining. The question has changed from where "
              "you would like to land to where you can."),
])

LIGHT["dawn"].append(
    ("li_d3", "The sky is going from black to a deep transparent blue and the "
              "brightest stars are still holding on in it. Below, nothing is "
              "lit yet at all.")
)
LIGHT["golden"].append(
    ("li_g3", "Everything has an edge of gold on the sunward side and is "
              "practically black on the other. The country has more relief in "
              "it now than it will have all day.")
)
LIGHT["dusk"].append(
    ("li_k3", "The shadow of the range is racing east across the plain below, "
              "eating the light as it goes, and it will reach the horizon in "
              "about a minute.")
)
LIGHT["night"].append(
    ("li_n4", "The cockpit is red-lit and everything beyond the glass is black. "
              "It is {hour}. The instruments and the engines are the entire "
              "world.")
)


# Flying an approach is a distinct register: the outside view stops being
# scenery and becomes a set of cues.
APPROACH = [
    ("ap_1", "The runway is a pale grey stripe laid across the valley floor "
             "ahead, foreshortened almost to a line from here, and it does not "
             "look nearly long enough. It never does."),
    ("ap_2", "Configured and committed: gear down, flaps out, the aircraft "
             "slow and stable and heavy-feeling in the way a jet gets when it "
             "is doing the one thing it is worst at."),
    ("ap_3", "Approach lights, sequenced, running toward the threshold in a "
             "chain of white that pulls the eye down the last mile."),
    ("ap_4", "The picture is steady: the runway sitting still in the "
             "windscreen, neither rising nor falling. Hold that and the "
             "arithmetic takes care of itself."),
    ("ap_5", "Vref is {vref:,.0f} knots and the wing is telling you about every one "
             "of them. Down here the margins are counted in single figures."),
    ("ap_6", "The threshold markings resolve, then the touchdown zone stripes, "
             "then the individual joints in the concrete. Each one arrives "
             "faster than the last."),
    ("ap_7", "Ground rush begins — that sudden acceleration of everything in "
             "the periphery that means the last hundred feet has started."),
    ("ap_8", "The aircraft is crabbed into the wind and the runway is arriving "
             "slightly sideways. That will have to be taken out, and taken out "
             "late."),
    ("ap_9", "Over the threshold. Power coming back, the nose coming up, and "
             "the runway widening out beneath into something that finally "
             "looks like somewhere you could land."),
]

ROLLOUT = [
    ("ro_1", "Down. The rumble of the mains on concrete, the nose lowering, "
             "and the spoilers standing up on the wing to kill what is left of "
             "the lift."),
    ("ro_2", "Reverse thrust builds to a roar behind you and the deceleration "
             "presses you forward into the straps. The runway edge lights slow "
             "from a blur into a procession."),
    ("ro_3", "The centreline stripes stop flicking past and start passing, and "
             "then start crawling. {ias:,.0f} knots and falling."),
    ("ro_4", "Brakes on. The aircraft is heavy and it does not want to stop, "
             "and the far end of the runway is closer than it was."),
    ("ro_5", "Rolling out, straight and slowing. The valley walls that were an "
             "obstacle five minutes ago are just scenery again."),
    ("ro_6", "The end of the runway approaches at a rate that is either fine "
             "or is not, and there will not be much warning about which."),
]
