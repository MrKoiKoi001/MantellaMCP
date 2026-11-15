"""The gestures the companion can play with the command gesture: their names and the animations of the game behind them.

They come from the table Mantella itself uses for its emotes (data/Skyrim/skyrim_idles.csv): per line the form ID
of an animation (an "Idle" of the game) and the name it goes by. Some names have several animations.
"""
import csv
import random
from pathlib import Path

from src.companion.ref_ids import format_ref_id, parse_ref_id

IDLES_FILE: Path = Path(__file__).resolve().parents[2] / "data" / "Skyrim" / "skyrim_idles.csv"
COLUMN_ID: str = "id"
COLUMN_NAME: str = "idle_name"
# These animations are in Mantella.esp, which the companion mod does not need. Their IDs start with this instead of a number
OTHER_PLUGIN_PREFIX: str = "xx"


def load_gestures(file: Path = IDLES_FILE) -> dict[str, list[str]]:
    """Returns the animations of every gesture: name -> form IDs as eight hex digits. Empty if the table cannot be read"""
    gestures: dict[str, list[str]] = {}
    try:
        with open(file, newline="", encoding="utf-8") as opened:
            for row in csv.DictReader(opened):
                name = str(row.get(COLUMN_NAME) or "").strip().lower()
                id_text = str(row.get(COLUMN_ID) or "").strip()
                if name == "" or id_text.lower().startswith(OTHER_PLUGIN_PREFIX):
                    continue
                form_id = parse_ref_id(id_text)
                if form_id is None or form_id == 0:
                    continue
                gestures.setdefault(name, []).append(format_ref_id(form_id))
    except (OSError, csv.Error, UnicodeDecodeError):
        return {}
    return gestures


GESTURES: dict[str, list[str]] = load_gestures()


# The table of the companion itself, next to the one of Mantella: per line a gesture, the form ID of an animation,
# the event that starts that animation, its kind and the event that ends it (if the game has one).
# It gives the gestures of Mantella's table their events, and it adds gestures:
# - kind empty: an animation for standing, like the ones of Mantella's table
# - kind "sitting": the animation the gesture plays instead while the companion sits. A gesture that only has
#   such a line can only be played while sitting
# - kind "state": something that stays on until the gesture is ended, also while the companion walks (being drunk)
OWN_IDLES_FILE: Path = Path(__file__).resolve().parents[2] / "data" / "Skyrim" / "companion_idles.csv"
COLUMN_EVENT: str = "event"
COLUMN_KIND: str = "kind"
COLUMN_STOP_EVENT: str = "stop_event"
KIND_SITTING: str = "sitting"
KIND_STATE: str = "state"


def load_own_gestures(file: Path = OWN_IDLES_FILE) -> list[dict[str, str]]:
    """Returns the lines of the companion's own table: name, id, event, kind, stop_event. Empty if it cannot be read"""
    lines: list[dict[str, str]] = []
    try:
        with open(file, newline="", encoding="utf-8") as opened:
            for row in csv.DictReader(opened):
                name = str(row.get(COLUMN_NAME) or "").strip().lower()
                form_id = parse_ref_id(str(row.get(COLUMN_ID) or "").strip())
                if name == "" or form_id is None or form_id == 0:
                    continue
                lines.append({"name": name, "id": format_ref_id(form_id), "event": str(row.get(COLUMN_EVENT) or "").strip(),
                              "kind": str(row.get(COLUMN_KIND) or "").strip().lower(),
                              "stop_event": str(row.get(COLUMN_STOP_EVENT) or "").strip()})
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    return lines


OWN_GESTURES: list[dict[str, str]] = load_own_gestures()
# What the own table knows about an animation, by its form ID
ANIMATION_DETAILS: dict[str, dict[str, str]] = {line["id"]: line for line in OWN_GESTURES}
# The animation a gesture plays while the companion sits, by the name of the gesture
SITTING_GESTURES: dict[str, dict[str, str]] = {line["name"]: line for line in OWN_GESTURES if line["kind"] == KIND_SITTING}
for own_line in OWN_GESTURES:
    if own_line["kind"] != KIND_SITTING and own_line["id"] not in GESTURES.setdefault(own_line["name"], []):
        GESTURES[own_line["name"]].append(own_line["id"])
# Every gesture there is, also the ones that can only be played while sitting
GESTURE_NAMES: list[str] = sorted(set(GESTURES) | set(SITTING_GESTURES))


def pick_gesture(name: str) -> dict[str, str] | None:
    """Returns what the game mod needs to play this gesture, or None if there is no such gesture.

    'id' is the form ID of the animation for standing (empty if the gesture is only for sitting), 'event' the event
    that starts it, 'stop_event' the event that ends it, 'state' whether it stays on until it is ended.
    'sit_id', 'sit_event' and 'sit_stop_event' are the same for the animation it plays while the companion sits
    """
    key = name.strip().lower().replace(" ", "_")
    animations = GESTURES.get(key)
    sitting = SITTING_GESTURES.get(key)
    if animations is None and sitting is None:
        return None
    gesture: dict[str, str] = {"name": key, "id": "", "event": "", "stop_event": "", "state": "", "sit_id": "", "sit_event": "", "sit_stop_event": ""}
    if animations is not None and len(animations) > 0:
        gesture["id"] = random.choice(animations)
        details = ANIMATION_DETAILS.get(gesture["id"], {})
        gesture["event"] = details.get("event", "")
        gesture["stop_event"] = details.get("stop_event", "")
        gesture["state"] = "1" if details.get("kind") == KIND_STATE else ""
    if sitting is not None:
        gesture["sit_id"] = sitting["id"]
        gesture["sit_event"] = sitting["event"]
        gesture["sit_stop_event"] = sitting["stop_event"]
    return gesture


def pick_animation(name: str) -> str:
    """Returns the form ID of one of the animations of this gesture, or an empty text if there is no such gesture"""
    animations = GESTURES.get(name.strip().lower().replace(" ", "_"))
    if animations is None:
        return ""
    return random.choice(animations)
