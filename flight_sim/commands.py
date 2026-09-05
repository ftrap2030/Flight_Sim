"""Natural-language flight command parsing.

Matches the way a pilot would actually phrase it -- "increase throttle 10%",
"pitch nose down 5 degrees", "turn left heading 180" -- and returns a normalised
Command. Unrecognised input costs no simulation time; the loop hands back a hint
instead of burning a tick.
"""

import re
from dataclasses import dataclass

from . import aircraft as fleet
from .physics import clamp, wrap360


@dataclass
class Command:
    kind: str
    value: float = 0.0
    text: str = ""
    advances_time: bool = True
    seconds: float = None


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
    if text in ("quit", "exit", "end", "eject", "stop"):
        return Command("quit", text=raw, advances_time=False)
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
    _match_lateral,
    _match_config,
]


def apply(sim, command):
    """Mutate the simulator's commanded state. Does not advance time."""
    s = sim.state
    kind = command.kind

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
    elif kind == "bank_set":
        s.cmd_bank_deg = clamp(command.value, -60.0, 60.0)
        s.cmd_heading_deg = None
    elif kind == "heading":
        s.cmd_heading_deg = wrap360(command.value)
    elif kind == "heading_delta":
        s.cmd_heading_deg = wrap360(s.heading_deg + command.value)
    elif kind == "flaps":
        s.flaps = int(clamp(command.value, 0, len(fleet.FLAP_CL_BONUS) - 1))
    elif kind == "gear":
        s.gear_down = bool(command.value)
    elif kind == "spoilers":
        s.spoilers = bool(command.value)
    elif kind in ("hold", "status", "help", "quit"):
        pass


HELP_TEXT = """\
### Flight commands

| Intent | Examples |
| --- | --- |
| **Throttle** | `increase throttle 10%`, `reduce throttle 15`, `throttle 85`, `full power`, `idle`, `climb power` |
| **Pitch** | `pitch nose down 5`, `pitch up 3`, `set pitch 10`, `climb`, `descend`, `level off` |
| **Turning** | `turn left heading 180`, `heading 090`, `bank right 25`, `turn left`, `roll level` |
| **Configuration** | `flaps 1`, `flaps full`, `flaps up`, `gear down`, `gear up`, `speedbrakes out`, `speedbrakes in` |
| **Time** | `hold` (advance 10 s unchanged), `wait 60 seconds`, `wait 2 minutes` |
| **Other** | `status` (re-read the panel, no time passes), `help`, `quit` |

Each command advances the simulation **10 seconds** unless you say otherwise.
`status` and `help` cost no time.\
"""
