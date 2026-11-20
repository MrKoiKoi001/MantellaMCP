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
# where (0 = here, 1 = behind a door, 2 = elsewhere), distance and angle from the player, name of the target, name of the place,
# ref ID to walk to (the target, or the door to it)
QUEST_TARGETS: list[list[str]] = [
    ["0", "0", "1", "140", "-40", "Jarl Balgruuf", "Dragonsreach", "0001A6F4"],
]
# The places around the player as the plugin writes them: kind (0 = map marker, 1 = door), ref ID, distance and angle
# from the player, kind of map marker as the game numbers it, 1 if the player has been there, name
PLACES: list[list[str]] = [
    ["0", "000C0001", "120", "-60", "13", "1", "Pelagia Farm"],
    ["0", "000C0002", "260", "170", "21", "0", "Whiterun Stables"],
    ["1", "0001A6F0", "20", "90", "0", "0", "Warmaiden's"],
    ["1", "0001A6F4", "140", "-40", "0", "0", "Dragonsreach"],
]
DOOR_REF_ID: str = "0001A6F4"
PLAYER_REF_ID: str = "00000014"
# The gestures the simulator knows the animations of (form ID of the animation -> what the game calls it)
GESTURE_ANIMATIONS: dict[str, str] = {"0003EA32": "IdleWave", "000D8734": "IdleSilentBow"}
# The things close to the player as the plugin writes them: kind (0 = furniture, 1 = container, 2 = item), ref ID,
# distance and angle from the player, 1 for a seat or a locked container, name
THINGS: list[list[str]] = [
    ["0", "0005A3F1", "3", "-90", "1", "Wooden Bench"],
    ["0", "0005A3F2", "6", "120", "0", "Grindstone"],
    ["1", "0005A400", "4", "30", "0", "Chest"],
    ["1", "0005A401", "9", "-150", "1", "Strongbox"],
    ["2", "0005A410", "2", "60", "0", "Iron Dagger"],
]
SEAT_REF_ID: str = "0005A3F1"
# A bed the companion can sleep in. It is not among THINGS, so that the lists of the tests stay as they are
BED_REF_ID: str = "0005A3F5"
ITEM_REF_ID: str = "0005A410"
# The state in one go (see pack_quick_state): how many numbers come before the first block, and the texts that are
# no lists
QUICK_HEAD_COUNT: int = 5
QUICK_SINGLE_TEXTS: tuple[str, ...] = (game_state.KEY_LOCATION, game_state.KEY_PLAYER_NAME, game_state.KEY_COMPANION_NAME, game_state.KEY_COMPANION_VOICE,
                                       game_state.KEY_COMPANION_PLACE, game_state.KEY_PLAYER_HORSE_NAME, game_state.KEY_HORSE_NAME)
# Of which kind the loose things are, for pick_up with 'all' and a kind (see consts.PICK_UP_KINDS)
LOOSE_KINDS: dict[str, str] = {ITEM_REF_ID: "weapons"}
# The traps close to the player as the plugin writes them: kind (always 3), ref ID, distance and angle from the player,
# 0, and the folder the game keeps the model of the trap in
TRAPS: list[list[str]] = [
    ["3", "0006B001", "5", "10", "0", "tripwire"],
    ["3", "0006B002", "12", "-100", "0", "SwingingBlade"],
]
# What the rays of the plugin find around the companion, three numbers per direction, clockwise from straight ahead:
# what (0 = nothing, 1 = a wall, 2 = rising ground), how far in meters, after how many meters the ground drops (-1 = not)
# What the plugin finds by name: kind (0 = character, 1 = door, 2 = map marker, 3 = thing), ref ID, distance and angle
# from the player, 1 for a dead character, name
FINDABLE: list[list[str]] = [
    ["1", "0001A6F0", "20", "90", "0", "Warmaiden's"],
    ["0", "000A2C8E", "85", "-20", "0", "Balgruuf the Greater"],
    ["2", "000C0002", "260", "170", "0", "Whiterun Stables"],
    ["3", "0005A400", "4", "30", "0", "Chest"],
    ["0", "FF000A03", "9", "140", "1", "Bandit Outlaw"],
]
SPACE: list[int] = [1, 3, -1,  0, 0, -1,  0, 0, 4,  2, 8, -1,  0, 0, -1,  0, 0, -1,  1, 5, -1,  1, 2, -1]
# A look into one direction: three rays, from the left edge of the angle to the right one. And where the companion faces
SPACE_LOOK: list[int] = [0, 0, -1,  1, 40, -1,  0, 0, 12]
SPACE_FACING_DEGREES: int = 90
SPELL_REF_ID: str = "0004D3F2"
HORSE_REF_ID: str = "0010982B"
HORSE_NAME: str = "Horse"
PLAYER_HORSE_REF_ID: str = "0010982C"
PLAYER_HORSE_NAME: str = "Shadowmere"
# How many requests of the simulator a walk of its companion takes
WALK_POLLS: int = 4
CHEST_REF_ID: str = "0005A400"
LOCKED_CHEST_REF_ID: str = "0005A401"
# What the chest among the THINGS holds
CHEST_ITEMS: list[tuple[str, int, int, int, str]] = [
    ("0003EADD", 1, 46, 0, "Potion of Minor Healing"), ("0000000F", 40, 32, 0, "Gold"),
]
# What the game calls sitting and not sitting (Actor.GetSitState)
SIT_STATE_SEATED: int = 3
SIT_STATE_STANDING: int = 0
NATIVE_PLUGIN_VERSION: int = 5
# What was said since the last state as the SKSE plugin writes it: seconds ago, ref ID of the speaker, name, text
HEARD: list[list[str]] = [
    ["12", "00000014", "Dragonborn", "What do you know about dragons?"],
    ["9", "000F6845", "Whiterun Guard", "Only what the old tales say."],
    ["3", "000A2C94", "Lydia", "I am sworn to carry your burdens."],
]


# What happened since the last state as the SKSE plugin writes it: seconds ago, time of day in the game in minutes
# since midnight, urgent (1 or 0), what happened. The companion is "you" in these texts
EVENTS: list[list[str]] = [
    ["40", "870", "0", "The player entered Whiterun."],
    ["12", "874", "1", "Bandit started fighting the player."],
    ["3", "875", "0", "The player took 2 hits from Bandit and lost about 15 health (85% left)."],
]
# The one book the simulator can read, as the plugin writes its pages: about the book (page, pages, kind, ID, title),
# then the pieces of the page (how each joins what came before, and its text)
BOOK_REF_ID: str = "0001ACDA"
BOOK_TITLE: str = "Thief's Letter"
BOOK_PAGES: list[list[list[str]]] = [
    [["1", "Dear [Thief],"], ["1", "meet me at the mill when the"], ["0", "moon is up."]],
    [["1", "Bring the claw."]],
]
# The numbers the game mod gives the ways of sneaking (see game_state.SNEAK_MODES)
SNEAK_NUMBERS: dict[str, int] = {consts.SNEAK_AUTO: 0, consts.SNEAK_ON: 1, consts.SNEAK_OFF: 2}


GOLD: list[int] = [250, 30]
# What the game mod sends about the player on request: level, health, magicka, stamina, magicka and stamina in percent,
# weight carried, most that can be carried, perk points to spend, experience in this level, experience for the next one,
# then the eighteen skills. Here a level-up is waiting (the experience is above what it takes)
PLAYER_STATS: list[int] = [5, 150, 80, 120, 80, 100, 182, 300, 2, 410, 400,
                           32, 15, 27, 20, 18, 24, 21, 15, 22, 26, 17, 19, 15, 16, 25, 15, 23, 15]
# And about the companion: level, health, magicka, stamina, magicka and stamina in percent
COMPANION_STATS: list[int] = [6, 250, 50, 180, 100, 100]
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
                has_heard_lines: bool = False, inventory: str = "", corpse_ref_id: str = "", report: str = "",
                is_sitting: bool = False, has_things: bool = True, is_around_companion: bool = False,
                are_weapons_drawn: bool = False, look_target_ref_id: str = "", leader_ref_id: str = "", expression: str = "",
                has_stats: bool = False, is_visible_only: bool = False, has_traps: bool = False, has_space: bool = False, find: str = "",
                is_player_sitting: bool = False, is_weapon_kept_away: bool = False,
                is_running: bool = False, space_info: list[int] | None = None,
                horse_ref_id: str = "", is_riding: bool = False, is_ride_locked: bool = False, is_player_riding: bool = False,
                seat_name: str = "", player_horse_ref_id: str = "", called_horse_ref_id: str = "",
                horse_names: dict[str, str] | None = None, is_sneaking: bool = False, is_player_sneaking: bool = False,
                sneak_mode: int = 0, has_events: bool = False, read: str = "", read_page: int = 1,
                auto_heal_percent: int = consts.AUTO_HEAL_USUAL_PERCENT, lead_state: int = 0) -> dict:
    """Same short form as BuildState in KI_MCP_Gefaehrte_Loop.psc sends. What the numbers mean: game_state.py

    Args:
        has_heard_lines: The plugin of the game mod hands out every line only once, so only one state should carry them
        inventory: Whose inventory the server asked for (companion_constants.INVENTORY_...), or empty for none
        corpse_ref_id: For INVENTORY_CORPSE: whose body the server asked about
        report: What became of a loot or move_to since the last state, or empty
        is_sitting: Whether the companion sits on a seat because of the command sit
        has_things: Whether the server asked for the things close to the player
        is_around_companion: Whether the server asked for the lists as the companion sees them. The simulator
            then sends the same numbers and only says that they are measured from the companion
        are_weapons_drawn: Whether the player and the companion have their weapons out
        look_target_ref_id: What the companion looks at because of the command look_at, or empty
        leader_ref_id: Whom the companion follows because of the command follow with a target, or empty
        expression: The look on the companion's face because of the command expression, or empty
        has_stats: Whether the server asked for the level, the skills and so on of the player
        is_visible_only: Whether the server asked only for what can be seen. The simulator sends the same characters
            and only says that it filtered them
        is_player_sitting: Whether the player sits on a seat
        is_weapon_kept_away: Whether the companion keeps its weapon put away because of the command sheathe
        is_running: Whether the companion runs at the moment
        is_sneaking, is_player_sneaking: Whether the companion and the player sneak
        sneak_mode: How the companion sneaks because of the command sneak (see SNEAK_NUMBERS)
        has_events: The plugin of the game mod hands out every event only once, so only one state should carry them
        read: The ID of the book the server asked to read (empty = none), and read_page the page of it
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
        game_state.KEY_PLAYER_VALUES: [85, int(is_fighting), 23500, -7900, -3300, 45, 0, int(are_weapons_drawn),
                                       SIT_STATE_SEATED if is_player_sitting else SIT_STATE_STANDING, 0, int(is_player_sneaking)],
        # Snow at 14:35
        game_state.KEY_WORLD_VALUES: [3, 875, NATIVE_PLUGIN_VERSION, 1, game_state.GAME_PROTOCOL, int(is_around_companion), int(is_visible_only)],
        game_state.KEY_QUESTS: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in QUESTS],
        game_state.KEY_QUEST_TARGETS: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in QUEST_TARGETS],
        game_state.KEY_PLACES: [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in PLACES],
        game_state.KEY_COMPANION_NAME: "Lydia",
        game_state.KEY_COMPANION_VOICE: "FemaleEvenToned",
        game_state.KEY_COMPANION_VALUES: [
            to_papyrus_number("000A2C94"), 100, 0, int(is_fighting), int(is_waiting), 1, 3, -150,
            to_papyrus_number(target_ref_id), 180, int(is_fleeing), to_papyrus_number(walk_target_ref_id),
            SIT_STATE_SEATED if is_sitting else SIT_STATE_STANDING,
            int(are_weapons_drawn), to_papyrus_number(look_target_ref_id), to_papyrus_number(leader_ref_id),
            int(is_weapon_kept_away), int(is_running), int(is_sneaking), sneak_mode, auto_heal_percent, lead_state,
        ],
        game_state.KEY_NEARBY_VALUES: nearby_values,
        game_state.KEY_NEARBY_NAMES: nearby_names,
        game_state.KEY_GOLD: GOLD,
        game_state.KEY_HANDS: HANDS,
        game_state.KEY_TIMES: [0, 0],
    }
    # On what the two sit. A rider and its horse: the simulator's horse stands 6 m to the left until it is ridden
    if is_player_riding:
        state[game_state.KEY_PLAYER_MOUNT] = PLAYER_HORSE_NAME
    elif is_player_sitting and seat_name != "":
        state[game_state.KEY_PLAYER_SEAT] = seat_name
    if is_riding:
        state[game_state.KEY_COMPANION_MOUNT] = HORSE_NAME
    elif is_sitting and seat_name != "":
        state[game_state.KEY_COMPANION_SEAT] = seat_name
    if horse_ref_id != "":
        state[game_state.KEY_HORSE] = [to_papyrus_number(horse_ref_id), 0 if is_riding else 6, 0 if is_riding else -90, int(is_riding), int(is_ride_locked)]
        state[game_state.KEY_HORSE_NAME] = (horse_names or {}).get(horse_ref_id, HORSE_NAME)
    # The horse the player rode last stands 14 m behind the player until it is ridden
    if player_horse_ref_id != "":
        state[game_state.KEY_PLAYER_HORSE] = [to_papyrus_number(player_horse_ref_id), 0 if is_player_riding else 14, 0 if is_player_riding else 180]
        state[game_state.KEY_PLAYER_HORSE_NAME] = (horse_names or {}).get(player_horse_ref_id, PLAYER_HORSE_NAME)
    if called_horse_ref_id != "":
        state[game_state.KEY_HORSE_CALLED] = to_papyrus_number(called_horse_ref_id)
    wanted_inventories = [
        (game_state.KEY_INVENTORY_OWN, OWN_ITEMS, (consts.INVENTORY_OWN, consts.INVENTORY_BOTH)),
        (game_state.KEY_INVENTORY_PLAYER, PLAYER_ITEMS, (consts.INVENTORY_PLAYER, consts.INVENTORY_BOTH)),
    ]
    for key, items, choices in wanted_inventories:
        if inventory in choices:
            state[key], state[key + game_state.NAMES_SUFFIX] = build_inventory(items)
    # The spells the companion knows go along with its own inventory: how many, then form ID and magicka cost of each
    if inventory in (consts.INVENTORY_OWN, consts.INVENTORY_BOTH):
        state[game_state.KEY_SPELLS] = [1, to_papyrus_number(SPELL_REF_ID), 25]
        state[game_state.KEY_SPELLS + game_state.NAMES_SUFFIX] = ["0:Healing Hands"]
    # Three numbers for what the server asked about: ref ID, container or body, locked. A locked container has no inventory
    if inventory == consts.INVENTORY_MERCHANT and corpse_ref_id != "":
        # Like the plugin: whoever is asked about gets a line, a merchant or not
        state[game_state.KEY_MERCHANT_LINES] = MERCHANT_LINES if corpse_ref_id == MERCHANT_REF_ID else [f"{corpse_ref_id}\t1\t0\t30\t0\t0\t0\t0\t{name_of(corpse_ref_id)}"]
    names_key = game_state.KEY_INVENTORY_CORPSE + game_state.NAMES_SUFFIX
    if inventory == consts.INVENTORY_CORPSE and corpse_ref_id == CORPSE_REF_ID:
        state[game_state.KEY_CORPSE] = [to_papyrus_number(CORPSE_REF_ID), 0, 0]
        state[game_state.KEY_CORPSE_NAME] = "Bandit Outlaw"
        state[game_state.KEY_INVENTORY_CORPSE], state[names_key] = build_inventory(CORPSE_ITEMS)
    if inventory == consts.INVENTORY_CORPSE and corpse_ref_id == CHEST_REF_ID:
        state[game_state.KEY_CORPSE] = [to_papyrus_number(CHEST_REF_ID), 1, 0]
        state[game_state.KEY_CORPSE_NAME] = "Chest"
        state[game_state.KEY_INVENTORY_CORPSE], state[names_key] = build_inventory(CHEST_ITEMS)
    if inventory == consts.INVENTORY_CORPSE and corpse_ref_id == LOCKED_CHEST_REF_ID:
        state[game_state.KEY_CORPSE] = [to_papyrus_number(LOCKED_CHEST_REF_ID), 1, 1]
        state[game_state.KEY_CORPSE_NAME] = "Strongbox"
    if expression != "":
        state[game_state.KEY_COMPANION_EXPRESSION] = expression
    if has_stats:
        state[game_state.KEY_STATS_PLAYER] = PLAYER_STATS
        state[game_state.KEY_STATS_COMPANION] = COMPANION_STATS
    if has_things:
        state[game_state.KEY_THINGS] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in THINGS]
    # The game mod sends the traps with every state, the space around the companion only when the server asked for it
    if has_traps:
        state[game_state.KEY_TRAPS] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in TRAPS]
    if has_space:
        state[game_state.KEY_SPACE] = SPACE
        # A game mod whose plugin can look into one direction says how the rays looked. A look that is not
        # all around has the rays of SPACE_LOOK
        if space_info is not None:
            state[game_state.KEY_SPACE_INFO] = space_info
            if space_info[1] < game_state.SPACE_FULL_CIRCLE:
                state[game_state.KEY_SPACE] = SPACE_LOOK
    if find != "":
        # Like the plugin: every word has to be in the name, capitals do not matter, nearest first
        words = find.lower().split()
        matches = sorted((parts for parts in FINDABLE if all(word in parts[5].lower() for word in words)), key=lambda parts: int(parts[2]))
        state[game_state.KEY_FOUND_FOR] = find
        if len(matches) > 0:
            state[game_state.KEY_FOUND] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in matches]
    if report != "":
        state[game_state.KEY_REPORT] = report
    if has_heard_lines:
        state[game_state.KEY_HEARD] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in HEARD]
    if has_events:
        state[game_state.KEY_EVENTS] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in EVENTS]
    if read != "":
        # Like the game mod: the ID that was asked for goes back, and the page only if it is a book
        state[game_state.KEY_BOOK_FOR] = read
        if read == BOOK_REF_ID:
            page = min(max(read_page, 1), len(BOOK_PAGES))
            header = [str(page), str(len(BOOK_PAGES)), "1", BOOK_REF_ID, BOOK_TITLE]
            state[game_state.KEY_BOOK] = [game_state.QUEST_PART_SEPARATOR.join(parts) for parts in [header] + BOOK_PAGES[page - 1]]
    return state


# In the simulator the guard is a merchant as well: what it sells, and what it would buy from the companion
MERCHANT_REF_ID: str = "000F6845"
MERCHANT_LINES: list[str] = [
    "000F6845\t0\t750\t30\t8\t20\t2\t1\tWhiterun Guard",
    "0\t00013989\t2\t41\t85\tSteel Sword", "0\t0003EADD\t3\t46\t56\tPotion of Minor Healing",
    "1\t00013985\t1\t41\t17\tHunting Bow",
]

# What the companion does without changing where it walks, whom it follows or whether it sits
QUIET_ACTIONS: list[str] = [consts.ACTION_LOOK_AT, consts.ACTION_GESTURE, consts.ACTION_EXPRESSION, consts.ACTION_SAY, consts.ACTION_FACE, consts.ACTION_SNEAK,
                            consts.ACTION_SHEATHE, consts.ACTION_DRAW, consts.ACTION_OPEN, consts.ACTION_CLOSE,
                            consts.ACTION_SAY_TALK, consts.ACTION_SAY_END]


def name_of(ref_id: str) -> str:
    """The name of the character, thing, door or place with this ref_id, or an empty text if the simulator has none"""
    if ref_id == PLAYER_REF_ID:
        return "Dragonborn"
    for character in NEARBY:
        if character["ref_id"] == ref_id:
            return character["name"]
    for parts in THINGS + PLACES:
        if parts[1] == ref_id:
            return parts[-1]
    return ""


def horse_after(action: str, target: str, horse_ref_id: str) -> str:
    """Like the game: claim_horse, and mount with a target, make that horse the companion's own"""
    if action in (consts.ACTION_CLAIM_HORSE, consts.ACTION_MOUNT) and target != "":
        return target
    if action == consts.ACTION_RELEASE_HORSE and target in ("", horse_ref_id):
        return ""
    return horse_ref_id


def is_riding_after(action: str, is_riding: bool) -> bool:
    """Like the game: mount puts the companion in the saddle (the simulator's companion is up at once),
    dismount takes it down. wait leaves it in the saddle"""
    if action == consts.ACTION_MOUNT:
        return True
    if action == consts.ACTION_DISMOUNT:
        return False
    return is_riding


def horse_names_after(action: str, target: str, detail: str, horse_ref_id: str, horse_names: dict[str, str]) -> dict[str, str]:
    """Like the game: rename_horse gives the horse that is the target (without one the companion's own) a new name"""
    if action != consts.ACTION_RENAME_HORSE or (target or horse_ref_id) == "":
        return horse_names
    return {**horse_names, (target or horse_ref_id): detail}


def called_horse_after(action: str, target: str, horse_ref_id: str, called_horse_ref_id: str) -> str:
    """Like the game: call_horse makes the named horse run to the player (without a target the companion's own, and
    if it has none, the player's). The simulator's horse never arrives, so it keeps running until it is mounted"""
    if action == consts.ACTION_CALL_HORSE:
        return target or horse_ref_id or PLAYER_HORSE_REF_ID
    if action == consts.ACTION_MOUNT and called_horse_ref_id == (target or horse_ref_id):
        return ""
    return called_horse_ref_id


def sneak_mode_after(action: str, detail: str, sneak_mode: int) -> int:
    """Like the game: sneak with on or off holds until sneak with auto"""
    if action == consts.ACTION_SNEAK:
        return SNEAK_NUMBERS.get(detail, 0)
    return sneak_mode


def is_ride_locked_after(action: str, detail: str, is_ride_locked: bool) -> bool:
    if action == consts.ACTION_RIDE_LOCK:
        return detail != consts.RIDE_LOCK_OFF
    return is_ride_locked


def run_command(action: str, target: str, target_ref_id: str, item_id: str = "", detail: str = "", horse_ref_id: str = "",
                is_quiet: bool = False) -> tuple[bool, str, str]:
    """Answers attack, move_to, loot, sit, look_at, gesture, flee and stand_down like the game mod does

    Args:
        item_id: For gesture: the ID of the animation the server picked, as eight hex digits (empty = end the gesture)
        detail: For gesture: the name of the gesture
        is_quiet: For say: whether the companion says it in a low voice

    Returns:
        Whether it worked, the text for the AI, and whom the companion fights afterwards (ref_id or empty)
    """
    if action == consts.ACTION_ATTACK:
        for character in NEARBY:
            if character["ref_id"] == target:
                if detail == consts.ATTACK_HOLD:
                    return True, (f"You stay where you stand and attack {character['name']} from here. You keep to this spot, in this fight and "
                                  "after it, until a command sends you somewhere: 'follow' brings you back to the player."), target
                return True, f"You attack {character['name']} now.", target
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_FOLLOW and target not in ("", PLAYER_REF_ID):
        for character in NEARBY:
            if character["ref_id"] == target:
                if character.get("dead", False):
                    return False, f"{character['name']} is dead.", target_ref_id
                return True, (f"You follow {character['name']} now, wherever that character goes. "
                              "'follow' without a target brings you back to the player."), target_ref_id
        if name_of(target) != "":
            return False, f"{name_of(target)} is not a character. You can only follow characters.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_ENTER:
        for kind, ref_id, distance, angle, marker_type, known, name in PLACES:
            if ref_id == target:
                if kind != "1":
                    return False, f"{name} is not a door. Take the ref_id from 'Doors' in STATE.", target_ref_id
                return True, ("You walk to the door and go through it. Your next STATE tells you where you are then. "
                              "You wait there until 'follow'."), target_ref_id
        if name_of(target) != "":
            return False, f"{name_of(target)} is not a door. Take the ref_id from 'Doors' in STATE.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_FACE:
        if target == "":
            return True, f"You turn to face {detail}.", target_ref_id
        if name_of(target) != "":
            return True, f"You turn to face {name_of(target)}.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from STATE.", target_ref_id
    if action == consts.ACTION_MOVE:
        # The lower half of what the server sends where loot has the item ID is how far
        meters = int(item_id, 16) & 0xFFFF if item_id != "" else consts.MOVE_DEFAULT_METERS
        return True, (f"You walk about {meters} meters {detail} and wait there. Your next STATE tells you if you did not get that far. "
                      "'follow' brings you back to the player."), target_ref_id
    if action == consts.ACTION_PICK_UP:
        for kind, ref_id, distance, angle, flag, name in THINGS:
            if ref_id == target:
                if kind == "1":
                    return False, f"{name} is a container. Use 'loot' to take what is in it.", target_ref_id
                if kind != "2":
                    return False, f"{name} is nothing you can pick up.", target_ref_id
                return True, f"You walk to {name} to pick it up. Your next STATE tells you whether you got it.", target_ref_id
        for character in NEARBY:
            if character["ref_id"] == target:
                return False, f"{character['name']} is a character, not a thing to pick up.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id of an item from 'Things' in STATE (argument 'things').", target_ref_id
    if action == consts.ACTION_MAKE_ROOM:
        # The server sends how far where loot has the item ID, as eight hex digits
        meters = int(item_id, 16) if item_id != "" else consts.ROOM_DEFAULT_METERS
        if detail in ("left", "right"):
            return True, f"You step about {meters} meters to the {detail}, as the player sees it. Apart from that you wait or follow as before.", target_ref_id
        return True, f"You step back about {meters} meters, away from the player. Apart from that you wait or follow as before.", target_ref_id
    if action == consts.ACTION_EXPRESSION:
        if detail in consts.EXPRESSION_NONE_TEXTS:
            return True, "Your face is your own again.", target_ref_id
        return True, f"You look {detail} now. Your face stays like this until 'expression' with 'none'.", target_ref_id
    if action == consts.ACTION_MOVE_TO:
        # A walk can lead to a character, a thing, a door or a place
        if name_of(target) != "" and target != PLAYER_REF_ID:
            # Like the game mod: in front of a character the companion stops a little away, unless 'detail' says how far
            is_character = any(character["ref_id"] == target for character in NEARBY)
            meters = int(detail) if detail.isdecimal() else (consts.MOVE_TO_PERSON_METERS if is_character else 0)
            if meters > 0:
                return True, (f"You walk to {name_of(target)}, stop about {meters} meters in front, and wait there. "
                              "'follow' brings you back to the player."), target_ref_id
            return True, f"You walk to {name_of(target)} and wait there. 'follow' brings you back to the player.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_LOOK_AT:
        if target == "":
            return True, "You look where you like again.", target_ref_id
        if name_of(target) != "":
            return True, f"You look at {name_of(target)} now. 'look_at' without a target lets you look around freely again.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_SAY:
        said = "You say it quietly." if is_quiet else "You say it aloud."
        if target not in ("", PLAYER_REF_ID) and name_of(target) == "":
            return True, f"{said} But '{target}' is not around here, so you do not look at them while you speak.", target_ref_id
        return True, said, target_ref_id
    if action in (consts.ACTION_SAY_TALK, consts.ACTION_SAY_END):
        return True, "ok", target_ref_id
    if action == consts.ACTION_SHEATHE:
        return True, ("You put your weapon away. It stays away even when the player draws a weapon, until 'draw'. "
                      "In a fight you draw it all the same."), target_ref_id
    if action == consts.ACTION_DRAW:
        return True, "You draw your weapon. From now on you draw it and put it away together with the player again.", target_ref_id
    if action in (consts.ACTION_OPEN, consts.ACTION_CLOSE):
        # The fake game has one door that swings, right next to the companion, and the doors of 'Doors' lead elsewhere
        if target != "":
            return False, f"That door leads into another place (ref_id {target}). 'enter' takes you through it.", target_ref_id
        return True, ("You open the door." if action == consts.ACTION_OPEN else "You close the door."), target_ref_id
    if action == consts.ACTION_GESTURE:
        if item_id == "":
            return True, "You stand normally again.", target_ref_id
        if item_id in GESTURE_ANIMATIONS:
            return True, f"You do the gesture '{detail}'. If it goes on and on, 'gesture' with 'stop' ends it.", target_ref_id
        return False, f"The game has no animation for the gesture '{detail}'.", target_ref_id
    if action == consts.ACTION_LOOT:
        for character in NEARBY:
            if character["ref_id"] == target:
                if not character.get("dead", False):
                    return False, f"{character['name']} is alive. You can only loot the dead.", target_ref_id
                return True, f"You walk to the body of {character['name']} to take everything it carries. Your next STATE tells you what you got.", target_ref_id
        if target == CHEST_REF_ID:
            return True, "You walk to Chest to take everything in it. Your next STATE tells you what you got.", target_ref_id
        if target == LOCKED_CHEST_REF_ID:
            return False, "Strongbox is locked.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_SIT:
        for kind, ref_id, distance, angle, flag, name in THINGS:
            if ref_id == target:
                if kind != "0":
                    return False, f"{name} is nothing to sit on. Take the ref_id of a seat from 'Things' in STATE (argument 'things').", target_ref_id
                return True, f"You walk to {name} and sit down. Your next STATE tells you whether you sit. 'follow' makes you get up and come along again.", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action in consts.ITEM_ACTIONS:
        items = PLAYER_ITEMS if action == consts.ACTION_TAKE else OWN_ITEMS
        for item_id, count, form_type, equipped, name in items:
            if item_id == target:
                return True, f"Fake game did '{action}' with {name}.", target_ref_id
        return False, f"There is no item with the ID '{target}'. Take the ID from the inventory in STATE.", target_ref_id
    if action == consts.ACTION_RELEASE_HORSE:
        if horse_ref_id == "" or target not in ("", horse_ref_id):
            return False, "You have no horse to give back.", target_ref_id
        return True, "It is not your horse any more. It belongs to the player again.", target_ref_id
    if action == consts.ACTION_MOUNT and target == "" and horse_ref_id == "":
        return False, "You have no horse. Take one as yours first: claim_horse with the ref_id of a horse from 'Nearby' in STATE (ask the player which one).", target_ref_id
    if action == consts.ACTION_CALL_HORSE:
        if target not in ("", horse_ref_id, PLAYER_HORSE_REF_ID):
            return False, "That is neither your horse nor the horse of the player, so it does not come when called. claim_horse makes a horse yours.", target_ref_id
        is_players_horse = (target or horse_ref_id) == PLAYER_HORSE_REF_ID or (target == "" and horse_ref_id == "")
        # Every horse runs to its owner, unless the detail names the other one
        is_to_player = detail == consts.CALL_TO_PLAYER or (is_players_horse and detail != consts.CALL_TO_COMPANION)
        return True, f"{PLAYER_HORSE_NAME if is_players_horse else HORSE_NAME} runs to {'the player' if is_to_player else 'you'} and stays there.", target_ref_id
    if action == consts.ACTION_RENAME_HORSE:
        if target == "" and horse_ref_id == "":
            return False, "You have no horse to rename. Name a horse as the target, or take one as yours first with claim_horse.", target_ref_id
        return True, f"The horse is called {detail} now.", target_ref_id
    if action == consts.ACTION_LEAD:
        if name_of(target) == "" or target == PLAYER_REF_ID:
            return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
        return True, f"You lead the player to {name_of(target)}: you walk ahead, stop whenever the player falls behind, and go on once the player has caught up.", target_ref_id
    if action == consts.ACTION_AUTO_HEAL:
        if detail == consts.AUTO_HEAL_OFF:
            return True, "You do not heal by yourself any more. cast_spell still heals when you say so.", target_ref_id
        return True, "You heal the player by yourself, and after that yourself, whenever one of you is low on health.", target_ref_id
    if action in consts.TRADE_ACTIONS:
        if target != MERCHANT_REF_ID:
            return False, f"{name_of(target) or target} is no merchant.", target_ref_id
        return True, f"Fake game did '{action}' with the item {item_id} at {name_of(target)}.", target_ref_id
    if action == consts.ACTION_SHOOT:
        for character in NEARBY:
            if character["ref_id"] == target:
                if character.get("dead"):
                    return False, f"{character['name']} is already dead.", target_ref_id
                return True, (f"You draw your bow and shoot one arrow at {character['name']} from where you stand. That takes a few seconds. "
                              "You are told whether the game let you draw and let go: this command is new and still being tested."), target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_SLEEP:
        if target == BED_REF_ID:
            return True, ("You walk to Bed and lie down to sleep. You are told once you sleep, or if you could not lie down. "
                          "'follow' gets you up and makes you come along again."), target_ref_id
        for kind, ref_id, distance, angle, flag, name in THINGS:
            if ref_id == target:
                return False, f"{name} is no bed. Take the ref_id of a bed from 'Things' in STATE (argument 'things').", target_ref_id
        return False, f"There is nothing with the ref_id '{target}'. Take the ref_id from 'Nearby' in STATE.", target_ref_id
    if action == consts.ACTION_COVER:
        return True, ("You run to cover about 5 meters away, out of sight of the 1 enemies around, and keep to it until a command sends you "
                      "somewhere ('follow' brings you back to the player)."), target_ref_id
    if action == consts.ACTION_PASSIVE:
        if detail == consts.PASSIVE_OFF:
            return True, "You attack enemies of your own accord again.", target_ref_id
        if detail == consts.PASSIVE_FULL:
            return True, ("You do not fight at all now: you start no fight and you do not fight back either, not even when you are hit. "
                          "In a fight you keep close to the player. This holds until passive with 'on' (you defend yourself again) or 'off', "
                          "or until you are told to attack."), target_ref_id
        return True, ("You hold back now: you follow as always, but start no fight of your own accord. If you or the player are attacked, "
                      "you fight back, and 'attack' with a target still works. This holds until passive with 'off'."), target_ref_id
    if action == consts.ACTION_SNEAK:
        if detail == consts.SNEAK_ON:
            return True, "You sneak now, whatever the player does, until sneak with 'off' or 'auto'. Speak with 'quiet' while you sneak.", target_ref_id
        if detail == consts.SNEAK_OFF:
            return True, "You walk upright now, also while the player sneaks, until sneak with 'on' or 'auto'.", target_ref_id
        return True, "You sneak whenever the player sneaks again. The player walks upright at the moment, so you do too.", target_ref_id
    if action == consts.ACTION_FLEE:
        return True, "You retreat: you do not fight and you run from enemies. This holds until your next command.", ""
    if action == consts.ACTION_STAND_DOWN:
        if target_ref_id == "":
            return True, "You were not fighting.", ""
        return True, "You stop fighting. Whoever keeps attacking you or the player will pull you back into the fight.", ""
    return True, f"Fake game did '{action}'.", target_ref_id


def pack_quick_state(raw: dict) -> dict:
    """Packs a state the way the game mod sends it when its plugin has built it in one go (see
    game_state.KEY_QUICK_NUMBERS): the lists of numbers as blocks in one list, the texts as lines in another.
    What is only given when it was asked for stays a single entry"""
    packed = dict(raw)
    # The first numbers are for the game mod itself: how many of them there are, and four flags
    numbers: list[int] = [QUICK_HEAD_COUNT] + [0] * (QUICK_HEAD_COUNT - 1)
    for block, key in game_state.QUICK_BLOCK_KEYS.items():
        if key not in packed:
            continue
        value = packed.pop(key)
        values = [value] if isinstance(value, int) else list(value)
        numbers += [block, len(values)] + values
    texts: list[str] = []
    for key in QUICK_SINGLE_TEXTS + game_state.QUICK_TEXT_LISTS:
        if key not in packed:
            continue
        value = packed.pop(key)
        for text in [value] if isinstance(value, str) else value:
            texts.append(key + game_state.QUICK_TEXT_SEPARATOR + text)
    packed[game_state.KEY_QUICK_NUMBERS] = numbers
    packed[game_state.KEY_QUICK_TEXTS] = texts
    return packed


def pick_up_several(numbers: list[int]) -> tuple[bool, str]:
    """Answers pick_up with several things like the game mod does: it sets off, and what is nothing to pick up is left out

    Args:
        numbers: consts.VALUES_PER_PICK_UP numbers per thing: the two halves of its ref ID
    """
    names: list[str] = []
    refused_count = 0
    for place in range(len(numbers) // consts.VALUES_PER_PICK_UP):
        high, low = numbers[place * consts.VALUES_PER_PICK_UP:(place + 1) * consts.VALUES_PER_PICK_UP]
        wanted_id = f"{(high << 16) | low:08X}"
        found = [name for kind, ref_id, distance, angle, flag, name in THINGS if ref_id == wanted_id and kind == "2"]
        if len(found) == 0:
            refused_count += 1
        else:
            names.append(found[0])
    if len(names) == 0:
        return False, "You took nothing. Number 1 of your list is no thing the game has here. Take the ref_ids from 'Things' in STATE."
    note = f" {refused_count} of your list you leave out." if refused_count > 0 else ""
    if len(names) == 1:
        return True, f"You walk to {names[0]} to pick it up. Your next STATE tells you whether you got it." + note
    return True, f"You set off to pick up {len(names)} things, the nearest first: {names[0]}. Your next STATE tells you what you got." + note


def collect_around(meters: int, kind: str) -> tuple[bool, str]:
    """Answers pick_up with 'all' like the game mod does: everything loose within that many meters, of that kind"""
    names = [name for thing_kind, ref_id, distance, angle, flag, name in THINGS
             if thing_kind == "2" and int(distance) <= meters and kind in ("", consts.PICK_UP_NO_CLUTTER, LOOSE_KINDS.get(ref_id, ""))]
    kind_text = "" if kind == "" else (" that is no clutter" if kind == consts.PICK_UP_NO_CLUTTER else f" of the kind '{kind}'")
    if len(names) == 0:
        return False, f"You took nothing: there is nothing loose{kind_text} within {meters} meters around you that is free to take."
    if len(names) == 1:
        return True, f"You walk to {names[0]} to pick it up. Your next STATE tells you whether you got it."
    return True, f"You set off to pick up {len(names)} things, the nearest first: {names[0]}. Your next STATE tells you what you got."


def hand_over(action: str, numbers: list[int]) -> tuple[bool, str]:
    """Answers give and take with several items like the game mod does: what changed hands and what did not

    Args:
        numbers: consts.VALUES_PER_HAND_OVER numbers per item: the two halves of its ID and how many (0 = all of them)
    """
    items = PLAYER_ITEMS if action == consts.ACTION_TAKE else OWN_ITEMS
    done: list[str] = []
    failed: list[str] = []
    for place in range(len(numbers) // consts.VALUES_PER_HAND_OVER):
        high, low, wanted_count = numbers[place * consts.VALUES_PER_HAND_OVER:(place + 1) * consts.VALUES_PER_HAND_OVER]
        wanted_id = f"{(high << 16) | low:08X}"
        found = [(count, name) for item_id, count, form_type, equipped, name in items if item_id == wanted_id]
        if len(found) == 0:
            failed.append(f"number {place + 1} of your list (no such item)")
            continue
        count, name = found[0]
        done.append(f"{min(wanted_count, count) if wanted_count > 0 else count} {name}")
    if action == consts.ACTION_DROP:
        message = "Nothing was dropped."
        if len(done) > 0:
            message = "You drop: " + ", ".join(done) + ". It lies on the ground where you stand."
        if len(failed) > 0:
            message += " Not dropped: " + ", ".join(failed) + "."
        return len(done) > 0, message
    message = "Nothing changed hands."
    if len(done) > 0:
        message = ("You take from the player: " if action == consts.ACTION_TAKE else "You give the player: ") + ", ".join(done) + "."
    if len(failed) > 0:
        message += " Not handed over: " + ", ".join(failed) + "."
    return len(done) > 0, message


def is_sitting_after(action: str, is_sitting: bool) -> bool:
    """Like the game: 'sit' seats the companion (the simulator's companion is there at once), 'wait', 'stand_down',
    'look_at', 'gesture' and what it does with its things leave it seated, every other command makes it get up"""
    if action == consts.ACTION_SIT:
        return True
    if action in QUIET_ACTIONS + [consts.ACTION_WAIT, consts.ACTION_STAND_DOWN] or action in consts.ITEM_ACTIONS:
        return is_sitting
    return False


def is_waiting_after(action: str, is_waiting: bool) -> bool:
    """Like the game: 'wait', 'move_to' and 'enter' make the companion stay, 'follow' makes it come along again, everything else
    changes nothing. That goes for 'sit' as well: the companion sits because of a package of the game mod, and once it
    gets up it follows or waits as before"""
    if action in (consts.ACTION_WAIT, consts.ACTION_MOVE_TO, consts.ACTION_MOVE, consts.ACTION_ENTER):
        return True
    if action == consts.ACTION_FOLLOW:
        return False
    return is_waiting


def is_fleeing_after(action: str) -> bool:
    """Like the game: the retreat holds until the next command"""
    return action == consts.ACTION_FLEE


def walk_target_after(action: str, target: str, walk_target_ref_id: str = "") -> str:
    """Like the game: every new command ends the walk, except for 'look_at', 'gesture' and what the companion does
    with its things. The simulator's companion never arrives, so it keeps walking until then"""
    if action in QUIET_ACTIONS or action in consts.ITEM_ACTIONS:
        return walk_target_ref_id
    return target if action in (consts.ACTION_MOVE_TO, consts.ACTION_LOOT, consts.ACTION_PICK_UP, consts.ACTION_ENTER) else ""


def leader_after(action: str, target: str, leader_ref_id: str) -> str:
    """Like the game: 'follow' with a target makes the companion follow that character. A fight, stepping out of the way
    and what the companion does without moving from the spot leave that as it is. Every other command ends it"""
    if action == consts.ACTION_FOLLOW:
        return target if target != PLAYER_REF_ID else ""
    if action in QUIET_ACTIONS + [consts.ACTION_ATTACK, consts.ACTION_STAND_DOWN, consts.ACTION_MAKE_ROOM] or action in consts.ITEM_ACTIONS:
        return leader_ref_id
    return ""


def expression_after(action: str, detail: str, expression: str) -> str:
    """Like the game: the look stays on the companion's face until 'expression' takes it off again"""
    if action != consts.ACTION_EXPRESSION:
        return expression
    return "" if detail in consts.EXPRESSION_NONE_TEXTS else detail


def look_target_after(action: str, target: str, look_target_ref_id: str) -> str:
    """Like the game: the companion keeps looking at the target of 'look_at' until 'look_at' comes without one"""
    return target if action == consts.ACTION_LOOK_AT else look_target_ref_id


def main():
    parser = argparse.ArgumentParser(description="Fake Skyrim for testing the companion server")
    parser.add_argument("--port", type=int, default=4998)
    args = parser.parse_args()
    url = f"http://localhost:{args.port}{consts.ROUTE_GAME}"

    results: list[dict] = []
    is_waiting = False
    target_ref_id = ""
    is_fleeing = False
    is_sitting = False
    walk_target_ref_id = ""
    look_target_ref_id = ""
    leader_ref_id = ""
    expression = ""
    horse_ref_id = ""
    is_riding = False
    is_ride_locked = False
    sneak_mode = 0
    wanted_read = ""
    wanted_read_page = 1
    called_horse_ref_id = ""
    horse_names: dict[str, str] = {}
    # The simulator's companion arrives where it walks to after a few requests, and nothing it starts fails
    walk_polls_left = 0
    is_around_companion = False
    are_stats_wanted = False
    is_visible_wanted = False
    is_space_wanted = False
    space_info: list[int] | None = None
    wanted_find = ""
    poll_id = 0
    # Like the game mod: the state goes along with the first request, and after that only when the server asked for it
    is_state_wanted = True
    wanted_inventory = ""
    wanted_corpse = ""
    are_things_wanted = False
    # Like the game mod with its newest plugin: the state is built in one go, unless the server asks for the old way
    is_slow_wanted = False
    print(f"Fake game polling {url} (Ctrl+C to stop)")
    while True:
        poll_id += 1
        if walk_target_ref_id != "":
            walk_polls_left -= 1
            if walk_polls_left <= 0:
                walk_target_ref_id = ""
        request_body: dict = {consts.KEY_POLL_ID: poll_id, consts.KEY_RESULTS: results,
                              consts.KEY_BUSY: int(walk_target_ref_id != ""), consts.KEY_FAILS: 0}
        if is_state_wanted:
            # Only the first state has something that was said
            built_state = build_state(is_waiting, target_ref_id, is_fleeing, walk_target_ref_id, poll_id == 1, wanted_inventory, wanted_corpse, "", is_sitting, are_things_wanted,
                                      is_around_companion, target_ref_id != "", look_target_ref_id,
                                      leader_ref_id, expression, are_stats_wanted, is_visible_wanted,
                                      has_traps=True, has_space=is_space_wanted, find=wanted_find,
                                      space_info=space_info, horse_ref_id=horse_ref_id, is_riding=is_riding,
                                      is_ride_locked=is_ride_locked, player_horse_ref_id=PLAYER_HORSE_REF_ID,
                                      called_horse_ref_id=called_horse_ref_id, horse_names=horse_names,
                                      is_sneaking=sneak_mode == SNEAK_NUMBERS[consts.SNEAK_ON], sneak_mode=sneak_mode,
                                      has_events=poll_id == 1, read=wanted_read, read_page=wanted_read_page)
            # Rays and 'only what can be seen' are built the old way by the game mod as well
            is_built_slow = is_slow_wanted or is_space_wanted or is_visible_wanted
            request_body[consts.KEY_STATE] = built_state if is_built_slow else pack_quick_state(built_state)
            is_slow_wanted = False
            wanted_find = ""
            wanted_read = ""
            are_things_wanted = False
            is_space_wanted = False
            is_around_companion = False
            are_stats_wanted = False
            is_visible_wanted = False
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
                state_options = command.get(consts.KEY_COMMAND_STATE_OPTIONS)
                is_slow_wanted = isinstance(state_options, list) and len(state_options) > consts.STATE_OPTION_SLOW and bool(state_options[consts.STATE_OPTION_SLOW])
                wanted_inventory = str(detail or "")
                wanted_corpse = str(target or "")
                are_things_wanted = bool(command.get(consts.KEY_COMMAND_THINGS))
                is_around_companion = bool(command.get(consts.KEY_COMMAND_AROUND))
                are_stats_wanted = bool(command.get(consts.KEY_COMMAND_STATS))
                is_visible_wanted = bool(command.get(consts.KEY_COMMAND_VISIBLE))
                is_space_wanted = bool(command.get(consts.KEY_COMMAND_SPACE))
                # Like the game mod: without a direction all around from where the companion faces
                space_degrees = int(command.get(consts.KEY_COMMAND_SPACE_DEGREES, consts.SPACE_NO_DIRECTION))
                space_heading = SPACE_FACING_DEGREES
                space_angle = game_state.SPACE_FULL_CIRCLE
                if space_degrees >= 0:
                    space_heading = (space_degrees + (SPACE_FACING_DEGREES if command.get(consts.KEY_COMMAND_SPACE_FACING) else 0)) % 360
                    space_angle = int(command.get(consts.KEY_COMMAND_SPACE_ANGLE, game_state.SPACE_FULL_CIRCLE))
                space_info = [space_heading, space_angle, int(command.get(consts.KEY_COMMAND_SPACE_METERS, 0)) or consts.SPACE_DEFAULT_METERS,
                              SPACE_FACING_DEGREES]
                wanted_find = str(command.get(consts.KEY_COMMAND_FIND) or "")
                wanted_read = str(command.get(consts.KEY_COMMAND_READ) or "")
                wanted_read_page = int(command.get(consts.KEY_COMMAND_READ_PAGE) or 1)
                continue
            print(f"COMMAND  action={action}  target={target}  detail={detail}")
            # The ID of the item (loot) or of the animation (gesture) comes in two halves, like the game mod reads it
            item_number = (int(command.get(consts.KEY_COMMAND_ITEM_HIGH) or 0) << 16) | int(command.get(consts.KEY_COMMAND_ITEM_LOW) or 0)
            item_id = f"{item_number:08X}" if item_number != 0 else ""
            is_ok, message, target_ref_id = run_command(str(action), str(target or ""), target_ref_id, item_id, str(detail or ""), horse_ref_id,
                                                        bool(command.get(consts.KEY_COMMAND_QUIET)))
            if action == consts.ACTION_PICK_UP and int(command.get(consts.KEY_COMMAND_PICK_ALL) or 0) > 0:
                # Everything loose around
                is_ok, message = collect_around(int(command[consts.KEY_COMMAND_PICK_ALL]), str(command.get(consts.KEY_COMMAND_PICK_FILTER) or ""))
            elif action == consts.ACTION_PICK_UP and isinstance(command.get(consts.KEY_COMMAND_ITEMS), list):
                is_ok, message = pick_up_several([int(number) for number in command[consts.KEY_COMMAND_ITEMS]])
            elif isinstance(command.get(consts.KEY_COMMAND_ITEMS), list):
                # give or take with several items in one go
                is_ok, message = hand_over(str(action), [int(number) for number in command[consts.KEY_COMMAND_ITEMS]])
            if is_ok:
                called_horse_ref_id = called_horse_after(str(action), str(target or ""), horse_ref_id, called_horse_ref_id)
                horse_ref_id = horse_after(str(action), str(target or ""), horse_ref_id)
                is_riding = is_riding_after(str(action), is_riding)
                is_ride_locked = is_ride_locked_after(str(action), str(detail or ""), is_ride_locked)
                sneak_mode = sneak_mode_after(str(action), str(detail or ""), sneak_mode)
                is_waiting = is_waiting_after(str(action), is_waiting)
                # Looking somewhere, a gesture and stepping out of the way do not end a retreat
                if action not in QUIET_ACTIONS + [consts.ACTION_MAKE_ROOM]:
                    is_fleeing = is_fleeing_after(str(action))
                is_sitting = is_sitting_after(str(action), is_sitting)
                walk_target_ref_id = walk_target_after(str(action), str(target or ""), walk_target_ref_id)
                walk_polls_left = WALK_POLLS
                horse_names = horse_names_after(str(action), str(target or ""), str(detail or ""), horse_ref_id, horse_names)
                look_target_ref_id = look_target_after(str(action), str(target or ""), look_target_ref_id)
                leader_ref_id = leader_after(str(action), str(target or ""), leader_ref_id)
                expression = expression_after(str(action), str(detail or ""), expression)
            results.append({
                consts.KEY_COMMAND_ID: command.get(consts.KEY_COMMAND_ID),
                consts.KEY_RESULT_OK: is_ok,
                consts.KEY_RESULT_MESSAGE: message,
            })


if __name__ == "__main__":
    main()
