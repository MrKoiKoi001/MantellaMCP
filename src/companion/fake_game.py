"""Pretends to be Skyrim, to try out the companion server and the AI connection without the game.

Polls the server like the Papyrus script does, prints every command it receives and reports it as done.

Run from the repo root (while companion_main.py is running):  python -m src.companion.fake_game
"""
import argparse
import json
import time
import urllib.error
import urllib.request

from src.companion import game_state
from src.companion.companion_constants import companion_constants as consts

ERROR_PAUSE_SECONDS: float = 1.0
SIGNED_NUMBER_LIMIT: int = 0x80000000
UNSIGNED_NUMBER_COUNT: int = 0x100000000


NEARBY: list[dict] = [
    {"name": "Bandit", "ref_id": "FF000A01", "distance_meters": 12, "angle_degrees": -40, "hostile": True},
    {"name": "Bandit", "ref_id": "FF000A02", "distance_meters": 20, "angle_degrees": 150, "hostile": True},
    {"name": "Whiterun Guard", "ref_id": "000F6845", "distance_meters": 8, "angle_degrees": 95, "hostile": False},
    {"name": "Bandit Outlaw", "ref_id": "FF000A03", "distance_meters": 5, "angle_degrees": 10, "hostile": False, "dead": True},
]


# The open quests as the SKSE plugin of the game mod writes them: tracked, type, name, then the open objectives
QUESTS: list[list[str]] = [
    ["1", "1", "Before the Storm", "Talk to the Jarl of Whiterun"],
    ["0", "8", "The Golden Claw", "Find the secret of Bleak Falls Barrow", "Retrieve the Golden Claw"],
    ["0", "6", "Favor for Alvor", "Buy a sword from Alvor"],
]
# Where the targets of the tracked quest are as the plugin writes them: place of the quest, place of the objective,
# where (0 = here, 1 = behind a door, 2 = elsewhere), distance and angle from the player, name of the target, name of the place
QUEST_TARGETS: list[list[str]] = [
    ["0", "0", "1", "140", "-40", "Jarl Balgruuf", "Dragonsreach"],
]
# The places around the player as the plugin writes them: kind (0 = map marker, 1 = door), distance and angle from the player,
# kind of map marker as the game numbers it, 1 if the player has been there, name
PLACES: list[list[str]] = [
    ["0", "120", "-60", "13", "1", "Pelagia Farm"],
    ["0", "260", "170", "21", "0", "Whiterun Stables"],
    ["1", "20", "90", "0", "0", "Warmaiden's"],
    ["1", "140", "-40", "0", "0", "Dragonsreach"],
]
NATIVE_PLUGIN_VERSION: int = 3
# What was said since the last state as the SKSE plugin writes it: seconds ago, ref ID of the speaker, name, text
HEARD: list[list[str]] = [
    ["12", "00000014", "Dragonborn", "What do you know about dragons?"],
    ["9", "000F6845", "Whiterun Guard", "Only what the old tales say."],
    ["3", "000A2C94", "Lydia", "I am sworn to carry your burdens."],
]


GOLD: list[int] = [250, 30]
HANDS: list[str] = ["R:Steel Sword", "L:Iron Shield"]
# Inventories as the game mod sends them: ID, how many, form type of the game, worn or held, name
OWN_ITEMS: list[tuple[str, int, int, int, str]] = [
    ("00013989", 1, 41, 1, "Steel Sword"), ("00012EB6", 1, 26, 1, "Iron Shield"), ("00013985", 1, 41, 0, "Hunting Bow"),
    ("0001397D", 24, 42, 0, "Iron Arrow"), ("0003EADD", 2, 46, 0, "Potion of Minor Healing"), ("0000000F", 30, 32, 0, "Gold"),
]
PLAYER_ITEMS: list[tuple[str, int, int, int, str]] = [
    ("00012E49", 1, 41, 1, "Iron Sword"), ("0003EADD", 5, 46, 0, "Potion of Minor Healing"), ("0000000A", 12, 32, 0, "Lockpick"),
]


# What the dead character in NEARBY carries
CORPSE_REF_ID: str = "FF000A03"
CORPSE_ITEMS: list[tuple[str, int, int, int, str]] = [
    ("0001359D", 1, 41, 1, "Iron War Axe"), ("00013911", 1, 26, 1, "Hide Armor"), ("0000000F", 23, 32, 0, "Gold"),
]


def build_inventory(items: list[tuple[str, int, int, int, str]]) -> tuple[list[int], list[str]]:
    values: list[int] = [len(items)]
    names: list[str] = []
    for place, (item_id, count, form_type, equipped, name) in enumerate(items):
        values.extend([to_papyrus_number(item_id), count, form_type, equipped])
        names.append(f"{place}{game_state.NAME_PLACE_SEPARATOR}{name}")
    return values, names


def to_papyrus_number(ref_id: str) -> int:
    """Papyrus only knows signed numbers, so the game mod sends ref IDs from 80000000 on as negative numbers"""
    if ref_id == "":
        return 0
    form_id = int(ref_id, 16)
    if form_id >= SIGNED_NUMBER_LIMIT:
        return form_id - UNSIGNED_NUMBER_COUNT
    return form_id


def build_state(is_waiting: bool = False, target_ref_id: str = "", is_fleeing: bool = False, walk_target_ref_id: str = "",
                has_heard_lines: bool = False, inventory: str = "", corpse_ref_id: str = "", report: str = "") -> dict:
    """Same short form as BuildState in KI_MCP_Gefaehrte_Loop.psc sends. What the numbers mean: game_state.py

    Args:
        has_heard_lines: The plugin of the game mod hands out every line only once, so only one state should carry them
        inventory: Whose inventory the server asked for (companion_constants.INVENTORY_...), or empty for none
        corpse_ref_id: For INVENTORY_CORPSE: whose body the server asked about
        report: What became of a loot or move_to since the last state, or empty
    """
    is_fighting = target_ref_id != ""
    nearby_values: list[int] = []
    nearby_names: list[str] = []
    for place, character in enumerate(NEARBY):
        nearby_values.extend([
            to_papyrus_number(character["ref_id"]), character["distance_meters"],
            character["angle_degrees"], int(character["hostile"]), int(character.get("dead", False)),
        ])
        nearby_names.append(f"{place}{game_state.NAME_PLACE_SEPARATOR}{character['name']}")
    state: dict = {
        game_state.KEY_LOCATION: "Whiterun, Plains District",
        game_state.KEY_PLAYER_NAME: "Dragonborn",
        game_state.KEY_PLAYER_VALUES: [85, int(is_fighting), 23500, -7900, -3300, 45, 0],
        # Snow at 14:35
        game_state.KEY_WORLD_VALUES: [3, 875, NATIVE_PLUGIN_VERSION, 1],
        game_state.KEY_QUESTS: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in QUESTS],
        game_state.KEY_QUEST_TARGETS: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in QUEST_TARGETS],
        game_state.KEY_PLACES: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in PLACES],
        game_state.KEY_COMPANION_NAME: "Lydia",
        game_state.KEY_COMPANION_VALUES: [
            to_papyrus_number("000A2C94"), 100, 0, int(is_fighting), int(is_waiting), 1, 3, -150,
            to_papyrus_number(target_ref_id), 180, int(is_fleeing), to_papyrus_number(walk_target_ref_id),
        ],
        game_state.KEY_NEARBY_VALUES: nearby_values,
        game_state.KEY_NEARBY_NAMES: nearby_names,
        game_state.KEY_GOLD: GOLD,
        game_state.KEY_HANDS: HANDS,
        game_state.KEY_TIMES: [0, 0],
    }
    wanted_inventories = [
        (game_state.KEY_INVENTORY_OWN, OWN_ITEMS, (consts.INVENTORY_OWN, consts.INVENTORY_BOTH)),
        (game_state.KEY_INVENTORY_PLAYER, PLAYER_ITEMS, (consts.INVENTORY_PLAYER, consts.INVENTORY_BOTH)),
    ]
    for key, items, choices in wanted_inventories:
        if inventory in choices:
            state[key], state[key + game_state.NAMES_SUFFIX] = build_inventory(items)
    if inventory == consts.INVENTORY_CORPSE and corpse_ref_id == CORPSE_REF_ID:
        state[game_state.KEY_CORPSE] = [to_papyrus_number(CORPSE_REF_ID)]
        state[game_state.KEY_CORPSE_NAME] = "Bandit Outlaw"
        state[game_state.KEY_INVENTORY_CORPSE], state[game_state.KEY_INVENTORY_CORPSE + game_state.NAMES_SUFFIX] = build_inventory(CORPSE_ITEMS)
    if report != "":
        state[game_state.KEY_REPORT] = report
    if has_heard_lines:
        state[game_state.KEY_HEARD] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in HEARD]
    return state


def run_command(action: str, target: str, target_ref_id: str) -> tuple[bool, str, str]:
    """Answers attack, move_to, loot, flee and stand_down like the game mod does

    Returns:
        Whether it worked, the text for the AI, and whom the companion fights afterwards (ref_id or empty)
    """
    if action == consts.ACTION_ATTACK:
        for character in NEARBY:
            if character["ref_id"] == target:
                return True, f"You attack {character['name']} now.", target
        return False, f"There is no character with the ref_id '{target}'. Take the ref_id from 'nearby' in STATE.", target_ref_id
    if action == consts.ACTION_MOVE_TO:
        for character in NEARBY:
            if character["ref_id"] == target:
                return True, f"You walk to {character['name']} and wait there. 'follow' brings you back to the player.", target_ref_id
        return False, f"There is no character with the ref_id '{target}'. Take the ref_id from 'nearby' in STATE.", target_ref_id
    if action == consts.ACTION_LOOT:
        for character in NEARBY:
            if character["ref_id"] == target:
                if not character.get("dead", False):
                    return False, f"{character['name']} is alive. You can only loot the dead.", target_ref_id
                return True, f"You walk to the body of {character['name']} to take everything it carries. Your next STATE tells you what you got.", target_ref_id
        return False, f"There is no character with the ref_id '{target}'. Take the ref_id from 'nearby' in STATE.", target_ref_id
    if action in consts.ITEM_ACTIONS:
        items = PLAYER_ITEMS if action == consts.ACTION_TAKE else OWN_ITEMS
        for item_id, count, form_type, equipped, name in items:
            if item_id == target:
                return True, f"Fake game did '{action}' with {name}.", target_ref_id
        return False, f"There is no item with the ID '{target}'. Take the ID from the inventory in STATE.", target_ref_id
    if action == consts.ACTION_FLEE:
        return True, "You retreat: you do not fight and you run from enemies. This holds until your next command.", ""
    if action == consts.ACTION_STAND_DOWN:
        if target_ref_id == "":
            return True, "You were not fighting.", ""
        return True, "You stop fighting. Whoever keeps attacking you or the player will pull you back into the fight.", ""
    return True, f"Fake game did '{action}'.", target_ref_id


def is_waiting_after(action: str, is_waiting: bool) -> bool:
    """Like the game: 'wait' and 'move_to' make the companion stay, 'follow' makes it come along again, everything else changes nothing"""
    if action in (consts.ACTION_WAIT, consts.ACTION_MOVE_TO):
        return True
    if action == consts.ACTION_FOLLOW:
        return False
    return is_waiting


def is_fleeing_after(action: str) -> bool:
    """Like the game: the retreat holds until the next command"""
    return action == consts.ACTION_FLEE


def walk_target_after(action: str, target: str) -> str:
    """Like the game: every new command ends the walk. The simulator's companion never arrives, so it keeps walking until then"""
    return target if action in (consts.ACTION_MOVE_TO, consts.ACTION_LOOT) else ""


def main():
    parser = argparse.ArgumentParser(description="Fake Skyrim for testing the companion server")
    parser.add_argument("--port", type=int, default=4998)
    args = parser.parse_args()
    url = f"http://localhost:{args.port}{consts.ROUTE_GAME}"

    results: list[dict] = []
    is_waiting = False
    target_ref_id = ""
    is_fleeing = False
    walk_target_ref_id = ""
    poll_id = 0
    # Like the game mod: the state goes along with the first request, and after that only when the server asked for it
    is_state_wanted = True
    wanted_inventory = ""
    wanted_corpse = ""
    print(f"Fake game polling {url} (Ctrl+C to stop)")
    while True:
        poll_id += 1
        request_body: dict = {consts.KEY_POLL_ID: poll_id, consts.KEY_RESULTS: results}
        if is_state_wanted:
            # Only the first state has something that was said
            request_body[consts.KEY_STATE] = build_state(is_waiting, target_ref_id, is_fleeing, walk_target_ref_id, poll_id == 1, wanted_inventory, wanted_corpse)
            wanted_inventory = ""
            wanted_corpse = ""
        body = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as error:
            print(f"Server not reachable ({error}), next try in {ERROR_PAUSE_SECONDS} s")
            time.sleep(ERROR_PAUSE_SECONDS)
            continue

        if reply.get(consts.KEY_POLL_ID) != poll_id:
            # The real game ignores such a reply and would only ask again when its watchdog hits
            print(f"WARNING  reply without the poll id of the request ({reply.get(consts.KEY_POLL_ID)} instead of {poll_id})")

        results = []
        is_state_wanted = False
        for command in reply.get(consts.KEY_COMMANDS, []):
            action = command.get(consts.KEY_COMMAND_ACTION)
            target = command.get(consts.KEY_COMMAND_TARGET)
            detail = command.get(consts.KEY_COMMAND_DETAIL)
            if action == consts.ACTION_REPORT_STATE:
                is_state_wanted = True
                wanted_inventory = str(detail or "")
                wanted_corpse = str(target or "")
                continue
            print(f"COMMAND  action={action}  target={target}  detail={detail}")
            is_ok, message, target_ref_id = run_command(str(action), str(target), target_ref_id)
            if is_ok:
                is_waiting = is_waiting_after(str(action), is_waiting)
                is_fleeing = is_fleeing_after(str(action))
                walk_target_ref_id = walk_target_after(str(action), str(target))
            results.append({
                consts.KEY_COMMAND_ID: command.get(consts.KEY_COMMAND_ID),
                consts.KEY_RESULT_OK: is_ok,
                consts.KEY_RESULT_MESSAGE: message,
            })


if __name__ == "__main__":
    main()
