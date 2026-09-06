"""Natural-language flight command parsing.

Matches the way a pilot would actually phrase it -- "increase throttle 10%",
"pitch nose down 5 degrees", "turn left heading 180" -- and returns a normalised
Command. Unrecognised input costs no simulation time; the loop hands back a hint
instead of burning a tick.
"""

import re
from dataclasses import dataclass

from . import aircraft as fleet
from . import autopilot
from .physics import clamp, wrap360


@dataclass
class Command:
    kind: str
    value: float = 0.0
    text: str = ""
    advances_time: bool = True
    seconds: float = None
    target: str = ""


class ParseError(Exception):
    """Raised with a helpful message when input cannot be understood."""


_NUMBER = r"(-?\d+(?:\.\d+)?)"

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def _normalise(text):
    lowered = text.strip().lower()
    lowered = lowered.replace("degrees", "").replace("degree", "")
    lowered = lowered.replace("percent", "%")
    for word, number in _WORD_NUMBERS.items():
        lowered = re.sub(r"\b{}\b".format(word), str(number), lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def parse(raw):
    """Parse one line of pilot input into a Command. Raises ParseError."""
    if raw is None or not raw.strip():
        raise ParseError("No command entered. Type `help` for the command list.")

    text = _normalise(raw)

    for matcher in _MATCHERS:
        command = matcher(text, raw)
        if command is not None:
            return command

    raise ParseError(
        "Unrecognised command: `{}`. Type `help` to see what the aircraft "
        "will accept.".format(raw.strip())
    )


# ---------------------------------------------------------------------------
# Matchers. Each returns a Command or None. Order matters -- more specific
# patterns are registered first.
# ---------------------------------------------------------------------------


def _match_meta(text, raw):
    if text in ("help", "?", "commands"):
        return Command("help", text=raw, advances_time=False)
    if text in ("status", "instruments", "panel", "report"):
        return Command("status", text=raw, advances_time=False)
    if text in ("map", "terrain", "chart", "plan view", "nav display", "nd"):
        return Command("map", text=raw, advances_time=False)
    if text in ("airfields", "airports", "fields", "nearest airfield",
                "nearest airport", "divert options"):
        return Command("airfields", text=raw, advances_time=False)
    if text in ("quit", "exit", "end", "eject", "stop"):
        return Command("quit", text=raw, advances_time=False)
    # `spec` alone is the aircraft you are flying; `spec a380` is any other.
    # Registered here, ahead of the navigation matcher, whose `fly <target>`
    # pattern would otherwise swallow "spec" as a destination name.
    m = re.match(
        r"^spec(?:s|ification)?(?:\s+(?:card|sheet))?(?:\s+(.{1,20}))?$", text
    )
    if m:
        return Command(
            "spec", text=raw, advances_time=False,
            target=(m.group(1) or "").strip(),
        )
    if text in ("fleet", "aircraft", "types", "fleet menu"):
        return Command("fleet", text=raw, advances_time=False)
    return None


def _match_wait(text, raw):
    m = re.match(r"^(?:wait|hold|maintain|continue|steady|carry on)$", text)
    if m:
        return Command("hold", text=raw)
    m = re.match(r"^(?:wait|hold|fly on|continue)\s+(?:for\s+)?" + _NUMBER + r"\s*(s|sec|secs|seconds|m|min|mins|minutes)?$", text)
    if m:
        amount = float(m.group(1))
        unit = m.group(2) or "s"
        seconds = amount * 60.0 if unit.startswith("m") else amount
        return Command("hold", text=raw, seconds=clamp(seconds, 1.0, 600.0))
    return None


def _match_throttle(text, raw):
    if re.match(r"^(?:toga|full power|firewall|max power|max thrust)$", text):
        return Command("throttle_set", 100.0, raw)
    if re.match(r"^(?:idle|flight idle|cut power|throttle idle)$", text):
        return Command("throttle_set", 0.0, raw)
    if re.match(r"^(?:climb (?:power|thrust))$", text):
        return Command("throttle_set", 92.0, raw)
    if re.match(r"^(?:cruise (?:power|thrust))$", text):
        return Command("throttle_set", 72.0, raw)

    m = re.match(
        r"^(?:increase|add|raise|more|up|advance)\s+(?:the\s+)?"
        r"(?:throttle|power|thrust)\s*(?:by\s+)?" + _NUMBER + r"\s*%?$",
        text,
    )
    if m:
        return Command("throttle_delta", float(m.group(1)), raw)

    m = re.match(
        r"^(?:decrease|reduce|lower|less|cut|pull back|retard)\s+(?:the\s+)?"
        r"(?:throttle|power|thrust)\s*(?:by\s+)?" + _NUMBER + r"\s*%?$",
        text,
    )
    if m:
        return Command("throttle_delta", -float(m.group(1)), raw)

    m = re.match(
        r"^(?:set\s+)?(?:the\s+)?(?:throttle|power|thrust)\s*(?:to\s+)?"
        + _NUMBER + r"\s*%?$",
        text,
    )
    if m:
        return Command("throttle_set", float(m.group(1)), raw)
    return None


def _match_pitch(text, raw):
    if re.match(r"^(?:level|level off|level out|level flight|straight and level|"
                r"level the (?:wings|aircraft)|wings level)$", text):
        return Command("level", text=raw)

    m = re.match(
        r"^(?:pitch|nose|pull)\s*(?:the\s+)?(?:nose\s*)?"
        r"(up|down|over)\s*(?:by\s+)?" + _NUMBER + r"?$",
        text,
    )
    if m:
        amount = float(m.group(2)) if m.group(2) else 5.0
        sign = 1.0 if m.group(1) == "up" else -1.0
        return Command("pitch_delta", sign * amount, raw)

    m = re.match(r"^(?:pull up|climb)\s*(?:by\s+)?" + _NUMBER + r"?$", text)
    if m:
        return Command("pitch_delta", float(m.group(1)) if m.group(1) else 8.0, raw)

    m = re.match(r"^(?:descend|dive|push over|nose over)\s*(?:by\s+)?" + _NUMBER + r"?$", text)
    if m:
        return Command("pitch_delta", -(float(m.group(1)) if m.group(1) else 5.0), raw)

    m = re.match(
        r"^(?:set\s+)?pitch\s*(?:to\s+|attitude\s+)?" + _NUMBER + r"$", text
    )
    if m:
        return Command("pitch_set", float(m.group(1)), raw)
    return None


def _match_lateral(text, raw):
    # "turn left heading 180" / "turn to heading 090" / "heading 270"
    m = re.match(
        r"^(?:turn|come|fly|steer|go)?\s*(?:left|right|port|starboard)?\s*"
        r"(?:to\s+)?(?:heading|hdg|course)\s*(\d{1,3})$",
        text,
    )
    if m:
        return Command("heading", wrap360(float(m.group(1))), raw)

    m = re.match(r"^(?:heading|hdg)\s*(\d{1,3})$", text)
    if m:
        return Command("heading", wrap360(float(m.group(1))), raw)

    if re.match(r"^(?:roll level|wings level|level the wings|stop turn|"
                r"roll out|centre the stick|center the stick)$", text):
        return Command("bank_set", 0.0, raw)

    # "bank left 25" / "turn right 30"
    m = re.match(
        r"^(?:bank|roll|turn)\s+(left|right|port|starboard)\s*(?:by\s+)?"
        + _NUMBER + r"?$",
        text,
    )
    if m:
        amount = float(m.group(2)) if m.group(2) else 25.0
        sign = -1.0 if m.group(1) in ("left", "port") else 1.0
        return Command("bank_set", sign * amount, raw)

    # "turn left" with no angle -> a standard 25 degree turn
    m = re.match(r"^(?:turn|bank|roll)\s+(left|right|port|starboard)$", text)
    if m:
        sign = -1.0 if m.group(1) in ("left", "port") else 1.0
        return Command("bank_set", sign * 25.0, raw)

    # "turn left 40 degrees" meaning a heading change, not a bank angle
    m = re.match(
        r"^(?:turn)\s+(left|right)\s+" + _NUMBER + r"\s*(?:deg)?\s*"
        r"(?:of heading|heading change)$",
        text,
    )
    if m:
        sign = -1.0 if m.group(1) == "left" else 1.0
        return Command("heading_delta", sign * float(m.group(2)), raw)
    return None


def _match_rudder(text, raw):
    if re.match(r"^(?:centre|center|neutral|zero)\s+(?:the\s+)?rudder$", text) or \
       re.match(r"^rudder\s+(?:centre|center|neutral|zero|off)$", text):
        return Command("rudder_set", 0.0, raw)

    m = re.match(
        r"^(?:full\s+)?(left|right)\s+rudder$", text
    )
    if m:
        sign = -1.0 if m.group(1) == "left" else 1.0
        return Command("rudder_set", sign * 30.0, raw)

    m = re.match(
        r"^rudder\s+(left|right)\s*(?:by\s+)?" + _NUMBER + r"?$", text
    )
    if m:
        amount = float(m.group(2)) if m.group(2) else 15.0
        sign = -1.0 if m.group(1) == "left" else 1.0
        return Command("rudder_set", sign * amount, raw)

    m = re.match(r"^(?:set\s+)?rudder\s*(?:to\s+)?" + _NUMBER + r"$", text)
    if m:
        return Command("rudder_set", float(m.group(1)), raw)
    return None


def _match_engines(text, raw):
    m = re.match(
        r"^(?:shut ?down|kill|fail|cut)\s+(?:the\s+)?engine\s*(\d)$", text
    )
    if m:
        return Command("engine_fail", float(m.group(1)) - 1, raw)

    if re.match(r"^engine\s*(?:failure|out|fire)$", text):
        # No engine named: fail the leftmost, the classic asymmetric case.
        return Command("engine_fail", 0.0, raw)

    if re.match(r"^(?:restart|relight|restore)\s+(?:all\s+)?engines?$", text):
        return Command("engine_restart", text=raw)
    return None


def _match_autopilot(text, raw):
    if re.match(r"^(?:autopilot|ap)\s*(?:on|engage|engaged)$", text):
        return Command("ap_on", text=raw, advances_time=False)
    if re.match(r"^(?:autopilot|ap)\s*(?:off|disengage|disconnect)$", text):
        return Command("ap_off", text=raw, advances_time=False)

    m = re.match(
        r"^(?:set\s+|maintain\s+|climb to\s+|descend to\s+)?"
        r"(?:altitude|alt|flight level|fl)\s*(?:to\s+)?" + _NUMBER + r"$",
        text,
    )
    if m:
        value = float(m.group(1))
        # "FL350" and "flight level 350" mean 35,000 ft.
        if value < 600 and re.search(r"flight level|\bfl\b", text):
            value *= 100.0
        return Command("ap_altitude", value, raw, advances_time=False)

    m = re.match(
        r"^(?:set\s+|maintain\s+|hold\s+)?(?:speed|ias|airspeed)\s*"
        r"(?:to\s+)?" + _NUMBER + r"$",
        text,
    )
    if m:
        return Command("ap_speed", float(m.group(1)), raw, advances_time=False)

    m = re.match(
        r"^(?:set\s+)?(?:vertical speed|v/s|vs|climb rate)\s*(?:to\s+)?"
        + _NUMBER + r"$",
        text,
    )
    if m:
        return Command("ap_vs", float(m.group(1)), raw, advances_time=False)

    if re.match(r"^(?:arm\s+)?(?:approach|appr|ils)(?:\s+mode)?$", text):
        return Command("ap_approach", text=raw, advances_time=False)
    return None


def _match_time_of_day(text, raw):
    m = re.match(r"^(?:set\s+)?(?:local\s+)?time\s+(?:to\s+)?(\d{1,2}):?(\d{2})?$", text)
    if m:
        hours = float(m.group(1)) + (float(m.group(2)) / 60.0 if m.group(2) else 0.0)
        return Command("time_of_day", hours % 24.0, raw, advances_time=False)
    for word, hour in (("dawn", 5.5), ("sunrise", 6.0), ("midday", 12.0),
                       ("noon", 12.0), ("dusk", 18.0), ("sunset", 18.2),
                       ("night", 22.0), ("midnight", 0.0)):
        if text in (word, "set " + word, "fly at " + word):
            return Command("time_of_day", hour, raw, advances_time=False)
    return None


def _match_navigation(text, raw):
    # `to` is optional throughout rather than a separate alternative: regex
    # alternation is ordered, so a bare `direct` would match first and leave
    # "to kebr" as the target.
    m = re.match(
        r"^(?:direct|fly|proceed|divert|go|goto|"
        r"set (?:course|destination))(?:\s+(?:to|for))?\s+"
        r"(?!heading|hdg|course|altitude|speed|level)([a-z0-9 ]{2,30})$",
        text,
    )
    if m:
        return Command("direct_to", 0.0, raw, advances_time=False,
                       target=m.group(1).strip())
    if re.match(r"^(?:show |flight )?(?:plan|route|nav|destination)$", text):
        return Command("show_plan", text=raw, advances_time=False)
    if re.match(r"^(?:clear|cancel|delete)\s+(?:the\s+)?(?:plan|route)$", text):
        return Command("clear_route", text=raw, advances_time=False)
    if re.match(r"^(?:debrief|summary|how did i do)$", text):
        return Command("debrief", text=raw, advances_time=False)
    return None


def _match_ground(text, raw):
    m = re.match(r"^(?:apply\s+)?(?:max|maximum|full)\s+(?:wheel\s+)?brakes?$", text)
    if m:
        return Command("brakes", 1.0, raw)
    if re.match(r"^(?:brakes?|apply brakes?|braking)$", text):
        return Command("brakes", 0.7, raw)
    if re.match(r"^(?:release|off)\s+brakes?$", text) or \
       re.match(r"^brakes?\s+(?:off|release)$", text):
        return Command("brakes", 0.0, raw)
    m = re.match(r"^brakes?\s+" + _NUMBER + r"\s*%?$", text)
    if m:
        return Command("brakes", float(m.group(1)) / 100.0, raw)

    if re.match(r"^(?:reverse|reverse thrust|reversers?|thrust reverse)"
                r"(?:\s+(?:on|out|deploy|select))?$", text):
        return Command("reverse", 1.0, raw)
    if re.match(r"^(?:stow|cancel|stow the)\s+(?:reversers?|reverse)$", text) or \
       re.match(r"^reverse(?:rs)?\s+(?:off|stow|in)$", text):
        return Command("reverse", 0.0, raw)
    return None


def _match_config(text, raw):
    m = re.match(r"^(?:set\s+)?flaps?\s*(up|0|1|2|3|full|4)$", text)
    if m:
        token = m.group(1)
        setting = 0 if token == "up" else (4 if token == "full" else int(token))
        return Command("flaps", float(setting), raw)

    if re.match(r"^(?:gear|landing gear)\s*(?:down|extend)$", text):
        return Command("gear", 1.0, raw)
    if re.match(r"^(?:gear|landing gear)\s*(?:up|retract)$", text):
        return Command("gear", 0.0, raw)

    if re.match(r"^(?:speed ?brakes?|spoilers?|airbrakes?)\s*(?:out|up|extend|deploy|on)$", text):
        return Command("spoilers", 1.0, raw)
    if re.match(r"^(?:speed ?brakes?|spoilers?|airbrakes?)\s*(?:in|down|retract|stow|off)$", text):
        return Command("spoilers", 0.0, raw)
    return None


_MATCHERS = [
    _match_meta,
    _match_wait,
    _match_throttle,
    _match_pitch,
    # Rudder before lateral: "left rudder" must not be eaten by the bank patterns.
    _match_rudder,
    _match_engines,
    _match_ground,
    _match_autopilot,
    _match_time_of_day,
    # Lateral before navigation: "fly to heading 270" is a heading command, and
    # the destination pattern would otherwise swallow "heading 270" as a name.
    _match_lateral,
    _match_navigation,
    _match_config,
]


def apply(sim, command):
    """Mutate the simulator's commanded state. Does not advance time."""
    s = sim.state
    kind = command.kind

    # A manual input on a channel takes it back from the autopilot. An
    # autopilot quietly fighting the pilot for the elevator is worse than none.
    autopilot.disengage_for(s, kind)

    if kind == "throttle_set":
        s.throttle_pct = clamp(command.value, 0.0, 100.0)
    elif kind == "throttle_delta":
        s.throttle_pct = clamp(s.throttle_pct + command.value, 0.0, 100.0)
    elif kind == "pitch_set":
        s.cmd_pitch_deg = clamp(command.value, -30.0, 30.0)
    elif kind == "pitch_delta":
        s.cmd_pitch_deg = clamp(s.cmd_pitch_deg + command.value, -30.0, 30.0)
    elif kind == "level":
        s.cmd_pitch_deg = sim.level_flight_pitch_deg()
        s.cmd_bank_deg = 0.0
        s.cmd_heading_deg = None
        s.rudder_deg = 0.0
    elif kind == "bank_set":
        s.cmd_bank_deg = clamp(command.value, -60.0, 60.0)
        s.cmd_heading_deg = None
    elif kind == "heading":
        s.cmd_heading_deg = wrap360(command.value)
        if s.ap_engaged:
            s.ap_heading_deg = s.cmd_heading_deg
    elif kind == "heading_delta":
        s.cmd_heading_deg = wrap360(s.heading_deg + command.value)
    elif kind == "rudder_set":
        s.rudder_deg = clamp(command.value, -60.0, 60.0)
    elif kind == "engine_fail":
        index = int(clamp(command.value, 0, sim.aircraft.engine_count - 1))
        if index not in s.engines_failed:
            s.engines_failed.append(index)
    elif kind == "engine_restart":
        s.engines_failed.clear()
    elif kind == "brakes":
        s.brakes = clamp(command.value, 0.0, 1.0)
    elif kind == "reverse":
        s.reverse_thrust = bool(command.value)
    elif kind == "flaps":
        s.flaps = int(clamp(command.value, 0, len(fleet.FLAP_CL_BONUS) - 1))
    elif kind == "gear":
        s.gear_down = bool(command.value)
    elif kind == "spoilers":
        s.spoilers = bool(command.value)
    elif kind in ("hold", "status", "map", "airfields", "help", "quit",
                  "direct_to", "show_plan", "clear_route", "debrief",
                  "spec", "fleet"):
        pass
    elif kind == "time_of_day":
        s.time_of_day_h = command.value % 24.0
    elif kind == "ap_on":
        s.ap_engaged = True
        if s.ap_altitude_ft is None and s.ap_vs_fpm is None:
            s.ap_altitude_ft = s.altitude_ft
        if s.ap_heading_deg is None:
            s.ap_heading_deg = s.heading_deg
    elif kind == "ap_off":
        s.ap_engaged = False
        s.ap_altitude_ft = None
        s.ap_vs_fpm = None
        s.ap_heading_deg = None
        s.ap_speed_kt = None
        s.ap_approach = False
    elif kind == "ap_altitude":
        s.ap_engaged = True
        s.ap_altitude_ft = clamp(command.value, 0.0, 45000.0)
        s.ap_vs_fpm = None
        s.ap_approach = False
    elif kind == "ap_vs":
        s.ap_engaged = True
        s.ap_vs_fpm = clamp(command.value, -6000.0, 6000.0)
        s.ap_altitude_ft = None
        s.ap_approach = False
    elif kind == "ap_speed":
        s.ap_engaged = True
        s.ap_speed_kt = clamp(command.value, 100.0, 400.0)
    elif kind == "ap_approach":
        s.ap_engaged = True
        s.ap_approach = True
        pass


HELP_TEXT = """\
### Flight commands

| Intent | Examples |
| --- | --- |
| **Throttle** | `increase throttle 10%`, `reduce throttle 15`, `throttle 85`, `full power`, `idle`, `climb power` |
| **Pitch** | `pitch nose down 5`, `pitch up 3`, `set pitch 10`, `climb`, `descend`, `level off` |
| **Turning** | `turn left heading 180`, `heading 090`, `bank right 25`, `turn left`, `roll level` |
| **Rudder** | `rudder left 10`, `rudder right 5`, `full left rudder`, `centre rudder` |
| **Engines** | `engine failure`, `shutdown engine 2`, `restart engines` |
| **On the ground** | `brakes`, `max brakes`, `release brakes`, `reverse thrust`, `stow reversers` |
| **Configuration** | `flaps 1`, `flaps full`, `flaps up`, `gear down`, `gear up`, `speedbrakes out`, `speedbrakes in` |
| **Time** | `hold` (advance 10 s unchanged), `wait 60 seconds`, `wait 2 minutes` |
| **Autopilot** | `autopilot on/off`, `set altitude 12000`, `set speed 280`, `vertical speed 1500`, `approach mode` |
| **Time of day** | `time 0530`, `dawn`, `midday`, `dusk`, `night` |
| **Navigation** | `direct to KEBR`, `show plan`, `clear route`, `airfields`, `debrief` |
| **Reference** | `spec` (your aircraft's card), `spec a380`, `fleet` |
| **Other** | `map` (terrain plan view), `status`, `help`, `quit` |

Each command advances the simulation **10 seconds** unless you say otherwise.
`map`, `airfields`, `status` and `help` cost no time.\
"""
