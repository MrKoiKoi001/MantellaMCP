"""Writes the game state as a few short lines for the AI.

The AI asks for the state many times while playing, and every answer stays in its conversation.
In a voice chat that conversation is full after a while. So the answer is plain text with only what
the AI needs to decide, instead of the whole state as JSON.
"""
from typing import Any

DEGREES_PER_DIRECTION: float = 45.0
# Clockwise, starting straight ahead of the player
RELATIVE_DIRECTIONS: list[str] = ["ahead", "ahead-right", "right", "behind-right", "behind", "behind-left", "left", "ahead-left"]
# Clockwise, starting in the north
COMPASS_DIRECTIONS: list[str] = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def relative_direction(angle_degrees: float) -> str:
    """Where something is, seen from the player. 0 = straight ahead, negative = to the left, positive = to the right"""
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
    return "\n".join(lines)


def _describe_place(state: dict[str, Any]) -> str:
    location = str(state.get("location", "Unknown place"))
    if "indoors" not in state:
        return location
    return f"{location} ({'indoors' if state['indoors'] else 'outdoors'})"


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
    parts.append("waiting" if companion.get("waiting") else "following")
    target_ref_id = companion.get("target_ref_id")
    if target_ref_id is not None:
        # The name is only known if the target is in the list of characters nearby
        parts.append(" ".join(text for text in ["fighting", _name_of(target_ref_id, nearby), target_ref_id] if text != ""))
    elif companion.get("in_combat"):
        parts.append("fighting")
    return f"You, {name}: {', '.join(parts)}"


def _describe_nearby_character(character: dict[str, Any]) -> str:
    line = f"{character.get('ref_id')} {character.get('name')}, {_describe_where(character)}"
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
