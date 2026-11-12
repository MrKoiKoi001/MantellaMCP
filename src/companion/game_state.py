"""The game state: from the short form the game mod sends to the layout the AI side works with.

Every value the game mod hands to its HTTP plugin costs the game one frame, and so does every question
it asks the game. So the mod sends three texts and a few lists of plain numbers instead of one named entry
per value (BuildState in KI_MCP_Gefaehrte_Loop.psc of the game mod). This module gives the numbers their
names back. The order of the numbers in each list has to match that script.
"""
import math
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
# angle from where the player faces to the companion in degrees (negative = left), ref ID of its combat target (0 = none),
# direction the companion faces in degrees (0 = north, 90 = east), retreating because of the command flee (0 or 1),
# ref ID of the character it is walking to because of the command move_to (0 = none)
KEY_COMPANION_VALUES: str = "companion"
# weather outside (see WEATHER_NAMES, -1 = unknown), time of day in minutes since midnight
KEY_WORLD_VALUES: str = "world"
# Four numbers per character: ref ID, distance to the player in meters,
# angle from where the player faces to the character in degrees (negative = left), hostile to the player (0 or 1)
KEY_NEARBY_VALUES: str = "nearby"
# One text per character: its place in the list above, a colon and its name, eg "0:Bandit".
# The HTTP plugin of the game drops names it cannot send without a word, so the place cannot be counted
KEY_NEARBY_NAMES: str = "nearby_names"
# How long the game mod took in milliseconds: for the whole state, and for the characters nearby alone
KEY_TIMES: str = "times_ms"

PLAYER_VALUE_COUNT: int = 7
COMPANION_VALUE_COUNT: int = 12
WORLD_VALUE_COUNT: int = 2
VALUES_PER_NEARBY_CHARACTER: int = 4
TIME_VALUE_COUNT: int = 2
NAME_PLACE_SEPARATOR: str = ":"
UNKNOWN_NAME: str = "unknown"
# Papyrus only knows signed numbers, so ref IDs from 80000000 on arrive as negative numbers
REF_ID_MASK: int = 0xFFFFFFFF
# The four kinds of weather the game tells apart (Weather.GetClassification). The game calls the first one 'pleasant'
WEATHER_NAMES: dict[int, str] = {0: "clear", 1: "cloudy", 2: "rain", 3: "snow"}
MINUTES_PER_HOUR: int = 60
MINUTES_PER_DAY: int = 1440
# Closer than this, a direction from one character to another says nothing
MINIMUM_DISTANCE_FOR_DIRECTION: float = 0.5


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

    world_values = _numbers(raw.get(KEY_WORLD_VALUES))
    if world_values is not None and len(world_values) == WORLD_VALUE_COUNT:
        weather, minutes = world_values
        if weather in WEATHER_NAMES:
            state["weather"] = WEATHER_NAMES[weather]
        if 0 <= minutes < MINUTES_PER_DAY:
            state["time"] = f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"

    companion = _read_companion(raw)
    if companion is not None:
        state["companion"] = companion

    state["nearby"] = _read_nearby(raw)
    _add_view_of_companion(state)

    times = _numbers(raw.get(KEY_TIMES))
    if times is not None and len(times) == TIME_VALUE_COUNT:
        state["build_ms"], state["scan_ms"] = times
    return state


def _read_companion(raw: dict[str, Any]) -> dict[str, Any] | None:
    """No companion in the result means: the player has no follower at the moment"""
    values = _numbers(raw.get(KEY_COMPANION_VALUES))
    if values is None or len(values) != COMPANION_VALUE_COUNT:
        return None
    form_id, health, dead, in_combat, waiting, is_loaded, distance, angle, target_form_id, facing, retreating, walk_target_form_id = values

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
        companion["facing_degrees"] = facing % 360
    if target_form_id != 0:
        companion["target_ref_id"] = _ref_id(target_form_id)
    companion["retreating"] = retreating != 0
    if walk_target_form_id != 0:
        companion["walk_target_ref_id"] = _ref_id(walk_target_form_id)
    return companion


def _add_view_of_companion(state: dict[str, Any]) -> None:
    """Adds where the player and the characters nearby are, seen from the companion

    The game measures everything from the player. Asking it again from the companion would cost two more
    questions per character, so this is calculated here: from where the player faces, and from the distance
    and angle of the companion and of the other character. Heights are left out, and the game sends whole
    meters and whole degrees, so the result is a little less exact than what was measured from the player.
    """
    player = state.get("player")
    companion = state.get("companion")
    if not isinstance(player, dict) or not isinstance(companion, dict):
        return
    if "facing_degrees" not in player or "facing_degrees" not in companion or "distance_meters" not in companion:
        return
    player_facing: int = player["facing_degrees"]
    companion_facing: int = companion["facing_degrees"]
    companion_east, companion_north = _map_offset(companion["distance_meters"], player_facing + companion["angle_degrees"])

    # Seen from the companion, the player is in the opposite direction of where the companion is seen from the player
    companion["player_angle_degrees"] = _signed_angle(player_facing + companion["angle_degrees"] + 180 - companion_facing)
    for character in state["nearby"]:
        east, north = _map_offset(character["distance_meters"], player_facing + character["angle_degrees"])
        east -= companion_east
        north -= companion_north
        distance = math.hypot(east, north)
        character["distance_from_companion_meters"] = round(distance)
        if distance >= MINIMUM_DISTANCE_FOR_DIRECTION:
            character["angle_from_companion_degrees"] = _signed_angle(math.degrees(math.atan2(east, north)) - companion_facing)


def _map_offset(distance: float, map_direction_degrees: float) -> tuple[float, float]:
    """How far to the east and to the north something is that lies in this direction on the map (0 = north, 90 = east)"""
    radians = math.radians(map_direction_degrees)
    return distance * math.sin(radians), distance * math.cos(radians)


def _signed_angle(angle_degrees: float) -> int:
    """The angle in whole degrees from -180 to 179, so that negative means left"""
    return (round(angle_degrees) + 180) % 360 - 180


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
