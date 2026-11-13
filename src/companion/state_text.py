"""Writes the game state as a few short lines for the AI.

The AI asks for the state many times while playing, and every answer stays in its conversation.
In a voice chat that conversation is full after a while. So the answer is plain text with only what
the AI needs to decide, instead of the whole state as JSON.
"""
from typing import Any

from src.companion.companion_constants import companion_constants as consts
from src.companion.game_state import (GAME_PROTOCOL, VISIBLE_HALF_ANGLE_DEGREES, ITEM_GROUPS, KEY_INVENTORY_CORPSE, KEY_INVENTORY_OWN, KEY_INVENTORY_PLAYER,
                                      MAX_INVENTORY_ITEMS, OTHER_ITEM_GROUP, PLACE_DOOR, PLACE_MARKER,
                                      TARGET_BEHIND_DOOR, TARGET_ELSEWHERE, THING_CONTAINER, THING_FURNITURE, THING_ITEM,
                                      SPACE_SLOPE, SPACE_WALL)

DEGREES_PER_DIRECTION: float = 45.0
# Clockwise, starting straight ahead of whoever looks
RELATIVE_DIRECTIONS: list[str] = ["ahead", "ahead-right", "right", "behind-right", "behind", "behind-left", "left", "ahead-left"]
# Clockwise, starting in the north
COMPASS_DIRECTIONS: list[str] = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
# A long journal must not fill the conversation of the AI. Tracked quests come first, so they are the ones that stay
MAX_QUEST_LINES: int = 8
MAX_MISCELLANEOUS_OBJECTIVES: int = 6
OBJECTIVE_SEPARATOR: str = "; "
# The lists 'Nearby' and 'Things' have this many lines at most each, unless the AI asked for another number
DEFAULT_MAX_LINES: int = 10
# A long list of places must not fill the conversation of the AI either. The game sends the nearest ones first
MAX_PLACE_MARKERS: int = 6
MAX_PLACE_DOORS: int = 6
# Up to this age a line that was heard is given in seconds, after that in minutes
MAX_AGE_IN_SECONDS: int = 90
SECONDS_PER_MINUTE: int = 60
# The ref_id every game gives the player
PLAYER_REF_ID: str = "00000014"


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

    mod_protocol = state.get("protocol")
    if mod_protocol is not None and mod_protocol != GAME_PROTOCOL:
        lines.append(f"Warning: the game mod and this server do not fit together (the mod speaks protocol {mod_protocol}, "
                     f"the server protocol {GAME_PROTOCOL}). Parts of what follows can be missing or wrong. "
                     "Tell the player to build and install the game mod again (build.py --install) and to restart the server.")
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
    things = state.get("things")
    if not isinstance(things, list):
        things = []
    places = state.get("places")
    if not isinstance(places, list):
        places = []
    # Whether the game measured the lists below from the companion instead of from the player
    is_from_companion = bool(state.get("around_companion"))
    lines.append(_describe_companion(state.get("companion"), nearby + things + places))
    gear_line = _describe_gear(state.get("player"), state.get("companion"))
    if gear_line != "":
        lines.append(gear_line)
    reports = state_result.get("reports")
    if isinstance(reports, list) and len(reports) > 0:
        lines.append("Since your last STATE: " + " ".join(str(report) for report in reports))
    max_lines = state_result.get("max_lines")
    if not isinstance(max_lines, int) or isinstance(max_lines, bool) or max_lines < 1:
        max_lines = DEFAULT_MAX_LINES
    if state_result.get("stats_wanted"):
        lines.extend(_describe_stats(state.get("player_stats"), state.get("companion_stats")))
    elif state_result.get("stats_missed"):
        lines.append("The game did not send the stats in time. Ask again.")
    companion = state.get("companion")
    if is_from_companion and isinstance(companion, dict) and not companion.get("nearby"):
        lines.append("The lists below show the place you are in. The player is not there, so the game has not loaded it: "
                     "you only get what it keeps of the place in memory. That is the characters who live there and the doors, "
                     "hardly any furniture or loose things, and no bandits or animals. Do not take an empty list for an empty place.")
    elif is_from_companion:
        lines.append("The lists below show what is around you, not around the player.")
    elif state_result.get("around_wanted"):
        lines.append("The game listed what is around the player: your character is not close enough to look around on its own.")
    # 'visible': the game has left out the characters the viewer cannot see. The things are left out here, by their direction
    is_visible_only = bool(state_result.get("visible_wanted")) and bool(state.get("visible_only"))
    if is_visible_only:
        viewer = "you" if is_from_companion else "the player"
        lines.append(f"Only what {viewer} can see: characters in front of {viewer} with no wall in between, "
                     f"things within {VISIBLE_HALF_ANGLE_DEGREES} degrees left and right (for things walls are not checked).")
        things = [thing for thing in things if abs(thing.get("angle_degrees", 0)) <= VISIBLE_HALF_ANGLE_DEGREES]
    elif state_result.get("visible_wanted"):
        lines.append("The game could not check what can be seen (inside a place it has not loaded, or an older game mod). "
                     "The lists below hold everything around.")
    # Traps come before everything else around, so that a warning does not get lost in a long list
    traps = state.get("traps")
    if isinstance(traps, list):
        if is_visible_only:
            traps = [trap for trap in traps if abs(trap.get("angle_degrees", 0)) <= VISIBLE_HALF_ANGLE_DEGREES]
        if len(traps) > 0:
            lines.append("Traps:")
            for trap in traps:
                lines.append(f"{trap.get('ref_id')} {trap.get('name')}, trap, {_describe_position(trap, is_from_companion)}")
    find_wanted = state_result.get("find_wanted")
    if isinstance(find_wanted, str) and find_wanted != "":
        lines.extend(_describe_found(find_wanted, state.get("found_for"), state.get("found"), is_from_companion))
    elif state_result.get("find_missed"):
        lines.append("The game did not send what it found in time. Ask again.")
    if state_result.get("space_wanted"):
        lines.append(_describe_space(state.get("space")))
    elif state_result.get("space_missed"):
        lines.append("The game did not send the space around you in time. Ask again.")
    if len(nearby) == 0:
        lines.append("Nearby: nobody")
    else:
        lines.append("Nearby:")
        for character in nearby[:max_lines]:
            lines.append(_describe_nearby_character(character, is_from_companion))
    # Only when the AI asked for them: furniture and containers before loose items, each nearest first
    if state_result.get("things_wanted"):
        shown_things = sorted(things, key=lambda thing: (thing.get("kind") == THING_ITEM, thing.get("distance_meters", 0)))
        if len(shown_things) == 0:
            lines.append("Things: nothing within about 10 meters" + (" of you" if is_from_companion else ""))
        else:
            lines.append("Things:")
            for thing in shown_things[:max_lines]:
                lines.append(_describe_thing(thing, is_from_companion))
    elif state_result.get("things_missed"):
        lines.append("The game did not send the things in time. Ask again.")
    lines.extend(_describe_places(state.get("places"), is_from_companion))
    lines.extend(_describe_quests(state.get("quests"), is_from_companion))
    lines.extend(_describe_heard(state_result.get("heard"), state.get("companion")))
    wanted = state_result.get("inventory_wanted")
    if wanted in (consts.INVENTORY_OWN, consts.INVENTORY_BOTH):
        lines.extend(_describe_inventory("Your inventory", state.get(KEY_INVENTORY_OWN)))
    if wanted in (consts.INVENTORY_PLAYER, consts.INVENTORY_BOTH):
        lines.extend(_describe_inventory("The player's inventory", state.get(KEY_INVENTORY_PLAYER)))
    corpse_wanted = state_result.get("corpse_wanted")
    if isinstance(corpse_wanted, str) and corpse_wanted != "":
        lines.extend(_describe_corpse(corpse_wanted, state.get("corpse"), state.get(KEY_INVENTORY_CORPSE)))
    if state_result.get("inventory_missed"):
        lines.append("The game did not send the inventory in time. Ask again.")
    return "\n".join(lines)


def _describe_quests(quests: Any, is_from_companion: bool = False) -> list[str]:
    """The open quests of the journal. No lines at all if the game could not tell them (its SKSE plugin is missing)

    Args:
        is_from_companion: Whether the game measured where the targets are from the companion instead of from the player
    """
    if not isinstance(quests, list):
        return []
    named_quests = [quest for quest in quests if not quest.get("miscellaneous")]
    miscellaneous_objectives: list[str] = []
    for quest in quests:
        if quest.get("miscellaneous"):
            miscellaneous_objectives.extend(_objective_texts(quest, is_from_companion))
    if len(named_quests) == 0 and len(miscellaneous_objectives) == 0:
        return ["Quests: none"]

    lines: list[str] = ["Quests:"]
    for quest in named_quests[:MAX_QUEST_LINES]:
        title = str(quest.get("name"))
        if quest.get("tracked"):
            title += " (tracked)"
        lines.append(f"- {title}: {OBJECTIVE_SEPARATOR.join(_objective_texts(quest, is_from_companion))}")
    left_out = len(named_quests) - MAX_QUEST_LINES
    if left_out > 0:
        lines.append(f"- and {left_out} more")
    if len(miscellaneous_objectives) > 0:
        shown = miscellaneous_objectives[:MAX_MISCELLANEOUS_OBJECTIVES]
        left_out = len(miscellaneous_objectives) - len(shown)
        more = f"{OBJECTIVE_SEPARATOR}and {left_out} more" if left_out > 0 else ""
        lines.append(f"- Miscellaneous: {OBJECTIVE_SEPARATOR.join(shown)}{more}")
    return lines


def _objective_texts(quest: dict[str, Any], is_from_companion: bool = False) -> list[str]:
    """The texts of the open objectives of a quest. An objective whose target the game knows gets it in brackets behind it"""
    texts: list[str] = [str(objective) for objective in quest.get("objectives", [])]
    for target in quest.get("targets", []):
        place = target.get("objective")
        if isinstance(place, int) and 0 <= place < len(texts):
            texts[place] += f" [{_describe_target(target, is_from_companion)}]"
    return texts


def _describe_target(target: dict[str, Any], is_from_companion: bool = False) -> str:
    """Where the target of an objective is: what the compass of the game points to.
    With the ref_id to walk to if the game sent it: of the target itself, or of the door to it"""
    name = str(target.get("name", "")).strip()
    title = "target" if name == "" else f"target {name}"
    place = str(target.get("place", "")).strip()
    if target.get("kind") == TARGET_ELSEWHERE:
        return f"{title}: not in this area" + ("" if place == "" else f", in {place}")
    where = _describe_position(target, is_from_companion)
    ref_id = str(target.get("ref_id", ""))
    if target.get("kind") == TARGET_BEHIND_DOOR:
        door = "a door" if place == "" else f"the door to {place}"
        if ref_id != "":
            door += f" {ref_id}"
        return f"{title}: behind {door}, {where}"
    if ref_id != "":
        title += f" {ref_id}"
    return f"{title}: {where}"


def _describe_places(places: Any, is_from_companion: bool = False) -> list[str]:
    """Map markers and doors around the player, seen from the player (or both from the companion, if the game measured
    from there). After each name its ref_id for the command move_to, if the game sent it.
    No lines at all if the game sent no places"""
    if not isinstance(places, list):
        return []
    lines: list[str] = []
    for title, kind, limit in (("Places", PLACE_MARKER, MAX_PLACE_MARKERS), ("Doors", PLACE_DOOR, MAX_PLACE_DOORS)):
        texts: list[str] = []
        for place in places:
            if place.get("kind") != kind or len(texts) >= limit:
                continue
            text = str(place.get("name"))
            if "ref_id" in place:
                text += f" {place['ref_id']}"
            if "type" in place:
                text += f" ({place['type']})"
            texts.append(f"{text} {_describe_where(place)}")
        if len(texts) > 0:
            lines.append(f"{title}{' (from you)' if is_from_companion else ''}: {', '.join(texts)}")
    return lines


def _describe_gear(player: Any, companion: Any) -> str:
    """What the companion holds and how much gold both have, as far as the game sent it"""
    parts: list[str] = []
    if isinstance(companion, dict):
        if "holds" in companion:
            parts.append("you hold " + (" and ".join(companion["holds"]) if len(companion["holds"]) > 0 else "nothing"))
        if "gold" in companion:
            parts.append(f"your gold {companion['gold']}")
    if isinstance(player, dict) and "gold" in player:
        parts.append(f"the player's gold {player['gold']}")
    if len(parts) == 0:
        return ""
    return "Gear: " + ", ".join(parts)


def _describe_inventory(title: str, inventory: Any) -> list[str]:
    """One line per kind of item (weapons, potions ...). After each item its ID for the commands equip, unequip, give and take"""
    if not isinstance(inventory, dict):
        return [f"{title}: the game did not send it."]
    items = inventory.get("items", [])
    if len(items) == 0:
        return [f"{title}: empty"]
    lines: list[str] = [f"{title}:"]
    for group in list(ITEM_GROUPS.values()) + [OTHER_ITEM_GROUP]:
        texts: list[str] = []
        for item in items:
            if item.get("group") != group:
                continue
            text = f"{item.get('name')} {item.get('id')}"
            if item.get("count", 1) != 1:
                text += f" x{item['count']}"
            if item.get("equipped"):
                text += " (equipped)"
            texts.append(text)
        if len(texts) > 0:
            lines.append(f"- {group}: {', '.join(texts)}")
    total = inventory.get("total", 0)
    if total > MAX_INVENTORY_ITEMS:
        lines.append(f"- The list is cut off: only the first {MAX_INVENTORY_ITEMS} of {total} kinds of items were checked.")
    return lines


def _describe_corpse(wanted_ref_id: str, corpse: Any, inventory: Any) -> list[str]:
    """What the dead character carries or the container holds that the AI asked about. After each item its ID for the command loot"""
    if not isinstance(corpse, dict) or corpse.get("ref_id") != wanted_ref_id:
        return [f"There is no dead character and no container with the ref_id {wanted_ref_id} around. "
                "Take the ref_id from the dead or the containers in 'Nearby'."]
    name = f"{corpse.get('name')} {wanted_ref_id}"
    if corpse.get("locked"):
        return [f"{name} is locked."]
    if corpse.get("container"):
        lines = _describe_inventory(f"{name} holds", inventory)
        # The game may not have filled a container nobody has opened yet
        return lines if len(lines) > 1 else [f"{name}: the game lists nothing in it."]
    lines = _describe_inventory(f"The body of {name} carries", inventory)
    return lines if len(lines) > 1 else [f"The body of {name} carries nothing."]


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
    if player.get("weapon_drawn"):
        parts.append("weapon drawn")
    if player.get("sleeping"):
        parts.append("lying asleep")
    elif player.get("sitting"):
        parts.append("sitting")
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
        place = str(companion.get("place", "")).strip()
        parts.append("far away from the player" if place == "" else f"in {place}, not where the player is")
    elif "distance_meters" in companion:
        parts.append(_describe_where(companion))
        if "facing_degrees" in companion:
            parts.append(f"faces {compass_direction(companion['facing_degrees'])}")
        if "player_angle_degrees" in companion:
            parts.append(f"player {relative_direction(companion['player_angle_degrees'])} of you")
    walk_target_ref_id = companion.get("walk_target_ref_id")
    if walk_target_ref_id is not None:
        parts.append(" ".join(text for text in ["walking to", _name_of(walk_target_ref_id, nearby), walk_target_ref_id] if text != ""))
    elif companion.get("sitting"):
        parts.append("sitting")
    elif companion.get("leader_ref_id") is not None:
        leader_ref_id = companion["leader_ref_id"]
        parts.append(" ".join(text for text in ["following", _name_of(leader_ref_id, nearby), leader_ref_id] if text != ""))
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
    if companion.get("weapon_drawn"):
        parts.append("weapon drawn")
    elif companion.get("weapon_kept_away"):
        parts.append("weapon kept away (sheathe)")
    if "expression" in companion:
        parts.append(f"face {companion['expression']}")
    look_target_ref_id = companion.get("look_target_ref_id")
    if look_target_ref_id == PLAYER_REF_ID:
        parts.append("looking at the player")
    elif look_target_ref_id is not None:
        parts.append(" ".join(text for text in ["looking at", _name_of(look_target_ref_id, nearby), look_target_ref_id] if text != ""))
    return f"You, {name}: {', '.join(parts)}"


def _describe_stats(player_stats: Any, companion_stats: Any) -> list[str]:
    """Level, skills and so on of the player and the level of the companion, as far as the game sent them"""
    if not isinstance(player_stats, dict):
        return ["The game did not send the stats. That needs a newer game mod."]
    parts: list[str] = [f"level {player_stats.get('level')}"]
    if player_stats.get("level_up_waiting"):
        parts.append("a level-up is waiting in the skills menu")
    perk_points = player_stats.get("perk_points", 0)
    if perk_points > 0:
        parts.append(f"{perk_points} perk point{'s' if perk_points != 1 else ''} to spend")
    parts.append(f"health {player_stats.get('health')}")
    parts.append(f"magicka {player_stats.get('magicka')} ({player_stats.get('magicka_percent')}%)")
    parts.append(f"stamina {player_stats.get('stamina')} ({player_stats.get('stamina_percent')}%)")
    parts.append(f"carries {player_stats.get('carried')} of {player_stats.get('carry_capacity')}")
    lines: list[str] = ["Player stats: " + ", ".join(parts)]
    skills = sorted(player_stats.get("skills", []), key=lambda skill: -skill.get("level", 0))
    lines.append("Player skills: " + ", ".join(f"{skill.get('name')} {skill.get('level')}" for skill in skills))
    if isinstance(companion_stats, dict):
        lines.append(f"Your stats: level {companion_stats.get('level')}, health {companion_stats.get('health')}, "
                     f"magicka {companion_stats.get('magicka')} ({companion_stats.get('magicka_percent')}%), "
                     f"stamina {companion_stats.get('stamina')} ({companion_stats.get('stamina_percent')}%)")
    return lines


def _describe_found(wanted: str, found_for: Any, found: Any, is_from_companion: bool) -> list[str]:
    """What the game found for the name the AI asked for, one line per match"""
    if not isinstance(found_for, str) or found_for.lower() != wanted.lower():
        return [f"The game could not look for '{wanted}': that needs the newest plugin of the game mod (KI_MCP_Gefaehrte.dll, interface version 10)."]
    if not isinstance(found, list) or len(found) == 0:
        return [f"Found for '{wanted}': nothing with that name in the loaded area. Names are as the game shows them. "
                "Who or what is inside a building is only found from inside: look for the door to that building instead."]
    lines = [f"Found for '{wanted}':"]
    for match in found:
        label = str(match.get("kind"))
        if match.get("dead"):
            label += ", dead"
        lines.append(f"{match.get('ref_id')} {match.get('name')}, {label}, {_describe_position(match, is_from_companion)}")
    return lines


def _describe_space(space: Any) -> str:
    """What rays found around the companion, as one line: walls, rising ground and drops with their direction and
    distance, and the directions that are open"""
    if not isinstance(space, list) or len(space) == 0:
        return ("Space around you: the game could not measure it (your character is not in the loaded area, "
                "or the plugin of the game mod is older than interface version 9).")
    walls: list[str] = []
    slopes: list[str] = []
    drops: list[str] = []
    open_directions: list[str] = []
    for direction in space:
        name = relative_direction(direction.get("angle_degrees", 0))
        obstacle = direction.get("obstacle")
        if obstacle == SPACE_WALL:
            walls.append(f"{name} {direction.get('obstacle_meters')} m")
        elif obstacle == SPACE_SLOPE:
            slopes.append(f"{name} {direction.get('obstacle_meters')} m")
        if "drop_meters" in direction:
            drops.append(f"{name} {direction['drop_meters']} m")
        elif obstacle not in (SPACE_WALL, SPACE_SLOPE):
            open_directions.append(name)
    parts: list[str] = []
    if len(walls) > 0:
        parts.append("wall " + ", ".join(walls))
    if len(slopes) > 0:
        parts.append("ground rises " + ", ".join(slopes))
    if len(drops) > 0:
        parts.append("DROP " + ", ".join(drops))
    if len(open_directions) > 0:
        parts.append("open " + ", ".join(open_directions))
    return "Space around you (rays at chest height, about 15 m far): " + "; ".join(parts)


def _describe_nearby_character(character: dict[str, Any], is_from_companion: bool = False) -> str:
    line = f"{character.get('ref_id')} {character.get('name')}, "
    if character.get("dead"):
        line += "dead, "
    line += _describe_position(character, is_from_companion)
    if character.get("hostile"):
        line += ", hostile"
    return line


def _describe_thing(thing: dict[str, Any], is_from_companion: bool = False) -> str:
    """A piece of furniture, a container or an item that lies around, as a line of the list 'Things'"""
    kind = thing.get("kind")
    if kind == THING_FURNITURE:
        label = "seat" if thing.get("seat") else "furniture"
    elif kind == THING_CONTAINER:
        label = "container, locked" if thing.get("locked") else "container"
    else:
        label = "item"
    return f"{thing.get('ref_id')} {thing.get('name')}, {label}, {_describe_position(thing, is_from_companion)}"


def _describe_position(character: dict[str, Any], is_from_companion: bool) -> str:
    """Where a character or a thing is: seen from the player and, as far as known, from the companion as well.
    If the game measured from the companion (is_from_companion), there is only that"""
    if is_from_companion:
        return "from you " + _describe_where(character)
    return _describe_where(character) + _describe_from_companion(character)


def _describe_from_companion(character: dict[str, Any]) -> str:
    """The same seen from the companion, as an addition to _describe_where. Empty if that is not known"""
    if "distance_from_companion_meters" not in character:
        return ""
    text = f", from you {character['distance_from_companion_meters']} m"
    if "angle_from_companion_degrees" in character:
        text += f" {relative_direction(character['angle_from_companion_degrees'])}"
    return text


def _describe_where(character: dict[str, Any]) -> str:
    """Distance to whoever the game measured from (the player, unless the AI asked for the view of the companion)
    and, if the game sent it, the direction seen from there"""
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
