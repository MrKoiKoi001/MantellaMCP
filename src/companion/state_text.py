"""Writes the game state as a few short lines for the AI.

The AI asks for the state many times while playing, and every answer stays in its conversation.
In a voice chat that conversation is full after a while. So the answer is plain text with only what
the AI needs to decide, instead of the whole state as JSON.
"""
from typing import Any

DEGREES_PER_DIRECTION: float = 45.0
# Clockwise, starting straight ahead of whoever looks
RELATIVE_DIRECTIONS: list[str] = ["ahead", "ahead-right", "right", "behind-right", "behind", "behind-left", "left", "ahead-left"]
# Clockwise, starting in the north
COMPASS_DIRECTIONS: list[str] = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
# A long journal must not fill the conversation of the AI. Tracked quests come first, so they are the ones that stay
MAX_QUEST_LINES: int = 8
MAX_MISCELLANEOUS_OBJECTIVES: int = 6
OBJECTIVE_SEPARATOR: str = "; "
# Up to this age a line that was heard is given in seconds, after that in minutes
MAX_AGE_IN_SECONDS: int = 90
SECONDS_PER_MINUTE: int = 60


def relative_direction(angle_degrees: float) -> str:
    """Where something is, seen from someone. 0 = straight ahead, negative = to the left, positive = to the right"""
    return _direction(angle_degrees, RELATIVE_DIRECTIONS)


def compass_direction(angle_degrees: float) -> str:
    """Direction on the map. 0 = north, 90 = east"""
    return _direction(angle_degrees, COMPASS_DIRECTIONS)


def _direction(angle_degrees: float, names: list[str]) -> str:
    # Half a step is added so that 'ahead' reaches from -22.5 to 22.5 degrees
    step = int((angle_degrees % 360.0 + DEGREES_PER_DIRECTION / 2.0) // DEGREES_PER_DIRECTION)
    return names[step % len(names)]


def describe_state(state_result: dict[str, Any]) -> str:
    """Returns the result of CompanionCommandQueue.get_state as text for the AI"""
    lines: list[str] = []
    state = state_result.get("state")
    is_connected = bool(state_result.get("connected"))
    if not is_connected:
        lines.append("The game is not connected. Is Skyrim running with a loaded save?")
    if not isinstance(state, dict):
        if is_connected:
            lines.append("The game has not sent its state yet. Ask again in a moment.")
        return "\n".join(lines)

    if not bool(state_result.get("fresh")):
        age = state_result.get("state_age_seconds")
        if is_connected:
            lines.append(f"The game did not answer in time. What follows is {age} seconds old:")
        else:
            lines.append(f"Last known state, {age} seconds old:")

    lines.append(_describe_place(state))
    player_line = _describe_player(state.get("player"))
    if player_line != "":
        lines.append(player_line)
    nearby = state.get("nearby")
    if not isinstance(nearby, list):
        nearby = []
    lines.append(_describe_companion(state.get("companion"), nearby))
    if len(nearby) == 0:
        lines.append("Nearby: nobody")
    else:
        lines.append("Nearby:")
        for character in nearby:
            lines.append(_describe_nearby_character(character))
    lines.extend(_describe_quests(state.get("quests")))
    lines.extend(_describe_heard(state_result.get("heard"), state.get("companion")))
    return "\n".join(lines)


def _describe_quests(quests: Any) -> list[str]:
    """The open quests of the journal. No lines at all if the game could not tell them (its SKSE plugin is missing)"""
    if not isinstance(quests, list):
        return []
    named_quests = [quest for quest in quests if not quest.get("miscellaneous")]
    miscellaneous_objectives: list[str] = []
    for quest in quests:
        if quest.get("miscellaneous"):
            miscellaneous_objectives.extend(quest.get("objectives", []))
    if len(named_quests) == 0 and len(miscellaneous_objectives) == 0:
        return ["Quests: none"]

    lines: list[str] = ["Quests:"]
    for quest in named_quests[:MAX_QUEST_LINES]:
        title = str(quest.get("name"))
        if quest.get("tracked"):
            title += " (tracked)"
        lines.append(f"- {title}: {OBJECTIVE_SEPARATOR.join(quest.get('objectives', []))}")
    left_out = len(named_quests) - MAX_QUEST_LINES
    if left_out > 0:
        lines.append(f"- and {left_out} more")
    if len(miscellaneous_objectives) > 0:
        shown = miscellaneous_objectives[:MAX_MISCELLANEOUS_OBJECTIVES]
        left_out = len(miscellaneous_objectives) - len(shown)
        more = f"{OBJECTIVE_SEPARATOR}and {left_out} more" if left_out > 0 else ""
        lines.append(f"- Miscellaneous: {OBJECTIVE_SEPARATOR.join(shown)}{more}")
    return lines


def _describe_heard(heard: Any, companion: Any) -> list[str]:
    """What the characters in the game said since the AI asked the last time. No lines at all if that is nothing"""
    if not isinstance(heard, list) or len(heard) == 0:
        return []
    own_ref_id = companion.get("ref_id") if isinstance(companion, dict) else None
    lines: list[str] = ["Heard:"]
    for line in heard:
        speaker = "You" if own_ref_id is not None and line.get("ref_id") == own_ref_id else line.get("name")
        lines.append(f"- {_describe_age(line.get('seconds_ago', 0))}, {speaker}: {line.get('text')}")
    return lines


def _describe_age(seconds: int) -> str:
    if seconds <= MAX_AGE_IN_SECONDS:
        return f"{seconds} s ago"
    return f"{round(seconds / SECONDS_PER_MINUTE)} min ago"


def _describe_place(state: dict[str, Any]) -> str:
    place = str(state.get("location", "Unknown place"))
    if "indoors" in state:
        place += f" ({'indoors' if state['indoors'] else 'outdoors'})"
    if "time" in state:
        place += f", {state['time']}"
    # Indoors nobody sees the weather
    if "weather" in state and state.get("indoors") is False:
        place += f", {state['weather']}"
    return place


def _describe_player(player: Any) -> str:
    if not isinstance(player, dict):
        return ""
    parts: list[str] = []
    if "health_percent" in player:
        parts.append(f"health {player['health_percent']}%")
    if "in_combat" in player:
        parts.append("fighting" if player["in_combat"] else "not fighting")
    if "facing_degrees" in player:
        parts.append(f"faces {compass_direction(player['facing_degrees'])}")
    if "position" in player:
        parts.append("position " + ",".join(str(coordinate) for coordinate in player["position"]))
    name = str(player.get("name", "")).strip()
    title = "Player" if name == "" else f"Player {name}"
    if len(parts) == 0:
        return title
    return f"{title}: {', '.join(parts)}"


def _describe_companion(companion: Any, nearby: list[dict[str, Any]]) -> str:
    if not isinstance(companion, dict):
        return "You have no body in the game: the player has no follower."
    name = companion.get("name", "unknown")
    if companion.get("dead"):
        return f"You, {name}: dead"

    parts: list[str] = [f"health {companion.get('health_percent')}%"]
    if not companion.get("nearby"):
        parts.append("far away from the player")
    elif "distance_meters" in companion:
        parts.append(_describe_where(companion))
        if "facing_degrees" in companion:
            parts.append(f"faces {compass_direction(companion['facing_degrees'])}")
        if "player_angle_degrees" in companion:
            parts.append(f"player {relative_direction(companion['player_angle_degrees'])} of you")
    walk_target_ref_id = companion.get("walk_target_ref_id")
    if walk_target_ref_id is not None:
        parts.append(" ".join(text for text in ["walking to", _name_of(walk_target_ref_id, nearby), walk_target_ref_id] if text != ""))
    else:
        parts.append("waiting" if companion.get("waiting") else "following")
    if companion.get("retreating"):
        parts.append("retreating")
    target_ref_id = companion.get("target_ref_id")
    if target_ref_id is not None:
        # The name is only known if the target is in the list of characters nearby
        parts.append(" ".join(text for text in ["fighting", _name_of(target_ref_id, nearby), target_ref_id] if text != ""))
    elif companion.get("in_combat"):
        parts.append("fighting")
    return f"You, {name}: {', '.join(parts)}"


def _describe_nearby_character(character: dict[str, Any]) -> str:
    line = f"{character.get('ref_id')} {character.get('name')}, {_describe_where(character)}"
    if "distance_from_companion_meters" in character:
        line += f", from you {character['distance_from_companion_meters']} m"
        if "angle_from_companion_degrees" in character:
            line += f" {relative_direction(character['angle_from_companion_degrees'])}"
    if character.get("hostile"):
        line += ", hostile"
    return line


def _describe_where(character: dict[str, Any]) -> str:
    """Distance to the player and, if the game sent it, the direction seen from the player"""
    where = f"{character.get('distance_meters')} m"
    angle = character.get("angle_degrees")
    if isinstance(angle, (int, float)) and not isinstance(angle, bool):
        where += f" {relative_direction(angle)}"
    return where


def _name_of(ref_id: str, nearby: list[dict[str, Any]]) -> str:
    for character in nearby:
        if character.get("ref_id") == ref_id:
            return str(character.get("name", ""))
    return ""
