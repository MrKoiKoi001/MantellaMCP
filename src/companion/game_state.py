"""The game state: from the short form the game mod sends to the layout the AI side works with.

Every value the game mod hands to its HTTP plugin costs the game one frame, and so does every question
it asks the game. So the mod sends three texts and a few lists of plain numbers instead of one named entry
per value (BuildState in KI_MCP_Gefaehrte_Loop.psc of the game mod). This module gives the numbers their
names back. The order of the numbers in each list has to match that script.
"""
from typing import Any

from src.companion.ref_ids import format_ref_id

# Keys of the state as the game mod sends it
KEY_LOCATION: str = "location"
KEY_PLAYER_NAME: str = "player_name"
# health in percent, in combat (0 or 1), position x, y and z in game units,
# direction the player faces in degrees (0 = north, 90 = east), indoors (0 or 1)
KEY_PLAYER_VALUES: str = "player"
KEY_COMPANION_NAME: str = "companion_name"
# ref ID, health in percent, dead, in combat, waiting, in the loaded area (each 0 or 1), distance to the player in meters,
# angle from where the player faces to the companion in degrees (negative = left), ref ID of its combat target (0 = none)
KEY_COMPANION_VALUES: str = "companion"
# Four numbers per character: ref ID, distance to the player in meters,
# angle from where the player faces to the character in degrees (negative = left), hostile to the player (0 or 1)
KEY_NEARBY_VALUES: str = "nearby"
# One text per character: its place in the list above, a colon and its name, eg "0:Bandit".
# The HTTP plugin of the game drops names it cannot send without a word, so the place cannot be counted
KEY_NEARBY_NAMES: str = "nearby_names"
# How long the game mod took in milliseconds: for the whole state, and for the characters nearby alone
KEY_TIMES: str = "times_ms"

PLAYER_VALUE_COUNT: int = 7
COMPANION_VALUE_COUNT: int = 9
VALUES_PER_NEARBY_CHARACTER: int = 4
TIME_VALUE_COUNT: int = 2
NAME_PLACE_SEPARATOR: str = ":"
UNKNOWN_NAME: str = "unknown"
# Papyrus only knows signed numbers, so ref IDs from 80000000 on arrive as negative numbers
REF_ID_MASK: int = 0xFFFFFFFF


def read_game_state(raw: Any) -> dict[str, Any] | None:
    """Returns the state the way the MCP side works with it, or None if the game sent no state

    Parts the game left out (or that do not have the expected shape) are left out here as well,
    except for 'nearby', which is always a list, nearest character first.
    """
    if not isinstance(raw, dict):
        return None

    state: dict[str, Any] = {}
    location = raw.get(KEY_LOCATION)
    if isinstance(location, str):
        state["location"] = location

    player: dict[str, Any] = {}
    player_name = raw.get(KEY_PLAYER_NAME)
    if isinstance(player_name, str):
        player["name"] = player_name
    player_values = _numbers(raw.get(KEY_PLAYER_VALUES))
    if player_values is not None and len(player_values) == PLAYER_VALUE_COUNT:
        health, in_combat, position_x, position_y, position_z, facing, indoors = player_values
        player["health_percent"] = health
        player["in_combat"] = in_combat != 0
        player["position"] = [position_x, position_y, position_z]
        player["facing_degrees"] = facing % 360
        state["indoors"] = indoors != 0
    if len(player) > 0:
        state["player"] = player

    companion = _read_companion(raw)
    if companion is not None:
        state["companion"] = companion

    state["nearby"] = _read_nearby(raw)

    times = _numbers(raw.get(KEY_TIMES))
    if times is not None and len(times) == TIME_VALUE_COUNT:
        state["build_ms"], state["scan_ms"] = times
    return state


def _read_companion(raw: dict[str, Any]) -> dict[str, Any] | None:
    """No companion in the result means: the player has no follower at the moment"""
    values = _numbers(raw.get(KEY_COMPANION_VALUES))
    if values is None or len(values) != COMPANION_VALUE_COUNT:
        return None
    form_id, health, dead, in_combat, waiting, is_loaded, distance, angle, target_form_id = values

    companion: dict[str, Any] = {}
    name = raw.get(KEY_COMPANION_NAME)
    companion["name"] = _tidy_name(name) if isinstance(name, str) else UNKNOWN_NAME
    companion["ref_id"] = _ref_id(form_id)
    companion["health_percent"] = health
    companion["dead"] = dead != 0
    companion["in_combat"] = in_combat != 0
    companion["waiting"] = waiting != 0
    # Far away (eg left behind in another place) the game has no position worth measuring
    companion["nearby"] = is_loaded != 0
    if is_loaded != 0:
        companion["distance_meters"] = distance
        companion["angle_degrees"] = angle
    if target_form_id != 0:
        companion["target_ref_id"] = _ref_id(target_form_id)
    return companion


def _read_nearby(raw: dict[str, Any]) -> list[dict[str, Any]]:
    values = _numbers(raw.get(KEY_NEARBY_VALUES))
    if values is None or len(values) % VALUES_PER_NEARBY_CHARACTER != 0:
        return []
    names = _names_by_place(raw.get(KEY_NEARBY_NAMES))

    nearby: list[dict[str, Any]] = []
    for place in range(len(values) // VALUES_PER_NEARBY_CHARACTER):
        start = place * VALUES_PER_NEARBY_CHARACTER
        form_id, distance, angle, hostile = values[start:start + VALUES_PER_NEARBY_CHARACTER]
        nearby.append({
            "name": names.get(place, UNKNOWN_NAME),
            "ref_id": _ref_id(form_id),
            "distance_meters": distance,
            "angle_degrees": angle,
            "hostile": hostile != 0,
        })
    nearby.sort(key=lambda character: character["distance_meters"])
    return nearby


def _names_by_place(raw_names: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    if not isinstance(raw_names, list):
        return names
    for entry in raw_names:
        if not isinstance(entry, str):
            continue
        place_text, separator, name = entry.partition(NAME_PLACE_SEPARATOR)
        if separator == "" or not place_text.isdecimal():
            continue
        names[int(place_text)] = _tidy_name(name)
    return names


def _tidy_name(name: str) -> str:
    """Returns the name with a capital first letter if it arrived all in lower case (eg 'bandit')

    The game keeps every text only once and does not tell upper and lower case apart. If some script
    used the word 'bandit' before the game loaded the name 'Bandit', every script gets 'bandit' from then on.
    """
    cleaned = name.strip()
    if cleaned == "":
        return UNKNOWN_NAME
    if cleaned == cleaned.lower() and cleaned != cleaned.upper():
        return cleaned[0].upper() + cleaned[1:]
    return cleaned


def _ref_id(form_id: int) -> str:
    return format_ref_id(form_id & REF_ID_MASK)


def _numbers(value: Any) -> list[int] | None:
    """Returns the value if it is a list of whole numbers, otherwise None"""
    if not isinstance(value, list):
        return None
    for number in value:
        if not isinstance(number, int) or isinstance(number, bool):
            return None
    return value
