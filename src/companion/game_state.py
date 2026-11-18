"""The game state: from the short form the game mod sends to the layout the AI side works with.

Every value the game mod hands to its HTTP plugin costs the game one frame, and so does every question
it asks the game. So the mod sends a few texts and a few lists of plain numbers instead of one named entry
per value (BuildState in KI_MCP_Gefaehrte_Loop.psc of the game mod). This module gives the numbers their
names back. The order of the numbers in each list has to match that script.
"""
import math
from typing import Any

from src.companion.ref_ids import format_ref_id, parse_ref_id

# Keys of the state as the game mod sends it
KEY_LOCATION: str = "location"
KEY_PLAYER_NAME: str = "player_name"
# health in percent, in combat (0 or 1), position x, y and z in game units,
# direction the player faces in degrees (0 = north, 90 = east), indoors (0 or 1),
# weapon drawn (0 or 1; older game mods leave this number out)
KEY_PLAYER_VALUES: str = "player"
KEY_COMPANION_NAME: str = "companion_name"
# The name of the companion's voice as the files of the game know it (eg FemaleEvenToned), for the command say.
# Only from a game mod whose SKSE plugin can tell it
KEY_COMPANION_VOICE: str = "companion_voice"
# ref ID, health in percent, dead, in combat, waiting, in the loaded area (each 0 or 1), distance to the player in meters,
# angle from where the player faces to the companion in degrees (negative = left), ref ID of its combat target (0 = none),
# direction the companion faces in degrees (0 = north, 90 = east), retreating because of the command flee (0 or 1),
# ref ID of the character or thing it is walking to because of the commands move_to, loot or sit (0 = none),
# sitting as the game tells it (see SIT_STATES_SEATED; older game mods leave this number out),
# weapon drawn (0 or 1), ref ID of what it looks at because of the command look_at (0 = nothing in particular;
# older game mods leave both numbers out), ref ID of the character it follows because of the command follow
# with a target (0 = the player or nobody; older game mods leave this number out)
KEY_COMPANION_VALUES: str = "companion"
# Only if the companion is not in the loaded area: the name of the building (cave ...) it is in, if the game has one
KEY_COMPANION_PLACE: str = "companion_place"
# Only if the companion's face has a look because of the command expression: that look as the AI named it
KEY_COMPANION_EXPRESSION: str = "companion_expression"
# 1 while the companion holds back because of the command passive. Left out otherwise
KEY_COMPANION_PASSIVE: str = "companion_passive"
# 1 while the companion keeps to its spot because of attack with 'hold'. Left out otherwise
KEY_COMPANION_HOLDING: str = "companion_holding"
# 1 while the spot the companion keeps to is a cover it took because of the command cover. Left out otherwise
KEY_COMPANION_COVERING: str = "companion_covering"
# Only when the server asked for it. 29 numbers about the player: level, health, magicka and stamina as they are now,
# magicka and stamina in percent, weight carried, the most that can be carried, perk points not spent yet,
# experience gathered in this level, experience it takes to reach the next one, then the skills (see PLAYER_SKILLS)
KEY_STATS_PLAYER: str = "stats_player"
# With it, 6 numbers about the companion: level, health, magicka, stamina, magicka and stamina in percent
KEY_STATS_COMPANION: str = "stats_companion"
# weather outside (see WEATHER_NAMES, -1 = unknown), time of day in minutes since midnight,
# interface version of the SKSE plugin of the game mod (0 = not loaded, so the game cannot tell the quests),
# whether that plugin listens to what characters say (0 or 1),
# number of the protocol the game mod speaks (see GAME_PROTOCOL; older game mods leave this number out),
# whether the lists of this state are seen from the companion (1) or from the player (0; older game mods leave this out).
# "The viewer" below is the one of the two from whom they are seen,
# whether the list of characters only holds those the viewer can see (1; older game mods leave this out)
KEY_WORLD_VALUES: str = "world"
# One text per open quest of the journal, tracked ones first. Its parts are separated by tabs:
# tracked (1 or 0), type of the quest as the game numbers it, name of the quest, then the text of each open objective.
# The SKSE plugin of the game mod writes these lines (GetOpenQuests in plugin.cpp of the plugin repository)
KEY_QUESTS: str = "quests"
# One text per target of a tracked quest (what the compass of the game points to). Its parts are separated by tabs:
# place of the quest in the list above (from 0), place of the objective in that quest (from 0), where the target is
# (see TARGET_KINDS), distance to the viewer in meters, angle from where the viewer faces in degrees (negative = left)
# - for a target behind a door these are the distance and angle of that door, for a target somewhere else both are 0 -,
# name of the target (can be empty), name of the place behind the door or of the place the target is in (can be empty),
# ref ID to walk to as eight hex digits: the target, or the door to it, 00000000 for a target somewhere else
# (older plugins leave this part out).
# The SKSE plugin of the game mod writes these lines (GetQuestTargets in plugin.cpp of the plugin repository)
KEY_QUEST_TARGETS: str = "quest_targets"
# One text per place around the viewer: map markers first, then doors that lead into another area, each nearest first.
# Its parts are separated by tabs: kind (see PLACE_KINDS), ref ID of the marker or the door as eight hex digits
# (older plugins leave this part out), distance to the viewer in meters, angle as above,
# for a map marker the kind of place as the game numbers it (see MARKER_TYPES), for a map marker 1 if the player
# has been there, name of the place (for a door: of the place it leads to).
# The SKSE plugin of the game mod writes these lines (GetNearbyPlaces in plugin.cpp of the plugin repository)
KEY_PLACES: str = "places"
# Only when the server asked for it. One text per thing close to the viewer: furniture first, then containers, then items that lie around, each nearest first.
# Its parts are separated by tabs: kind (see THING_KINDS), ref ID as eight hex digits, distance to the viewer in meters,
# angle as above, for furniture 1 if a character can sit on it and for a container 1 if it is locked, name.
# The SKSE plugin of the game mod writes these lines (GetNearbyThings in plugin.cpp of the plugin repository)
KEY_THINGS: str = "things"
# One text per trap close to the viewer, nearest first, built like the lines of the things: kind (always THING_TRAP_NUMBER),
# ref ID, distance, angle, 0, and the kind of trap as the game files its model (see TRAP_NAMES). They come with every state.
# The SKSE plugin of the game mod writes these lines (GetNearbyTraps in plugin.cpp of the plugin repository)
KEY_TRAPS: str = "traps"
# Only when the server asked for it. What rays found around the companion: SPACE_VALUES_PER_DIRECTION numbers per direction,
# the first direction straight ahead of the companion and the others clockwise from there, evenly spread:
# what the ray met (see SPACE_OBSTACLES), how far away in meters, after how many meters the ground drops away (SPACE_NO_DROP = it does not).
# The SKSE plugin of the game mod measures this (GetSurroundings in plugin.cpp of the plugin repository)
KEY_SPACE: str = "space"
# Comes with KEY_SPACE from a game mod whose plugin can look into one direction (interface version 13). How the rays
# looked, SPACE_INFO_COUNT numbers: the middle of the look as a point of the compass in degrees (0 = north, 90 = east),
# the angle the rays fan out over (SPACE_FULL_CIRCLE = all around: the first ray goes into the direction, the others
# clockwise from there; less: the rays go from the left edge of the angle to the right one), how far they reach in
# meters, and where the companion faces in degrees
KEY_SPACE_INFO: str = "space_info"
# Only when the server asked for it: the picture in front of the companion as numbers (see GetView in the plugin of the
# game mod): the format, columns, rows, degrees from left to right, how many meters the rays reach, how many entries
# the legend has, how many numbers each entry and each cell has, then the legend (a character or a thing, the two
# halves of its ref ID, its flags or kind) and the cells row by row from the top (what the ray met, its meters,
# whether it found a drop, and the two nearest entries of the legend with their meters). The names of the legend
# come as texts of their own
KEY_VIEW: str = "view"
KEY_VIEW_NAMES: str = "view_names"
# Only with a state that is about what the companion sees ('visible' from the companion, 'space', 'view'): how far it
# sees right now (see UpdateSight in the plugin of the game mod). The meters, the weather (see WEATHER_NAMES, -1
# indoors or not known), the part the time of day leaves in percent, 1 if indoors, the light (see SIGHT_LIGHTS), 1 if a
# torch keeps the sight from being shorter, the light level the game tells (0 to 150), the time of day in minutes
KEY_SIGHT: str = "sight"
# How much higher or lower than the viewer the characters and things of the lists stand (see TakeHeights in the plugin
# of the game mod): two numbers each, the ref ID and the whole meters above the feet of the viewer (negative = below).
# Only what stands at least 2 meters higher or lower is in it
KEY_HEIGHTS: str = "heights"
FURNITURE_IS_SEAT: int = 1
FURNITURE_IS_BED: int = 2
VALUES_PER_HEIGHT: int = 2
# The lists of a state whose entries can carry a height
LISTS_WITH_HEIGHTS: tuple[str, ...] = ("nearby", "things", "traps", "found")
SIGHT_VALUE_COUNT: int = 8
SIGHT_LIGHTS: dict[int, str] = {1: "bright", 2: "dim", 3: "dark"}
VIEW_FORMAT: int = 1
VIEW_HEAD_COUNT: int = 8
VIEW_TERRAINS: dict[int, str] = {1: "ground", 2: "wall", 3: "roof", 4: "water", 5: "water", 6: "water"}
# Water is a try with two ways at once: the ray met the layer the game has for water, the ground it met lies below the
# water height of the cell, or both. Which of them found it goes along for testing ('water_by' of a cell)
VIEW_WATER_WAYS: dict[int, str] = {4: "layer", 5: "height", 6: "both"}
VIEW_IS_CREATURE: int = 1
VIEW_CREATURE_HOSTILE: int = 1
VIEW_CREATURE_DEAD: int = 2
VIEW_CREATURE_PLAYER: int = 4
VIEW_THING_KINDS: dict[int, str] = {1: "door", 2: "container", 3: "furniture", 4: "item"}
SPACE_INFO_COUNT: int = 4
SPACE_FULL_CIRCLE: int = 360
# Only when the server asked for a search by name: the text that was looked for, and one text per match, nearest first,
# built like the lines of the things: kind (see FOUND_KINDS), ref ID, distance, angle, for a character 1 if it is dead, name
# (for a door: of the place it leads to). No list means nothing was found.
# The SKSE plugin of the game mod searches (FindByName in plugin.cpp of the plugin repository)
KEY_FOUND_FOR: str = "found_for"
KEY_FOUND: str = "found"
# One text per line a character near the player said since the last state (the subtitles of the game), oldest first.
# Its parts are separated by tabs: how many seconds ago, ref ID of the speaker as eight hex digits, name of the speaker,
# what was said. A line the player picked in a dialogue menu has the player as its speaker.
# The SKSE plugin of the game mod writes these lines (TakeHeardLines in plugin.cpp of the plugin repository)
KEY_HEARD: str = "heard"
# Five numbers per character, living or dead: ref ID, distance to the viewer in meters,
# angle from where the viewer faces to the character in degrees (negative = left), hostile to the player (0 or 1), dead (0 or 1)
KEY_NEARBY_VALUES: str = "nearby"
# Instead of the two lists of characters if the companion looks around inside a building the player is not in
# (the game has not loaded it, so only its SKSE plugin can tell who is there, and only who the game keeps in memory).
# One text per character, nearest first. Its parts are separated by tabs: ref ID as eight hex digits, distance to the
# companion in meters, angle from where it faces in degrees, dead (1 or 0), name.
# The SKSE plugin of the game mod writes these lines (GetCharactersInCell in plugin.cpp of the plugin repository)
KEY_NEARBY_LINES: str = "nearby_lines"
# One text per character: its place in the list above, a colon and its name, eg "0:Bandit".
# The HTTP plugin of the game drops names it cannot send without a word, so the place cannot be counted
KEY_NEARBY_NAMES: str = "nearby_names"
# With 'visible' and the companion as the viewer (plugin of the game mod from PLUGIN_VERSION_WITH_SENSES on): by which
# sense the companion notices each character of KEY_NEARBY_VALUES, one number per character (see SENSE_NAMES). The
# list then holds whom it sees, whom it has within about two meters all around, and whom it hears without seeing
KEY_NEARBY_SENSES: str = "nearby_senses"
SENSE_SEEN: str = "seen"
SENSE_CLOSE: str = "close"
SENSE_HEARD: str = "heard"
SENSE_NAMES: dict[int, str] = {1: SENSE_SEEN, 2: SENSE_CLOSE, 3: SENSE_HEARD}
PLUGIN_VERSION_WITH_SENSES: int = 25
# From the same version on the plugin tells how long it took for a state in one go in tenths of a millisecond
QUICK_TIME_STEPS_PER_MILLISECOND: int = 10
# Gold of the player and of the companion (0 without a companion)
KEY_GOLD: str = "gold"
# What the companion holds: two texts, "R:" and "L:" followed by the name of what is in that hand (nothing = empty hand)
KEY_HANDS: str = "hands"
# Only when the server asked for it: the inventory of the companion and of the player.
# First number: how many kinds of items there are in all (the game mod cuts long inventories off).
# Then four numbers per item: form ID, how many, form type (see ITEM_GROUPS), worn or held (0 or 1). All 0 = left out.
# The names are under the same key with NAMES_SUFFIX, built like KEY_NEARBY_NAMES
# From a game mod whose plugin lists inventories (interface version 18) they come as texts instead, under the same
# key with LINES_SUFFIX: first how many kinds of items there are (or passed the filter), then one text per item, its
# parts separated by tabs: form ID as eight hex digits, how many, form type, the fourth number from above, name.
# The SKSE plugin of the game mod writes these lines (GetInventoryLines in plugin.cpp of the plugin repository)
LINES_SUFFIX: str = "_lines"
INVENTORY_LINE_PART_COUNT: int = 5
KEY_INVENTORY_OWN: str = "inventory_own"
KEY_INVENTORY_PLAYER: str = "inventory_player"
# Only when the server asked for it: what a dead character carries or a container holds, built like the inventories above.
# Under KEY_CORPSE three numbers: its ref ID, container (1) or body (0), locked (0 or 1; older game mods send the ref ID only),
# and its name under KEY_CORPSE_NAME. A locked container comes without an inventory.
# All of it is missing if the game found no such body or container
KEY_INVENTORY_CORPSE: str = "inventory_corpse"
# Comes with the own inventory, from a game mod that knows spells: how many spells the companion knows, then
# VALUES_PER_SPELL numbers per spell: its form ID and what it costs in magicka. The names are under the same key
# with NAMES_SUFFIX, built like KEY_NEARBY_NAMES
KEY_SPELLS: str = "spells"
VALUES_PER_SPELL: int = 2
# On what the player and the companion sit, from a game mod that knows riding. The mount keys hold the name of the horse
# a character rides (UNKNOWN_MOUNT_NAME if the game mod cannot tell which horse: its plugin is older than interface
# version 14). The seat keys hold the name of the piece of furniture a character sits on. Each is missing if it does not apply
KEY_PLAYER_MOUNT: str = "player_mount"
KEY_PLAYER_SEAT: str = "player_seat"
KEY_COMPANION_MOUNT: str = "companion_mount"
KEY_COMPANION_SEAT: str = "companion_seat"
UNKNOWN_MOUNT_NAME: str = "?"
# The horse of the companion, missing if it has none: its form ID, how far it is from the companion in meters
# (HORSE_NOT_LOADED = not in the loaded area), the angle seen from where the companion faces, 1 if the companion is to
# ride it, 1 if the companion does not mount and dismount together with the player (ride_lock). Its name is under KEY_HORSE_NAME
KEY_HORSE: str = "horse"
KEY_HORSE_NAME: str = "horse_name"
HORSE_VALUE_COUNT: int = 5
HORSE_NOT_LOADED: int = -1
# The horse the player rode last, missing if there is none: its form ID, how far it is from the player in meters
# (HORSE_NOT_LOADED = not in the loaded area) and the angle seen from where the player faces. Its name is under
# KEY_PLAYER_HORSE_NAME. KEY_HORSE_CALLED: the form ID of the horse that runs to the player because of call_horse
KEY_PLAYER_HORSE: str = "player_horse"
KEY_PLAYER_HORSE_NAME: str = "player_horse_name"
PLAYER_HORSE_VALUE_COUNT: int = 3
KEY_HORSE_CALLED: str = "horse_called"
KEY_CORPSE: str = "corpse"
KEY_CORPSE_NAME: str = "corpse_name"
# What became of the commands loot, move_to and sit since the last state, as text for the AI. Missing if that is nothing
KEY_REPORT: str = "report"
# What a merchant offers, as the plugin of the game mod lists it (only when the server asked for it). The first text,
# its parts separated by tabs: ref ID of the merchant as eight hex digits, status (see MERCHANT_STATUSES), gold of the
# merchant, gold of the companion, the hours the merchant trades from and to, how many kinds of wares it has, how
# many kinds of things the companion could sell, name. Then one text per thing: side (0 = the merchant sells it,
# 1 = the companion could sell it), form ID as eight hex digits, how many, form type, price of one piece, name
KEY_MERCHANT_LINES: str = "merchant_lines"
MERCHANT_HEAD_PART_COUNT: int = 9
MERCHANT_ITEM_PART_COUNT: int = 6
MERCHANT_TRADES: str = "trades"
MERCHANT_IS_NONE: str = "none"
MERCHANT_IS_CLOSED: str = "closed"
MERCHANT_STATUSES: dict[int, str] = {0: MERCHANT_TRADES, 1: MERCHANT_IS_NONE, 2: MERCHANT_IS_CLOSED}
MERCHANT_SIDE_COMPANION: int = 1
# One text per thing that happened since the last state (a fight, a death, hits, what the player used, read, took),
# oldest first. Its parts are separated by tabs: how many seconds ago, the time of day in the game in minutes since
# midnight, urgent (1 or 0), what happened. The companion is called "you" in these texts.
# The SKSE plugin of the game mod writes these lines (TakeEvents in plugin.cpp of the plugin repository)
KEY_EVENTS: str = "events"
EVENT_PART_COUNT: int = 4
# Only when the server asked to read a book: the ID it asked for under KEY_BOOK_FOR, and under KEY_BOOK the page.
# The first text tells about the book, its parts separated by tabs: the number of this page, how many pages there
# are (0 = the book has no text), the kind (see BOOK_KINDS), the form ID of the book as eight hex digits, its title.
# Then one text per piece of the page: how it joins what came before (see BOOK_PIECE_...), a tab, the text.
# No KEY_BOOK means that the ID is no book. The plugin writes these lines (GetBookText in plugin.cpp)
KEY_BOOK: str = "book"
KEY_BOOK_FOR: str = "book_for"
BOOK_HEADER_PART_COUNT: int = 5
BOOK_KINDS: dict[int, str] = {0: "book", 1: "note", 2: "spell tome"}
BOOK_PIECE_BEGINS_PARAGRAPH: str = "1"
BOOK_PIECE_FOLLOWS_BLANK: str = "0"
BOOK_PIECE_FOLLOWS_DIRECTLY: str = "2"
NAMES_SUFFIX: str = "_names"
# How long the game mod took in milliseconds: for the whole state, and for the characters nearby alone
KEY_TIMES: str = "times_ms"
# From protocol 16 on the plugin of the game mod collects what goes along with every state in one go (GetWholeState in
# plugin.cpp), and the game mod sends that packed into two lists, because every single entry costs it a picture of
# the game. KEY_QUICK_NUMBERS: first how many numbers are meant for the game mod itself (they are skipped here), then
# one block per list of numbers of the state: which one (QUICK_BLOCK_KEYS), how many numbers, the numbers.
# KEY_QUICK_TEXTS: one line per text: the key of the entry, a tab, the text; the entries in QUICK_TEXT_LISTS are lists
# and have one line per text. expand_quick_state turns both back into the entries above. What a state is only
# given when it was asked for (things, inventories ...) the game mod still sends as single entries next to them
KEY_QUICK_NUMBERS: str = "quick_numbers"
KEY_QUICK_TEXTS: str = "quick_texts"
# Set by expand_quick_state: this state was built in one go. The second time in KEY_TIMES is then how long the plugin took
KEY_BUILT_QUICK: str = "built_quick"
# From plugin version 26 on the plugin works out health, sitting, sleeping and running itself, and the game mod checks
# that now and then against what the game tells its script. Where the two differ it sends this text. Only for the log
KEY_PLUGIN_MISMATCH: str = "plugin_mismatch"
QUICK_BLOCK_OF_HORSE_CALLED: int = 6
QUICK_BLOCK_KEYS: dict[int, str] = {1: KEY_TIMES, 2: "player", 3: "world", 4: "companion", 5: "gold", QUICK_BLOCK_OF_HORSE_CALLED: "horse_called",
                                    7: "player_horse", 8: "horse", 9: "nearby", 10: "nearby_senses"}
QUICK_TEXT_LISTS: tuple[str, ...] = ("quests", "quest_targets", "places", "traps", "heard", "events", "hands", "nearby_names")
QUICK_TEXT_SEPARATOR: str = "\t"

PLAYER_VALUE_COUNT: int = 11
# Older game mods say nothing about sneaking, still older ones nothing about sitting and sleeping, and the oldest
# nothing about the weapon
PLAYER_VALUE_COUNT_WITHOUT_SNEAKING: int = 10
PLAYER_PLACE_OF_SNEAKING: int = 10
PLAYER_VALUE_COUNT_WITHOUT_SITTING: int = 8
PLAYER_VALUE_COUNT_WITHOUT_WEAPON: int = 7
PLAYER_PLACE_OF_WEAPON: int = 7
PLAYER_PLACE_OF_SITTING: int = 8
PLAYER_PLACE_OF_SLEEPING: int = 9
COMPANION_VALUE_COUNT: int = 22
# Older game mods say nothing about healing by itself (below how many percent of health, 0 = off) and about leading
# the player (see LEAD_STATES)
COMPANION_VALUE_COUNT_WITHOUT_HEALING: int = 20
COMPANION_PLACE_OF_AUTO_HEAL: int = 20
COMPANION_PLACE_OF_LEADING: int = 21
LEAD_WALKING: str = "walking"
LEAD_WAITING: str = "waiting"
LEAD_STATES: dict[int, str] = {1: LEAD_WALKING, 2: LEAD_WAITING}
# Older game mods say nothing about sneaking: whether the companion sneaks, and how it does because of the command
# sneak (see SNEAK_MODES)
COMPANION_VALUE_COUNT_WITHOUT_SNEAKING: int = 18
COMPANION_PLACE_OF_SNEAKING: int = 18
COMPANION_PLACE_OF_SNEAK_MODE: int = 19
# The command sneak as the game mod numbers it: as the player does, always, never
SNEAK_MODES: dict[int, str] = {0: "auto", 1: "on", 2: "off"}
# Older game mods say nothing about running
COMPANION_VALUE_COUNT_WITHOUT_RUNNING: int = 17
COMPANION_PLACE_OF_RUNNING: int = 17
# Older game mods say nothing about a weapon that is kept put away (sheathe)
COMPANION_VALUE_COUNT_WITHOUT_KEPT_WEAPON: int = 16
COMPANION_PLACE_OF_KEPT_WEAPON: int = 16
# Older game mods say nothing about whom the companion follows, still older ones nothing about the weapon and the look,
# and the oldest nothing about sitting either
COMPANION_VALUE_COUNT_WITHOUT_LEADER: int = 15
COMPANION_VALUE_COUNT_WITHOUT_WEAPON: int = 13
COMPANION_VALUE_COUNT_WITHOUT_SITTING: int = 12
COMPANION_PLACE_OF_SITTING: int = 12
COMPANION_PLACE_OF_WEAPON: int = 13
COMPANION_PLACE_OF_LOOK_TARGET: int = 14
COMPANION_PLACE_OF_LEADER: int = 15
PLAYER_STATS_BEFORE_SKILLS: int = 11
# The skills of the player in the order the game mod sends them (GetSkillNames in KI_MCP_Gefaehrte_Loop.psc),
# by the names the game shows for them in English
PLAYER_SKILLS: list[str] = [
    "One-Handed", "Two-Handed", "Archery", "Block", "Smithing", "Heavy Armor", "Light Armor", "Pickpocket", "Lockpicking",
    "Sneak", "Alchemy", "Speech", "Alteration", "Conjuration", "Destruction", "Illusion", "Restoration", "Enchanting",
]
COMPANION_STATS_COUNT: int = 6
NEARBY_LINE_PART_COUNT: int = 5
# What the game calls sitting (Actor.GetSitState): 3 = seated, 4 = seated and about to get up
SIT_STATES_SEATED: tuple[int, ...] = (3, 4)
# What the game calls sleeping (Actor.GetSleepState): 3 = asleep, 4 = asleep and about to wake up
SLEEP_STATES_ASLEEP: tuple[int, ...] = (3, 4)
WORLD_VALUE_COUNT: int = 7
WORLD_PLACE_OF_PROTOCOL: int = 4
WORLD_PLACE_OF_AROUND: int = 5
WORLD_PLACE_OF_VISIBLE: int = 6
# What the viewer can see lies within this many degrees to the left and right of where it faces
# (VisibleHalfAngleDegrees in KI_MCP_Gefaehrte_Loop.psc). The things are filtered by it here
VISIBLE_HALF_ANGLE_DEGREES: int = 60
# The flag of a container in 'things' counts its items in steps of this (see _read_things)
CONTAINER_COUNT_STEP: int = 2
# From this interface version on the SKSE plugin of the game mod leaves out the places and doors behind walls for 'visible'
PLUGIN_VERSION_WITH_SIGHT: int = 15
# Goes up whenever the lists of numbers in the state change their length or meaning, together with ProtocolNumber
# in KI_MCP_Gefaehrte_Loop.psc of the game mod. A game mod that sends another number (or none: 0) does not fit this server
GAME_PROTOCOL: int = 22
CORPSE_VALUE_COUNT: int = 3
CORPSE_VALUE_COUNT_WITHOUT_KIND: int = 1
# Older game mods send fewer numbers: only the weather and the time before there were quests,
# and nothing about listening before there was hearing
WORLD_VALUE_COUNT_WITHOUT_PLUGIN: int = 2
WORLD_VALUE_COUNT_WITHOUT_HEARING: int = 3
WORLD_PLACE_OF_PLUGIN_VERSION: int = 2
WORLD_PLACE_OF_HEARING: int = 3
HEARD_PART_COUNT: int = 4
# The text the game mod's own lines for the command say carry. The sound of such a line is what the AI itself said,
# so it does not have to hear it again
OWN_LINE_TEXT: str = "..."
GOLD_VALUE_COUNT: int = 2
VALUES_PER_ITEM: int = 4
# The game mod lists at most this many kinds of items of one inventory (MaxInventoryItems in KI_MCP_Gefaehrte_Loop.psc)
MAX_INVENTORY_ITEMS: int = 120
HAND_SEPARATOR: str = ":"
# Gold is an item like any other. It is shown as a number instead of in the lists
GOLD_ITEM_ID: str = "0000000F"
# Form types of the game (as SKSE numbers them) and the heading their items get. Everything else is 'Other'
ITEM_GROUPS: dict[int, str] = {
    41: "Weapons", 42: "Ammunition", 26: "Armor and clothing", 46: "Potions and food", 30: "Ingredients",
    27: "Books", 23: "Scrolls", 52: "Soul gems", 45: "Keys",
}
OTHER_ITEM_GROUP: str = "Other"
QUEST_PART_SEPARATOR: str = "\t"
# A line about a menu that was open (see read_menus): its name and how long in milliseconds
MENU_PART_COUNT: int = 2
MAX_MENU_LINES: int = 20
MAX_MENU_NAME_LENGTH: int = 40
MILLISECONDS_PER_SECOND: float = 1000.0
QUEST_PART_COUNT_BEFORE_OBJECTIVES: int = 3
# The journal lists the objectives of these quests under 'Miscellaneous' and does not show their names
QUEST_TYPE_MISCELLANEOUS: int = 6
TARGET_PART_COUNT: int = 8
# Older plugins do not send the ref ID to walk to
TARGET_PART_COUNT_WITHOUT_REF_ID: int = 7
TARGET_HERE: str = "here"
TARGET_BEHIND_DOOR: str = "door"
TARGET_ELSEWHERE: str = "elsewhere"
TARGET_KINDS: dict[int, str] = {0: TARGET_HERE, 1: TARGET_BEHIND_DOOR, 2: TARGET_ELSEWHERE}
PLACE_PART_COUNT: int = 7
# Older plugins do not send the ref ID of the place
PLACE_PART_COUNT_WITHOUT_REF_ID: int = 6
PLACE_MARKER: str = "marker"
PLACE_DOOR: str = "door"
PLACE_KINDS: dict[int, str] = {0: PLACE_MARKER, 1: PLACE_DOOR}
THING_PART_COUNT: int = 6
THING_FURNITURE: str = "furniture"
THING_CONTAINER: str = "container"
THING_ITEM: str = "item"
THING_KINDS: dict[int, str] = {0: THING_FURNITURE, 1: THING_CONTAINER, 2: THING_ITEM}
THING_TRAP_NUMBER: int = 3
FOUND_CHARACTER: str = "character"
FOUND_KINDS: dict[int, str] = {0: FOUND_CHARACTER, 1: "door", 2: "place", 3: "thing"}
# The folders the game keeps the models of its traps in, by what they are. A folder that is not here goes out as it is
TRAP_NAMES: dict[str, str] = {"tripwire": "tripwire", "pressureplate": "pressure plate", "beartrap": "bear trap",
                              "macetrap": "swinging mace", "skullram": "battering ram"}
SPACE_VALUES_PER_DIRECTION: int = 3
SPACE_WALL: str = "wall"
SPACE_SLOPE: str = "slope"
SPACE_OBSTACLES: dict[int, str] = {0: "", 1: SPACE_WALL, 2: SPACE_SLOPE}
SPACE_NO_DROP: int = -1
FULL_CIRCLE_DEGREES: int = 360
# What the map markers of the game stand for (RE::MARKER_TYPE in CommonLibSSE-NG, file ExtraMapMarker.h).
# From 35 to 52 the game has one marker per hold capital and one per castle in it
MARKER_TYPES: dict[int, str] = {
    1: "city", 2: "town", 3: "settlement", 4: "cave", 5: "camp", 6: "fort", 7: "Nordic ruin", 8: "Dwemer ruin",
    9: "shipwreck", 10: "grove", 11: "landmark", 12: "dragon lair", 13: "farm", 14: "wood mill", 15: "mine",
    16: "Imperial camp", 17: "Stormcloak camp", 18: "standing stone", 19: "wheat mill", 20: "smelter", 21: "stable",
    22: "Imperial tower", 23: "clearing", 24: "pass", 25: "altar", 26: "rock", 27: "lighthouse", 28: "Orc stronghold",
    29: "giant camp", 30: "shack", 31: "Nordic tower", 32: "Nordic dwelling", 33: "docks", 34: "shrine",
    53: "temple", 54: "town", 55: "standing stone", 56: "Telvanni tower", 59: "castle",
}
FIRST_CAPITAL_MARKER_TYPE: int = 35
LAST_CAPITAL_MARKER_TYPE: int = 52
VALUES_PER_NEARBY_CHARACTER: int = 5
TIME_VALUE_COUNT: int = 2
NAME_PLACE_SEPARATOR: str = ":"
UNKNOWN_NAME: str = "unknown"
# Papyrus only knows signed numbers, so ref IDs from 80000000 on arrive as negative numbers
REF_ID_MASK: int = 0xFFFFFFFF
# The four kinds of weather the game tells apart (Weather.GetClassification). The game calls the first one 'pleasant'
WEATHER_NAMES: dict[int, str] = {0: "clear", 1: "cloudy", 2: "rain", 3: "snow", 4: "fog", 5: "dense fog", 6: "snowstorm"}
# The last three only come from the plugin of the game mod (from interface version 32 on): the game marks its fog
# weathers as cloudy and its snowstorms as snow, and the plugin tells them apart by how far the fog of the weather reaches
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
    raw = expand_quick_state(raw)

    state: dict[str, Any] = {}
    if raw.get(KEY_BUILT_QUICK) is True:
        state["quick"] = True
    location = raw.get(KEY_LOCATION)
    if isinstance(location, str):
        state["location"] = location

    player: dict[str, Any] = {}
    player_name = raw.get(KEY_PLAYER_NAME)
    if isinstance(player_name, str):
        player["name"] = player_name
    player_values = _numbers(raw.get(KEY_PLAYER_VALUES))
    if player_values is not None and len(player_values) in (PLAYER_VALUE_COUNT, PLAYER_VALUE_COUNT_WITHOUT_SNEAKING, PLAYER_VALUE_COUNT_WITHOUT_SITTING,
                                                            PLAYER_VALUE_COUNT_WITHOUT_WEAPON):
        health, in_combat, position_x, position_y, position_z, facing, indoors = player_values[:PLAYER_VALUE_COUNT_WITHOUT_WEAPON]
        player["health_percent"] = health
        player["in_combat"] = in_combat != 0
        player["position"] = [position_x, position_y, position_z]
        player["facing_degrees"] = facing % 360
        state["indoors"] = indoors != 0
        if len(player_values) > PLAYER_PLACE_OF_WEAPON:
            player["weapon_drawn"] = player_values[PLAYER_PLACE_OF_WEAPON] != 0
        if len(player_values) > PLAYER_PLACE_OF_SLEEPING:
            player["sitting"] = player_values[PLAYER_PLACE_OF_SITTING] in SIT_STATES_SEATED
            player["sleeping"] = player_values[PLAYER_PLACE_OF_SLEEPING] in SLEEP_STATES_ASLEEP
        if len(player_values) > PLAYER_PLACE_OF_SNEAKING:
            player["sneaking"] = player_values[PLAYER_PLACE_OF_SNEAKING] != 0
    _add_seat(player, raw, KEY_PLAYER_MOUNT, KEY_PLAYER_SEAT)
    if len(player) > 0:
        state["player"] = player

    world_values = _numbers(raw.get(KEY_WORLD_VALUES))
    if world_values is not None and WORLD_VALUE_COUNT_WITHOUT_PLUGIN <= len(world_values) <= WORLD_VALUE_COUNT:
        weather, minutes = world_values[:WORLD_VALUE_COUNT_WITHOUT_PLUGIN]
        if weather in WEATHER_NAMES:
            state["weather"] = WEATHER_NAMES[weather]
        if 0 <= minutes < MINUTES_PER_DAY:
            state["time"] = f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"
        # Without the SKSE plugin the game cannot tell the quests. Then there is no 'quests' in the result,
        # so that 'no open quests' (an empty list) can be told apart from 'not known'
        if len(world_values) > WORLD_PLACE_OF_PLUGIN_VERSION and world_values[WORLD_PLACE_OF_PLUGIN_VERSION] > 0:
            state["quests"] = _read_quests(raw.get(KEY_QUESTS), raw.get(KEY_QUEST_TARGETS))
            state["plugin_version"] = world_values[WORLD_PLACE_OF_PLUGIN_VERSION]
        # Same idea: 'hearing' is only in the result if the game mod knows about listening at all
        state["protocol"] = world_values[WORLD_PLACE_OF_PROTOCOL] if len(world_values) > WORLD_PLACE_OF_PROTOCOL else 0
        # From whom the lists below are seen: distances and directions are measured from the companion then
        state["around_companion"] = len(world_values) > WORLD_PLACE_OF_AROUND and world_values[WORLD_PLACE_OF_AROUND] != 0
        # Whether the game only listed the characters the viewer can see
        state["visible_only"] = len(world_values) > WORLD_PLACE_OF_VISIBLE and world_values[WORLD_PLACE_OF_VISIBLE] != 0
        if len(world_values) > WORLD_PLACE_OF_HEARING:
            state["hearing"] = world_values[WORLD_PLACE_OF_HEARING] != 0
            if state["hearing"]:
                state["heard"] = _read_heard(raw.get(KEY_HEARD))

    companion = _read_companion(raw)
    if companion is not None:
        _add_seat(companion, raw, KEY_COMPANION_MOUNT, KEY_COMPANION_SEAT)
        state["companion"] = companion
        horse = _read_horse(raw)
        if horse is not None:
            state["horse"] = horse
    player_horse = _read_player_horse(raw)
    if player_horse is not None:
        state["player_horse"] = player_horse
    # Which of the two horses runs to the player at the moment
    called_form_id = raw.get(KEY_HORSE_CALLED)
    if isinstance(called_form_id, int) and not isinstance(called_form_id, bool) and called_form_id != 0:
        for known_horse in (state.get("horse"), state.get("player_horse")):
            if known_horse is not None and known_horse["ref_id"] == _ref_id(called_form_id):
                known_horse["called"] = True

    gold_values = _numbers(raw.get(KEY_GOLD))
    if gold_values is not None and len(gold_values) == GOLD_VALUE_COUNT:
        player["gold"] = gold_values[0]
        state["player"] = player
        if companion is not None:
            companion["gold"] = gold_values[1]
    hands = raw.get(KEY_HANDS)
    if companion is not None and isinstance(hands, list):
        companion["holds"] = [name for name in (_hand_name(hand) for hand in hands) if name != ""]
    for key in (KEY_INVENTORY_OWN, KEY_INVENTORY_PLAYER):
        inventory = _read_inventory(raw, key)
        if inventory is not None:
            state[key] = inventory
    spells = _read_spells(raw)
    if spells is not None:
        state["spells"] = spells
    corpse_values = _numbers(raw.get(KEY_CORPSE))
    # On a body and in a chest the gold is something to take, so it stays in the list
    corpse_inventory = _read_inventory(raw, KEY_INVENTORY_CORPSE, is_gold_listed=True)
    if corpse_values is not None and len(corpse_values) in (CORPSE_VALUE_COUNT, CORPSE_VALUE_COUNT_WITHOUT_KIND):
        is_container = len(corpse_values) == CORPSE_VALUE_COUNT and corpse_values[1] != 0
        is_locked = len(corpse_values) == CORPSE_VALUE_COUNT and corpse_values[2] != 0
        if corpse_inventory is not None or is_locked:
            corpse_name = raw.get(KEY_CORPSE_NAME)
            state["corpse"] = {
                "ref_id": _ref_id(corpse_values[0]),
                "name": _tidy_name(corpse_name) if isinstance(corpse_name, str) else UNKNOWN_NAME,
                "container": is_container,
                "locked": is_locked,
            }
            if corpse_inventory is not None:
                state[KEY_INVENTORY_CORPSE] = corpse_inventory
    merchant = _read_merchant(raw.get(KEY_MERCHANT_LINES))
    if merchant is not None:
        state["merchant"] = merchant
    report = raw.get(KEY_REPORT)
    if isinstance(report, str) and report.strip() != "":
        state["report"] = report.strip()
    # Only a game mod whose plugin keeps events sends this list, and only if something happened
    if isinstance(raw.get(KEY_EVENTS), list):
        state["events"] = read_events(raw.get(KEY_EVENTS))
    # Only when the server asked to read a book. Without 'book' the ID that was asked for is no book
    book_for = raw.get(KEY_BOOK_FOR)
    if isinstance(book_for, str) and book_for.strip() != "":
        state["book_for"] = book_for.strip()
        book = _read_book(raw.get(KEY_BOOK))
        if book is not None:
            state["book"] = book
    _add_stats(state, raw)

    if isinstance(raw.get(KEY_NEARBY_LINES), list):
        state["nearby"] = _read_nearby_lines(raw.get(KEY_NEARBY_LINES))
    else:
        state["nearby"] = _read_nearby(raw)
    # Only a game mod whose plugin knows about places sends this list. Without it nothing is said about places
    if isinstance(raw.get(KEY_PLACES), list):
        state["places"] = _read_places(raw.get(KEY_PLACES))
    # The same goes for the things close to the player
    if isinstance(raw.get(KEY_THINGS), list):
        state["things"] = _read_things(raw.get(KEY_THINGS))
    # Only a game mod whose plugin knows about traps sends this list, and only if there are any
    if isinstance(raw.get(KEY_TRAPS), list):
        state["traps"] = _read_traps(raw.get(KEY_TRAPS))
    # Only a game mod whose plugin can search by name sends back what it looked for
    found_for = raw.get(KEY_FOUND_FOR)
    if isinstance(found_for, str) and found_for.strip() != "":
        state["found_for"] = found_for.strip()
        state["found"] = _read_found(raw.get(KEY_FOUND))
    space_look = _read_space_look(raw.get(KEY_SPACE_INFO))
    space = _read_space(raw.get(KEY_SPACE), space_look)
    if len(space) > 0:
        state["space"] = space
        if space_look is not None:
            state["space_look"] = space_look
    sight = read_sight(raw.get(KEY_SIGHT))
    if sight is not None:
        state["sight"] = sight
    view = read_view(raw.get(KEY_VIEW), raw.get(KEY_VIEW_NAMES))
    if view is not None:
        state["view"] = view
    _add_heights(state, read_heights(raw.get(KEY_HEIGHTS)))
    _add_view_of_companion(state)

    mismatch = raw.get(KEY_PLUGIN_MISMATCH)
    if isinstance(mismatch, str) and mismatch.strip() != "":
        state["plugin_mismatch"] = mismatch.strip()
    times = _numbers(raw.get(KEY_TIMES))
    if times is not None and len(times) == TIME_VALUE_COUNT:
        state["build_ms"], state["scan_ms"] = times
        if state.get("quick") is True and int(state.get("plugin_version") or 0) >= PLUGIN_VERSION_WITH_SENSES:
            # The second time is the plugin's own, in finer steps
            state["scan_ms"] = times[1] / QUICK_TIME_STEPS_PER_MILLISECOND
    return state


def _read_companion(raw: dict[str, Any]) -> dict[str, Any] | None:
    """No companion in the result means: the player has no follower at the moment"""
    values = _numbers(raw.get(KEY_COMPANION_VALUES))
    if values is None or len(values) not in (COMPANION_VALUE_COUNT, COMPANION_VALUE_COUNT_WITHOUT_HEALING,
                                             COMPANION_VALUE_COUNT_WITHOUT_SNEAKING, COMPANION_VALUE_COUNT_WITHOUT_RUNNING,
                                             COMPANION_VALUE_COUNT_WITHOUT_KEPT_WEAPON, COMPANION_VALUE_COUNT_WITHOUT_LEADER,
                                             COMPANION_VALUE_COUNT_WITHOUT_WEAPON, COMPANION_VALUE_COUNT_WITHOUT_SITTING):
        return None
    (form_id, health, dead, in_combat, waiting, is_loaded, distance, angle, target_form_id, facing, retreating,
     walk_target_form_id) = values[:COMPANION_VALUE_COUNT_WITHOUT_SITTING]

    companion: dict[str, Any] = {}
    name = raw.get(KEY_COMPANION_NAME)
    companion["name"] = _tidy_name(name) if isinstance(name, str) else UNKNOWN_NAME
    companion["ref_id"] = _ref_id(form_id)
    voice = raw.get(KEY_COMPANION_VOICE)
    if isinstance(voice, str) and voice.strip() != "":
        companion["voice"] = voice.strip()
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
    if len(values) > COMPANION_PLACE_OF_SITTING:
        companion["sitting"] = values[COMPANION_PLACE_OF_SITTING] in SIT_STATES_SEATED
    if len(values) > COMPANION_PLACE_OF_LOOK_TARGET:
        companion["weapon_drawn"] = values[COMPANION_PLACE_OF_WEAPON] != 0
        if values[COMPANION_PLACE_OF_LOOK_TARGET] != 0:
            companion["look_target_ref_id"] = _ref_id(values[COMPANION_PLACE_OF_LOOK_TARGET])
    if len(values) > COMPANION_PLACE_OF_LEADER and values[COMPANION_PLACE_OF_LEADER] != 0:
        companion["leader_ref_id"] = _ref_id(values[COMPANION_PLACE_OF_LEADER])
    if len(values) > COMPANION_PLACE_OF_KEPT_WEAPON:
        companion["weapon_kept_away"] = values[COMPANION_PLACE_OF_KEPT_WEAPON] != 0
    if len(values) > COMPANION_PLACE_OF_RUNNING:
        companion["running"] = values[COMPANION_PLACE_OF_RUNNING] != 0
    if len(values) > COMPANION_PLACE_OF_SNEAK_MODE:
        companion["sneaking"] = values[COMPANION_PLACE_OF_SNEAKING] != 0
        companion["sneak_mode"] = SNEAK_MODES.get(values[COMPANION_PLACE_OF_SNEAK_MODE], SNEAK_MODES[0])
    if len(values) > COMPANION_PLACE_OF_LEADING:
        companion["auto_heal_percent"] = values[COMPANION_PLACE_OF_AUTO_HEAL]
        if values[COMPANION_PLACE_OF_LEADING] in LEAD_STATES:
            companion["leading"] = LEAD_STATES[values[COMPANION_PLACE_OF_LEADING]]
    if raw.get(KEY_COMPANION_PASSIVE) == 1:
        companion["passive"] = True
    if raw.get(KEY_COMPANION_HOLDING) == 1:
        companion["holding_position"] = True
    if raw.get(KEY_COMPANION_COVERING) == 1:
        companion["in_cover"] = True
    for key, name in ((KEY_COMPANION_PLACE, "place"), (KEY_COMPANION_EXPRESSION, "expression")):
        text = raw.get(key)
        if isinstance(text, str) and text.strip() != "":
            companion[name] = text.strip()
    return companion


def _add_stats(state: dict[str, Any], raw: dict[str, Any]) -> None:
    """Adds 'player_stats' and 'companion_stats' if the game sent them (it only does when the server asked for them)"""
    values = _numbers(raw.get(KEY_STATS_PLAYER))
    if values is not None and len(values) == PLAYER_STATS_BEFORE_SKILLS + len(PLAYER_SKILLS):
        (level, health, magicka, stamina, magicka_percent, stamina_percent, carried, carry_capacity, perk_points, experience,
         experience_needed) = values[:PLAYER_STATS_BEFORE_SKILLS]
        state["player_stats"] = {
            "level": level, "health": health, "magicka": magicka, "stamina": stamina, "magicka_percent": magicka_percent,
            "stamina_percent": stamina_percent, "carried": carried, "carry_capacity": carry_capacity, "perk_points": perk_points,
            # The game raises the level when the player opens the skills menu. Until then the experience stays above what it takes
            "level_up_waiting": 0 < experience_needed <= experience,
            "skills": [{"name": name, "level": level_of_skill}
                       for name, level_of_skill in zip(PLAYER_SKILLS, values[PLAYER_STATS_BEFORE_SKILLS:])],
        }
    values = _numbers(raw.get(KEY_STATS_COMPANION))
    if values is not None and len(values) == COMPANION_STATS_COUNT:
        level, health, magicka, stamina, magicka_percent, stamina_percent = values
        state["companion_stats"] = {"level": level, "health": health, "magicka": magicka, "stamina": stamina,
                                    "magicka_percent": magicka_percent, "stamina_percent": stamina_percent}


def _read_nearby_lines(raw_lines: list) -> list[dict[str, Any]]:
    """The characters in a building the game has not loaded, as its SKSE plugin lists them (see KEY_NEARBY_LINES).
    Whether one of them is hostile cannot be told there"""
    nearby: list[dict[str, Any]] = []
    for entry in raw_lines:
        if not isinstance(entry, str):
            continue
        # The name comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, NEARBY_LINE_PART_COUNT - 1)
        if len(parts) != NEARBY_LINE_PART_COUNT:
            continue
        form_id = parse_ref_id(parts[0])
        distance, angle, dead = (_whole_number(text) for text in parts[1:4])
        if None in (form_id, distance, angle, dead) or parts[4].strip() == "":
            continue
        nearby.append({"name": _tidy_name(parts[4]), "ref_id": format_ref_id(form_id), "distance_meters": distance,
                       "angle_degrees": angle, "hostile": False, "dead": dead != 0})
    nearby.sort(key=lambda character: (character["dead"], character["distance_meters"]))
    return nearby


def _read_quests(raw_quests: Any, raw_targets: Any = None) -> list[dict[str, Any]]:
    """The open quests in the order the game sent them (tracked ones first)

    Args:
        raw_targets: Where the targets of the tracked quests are (KEY_QUEST_TARGETS). A quest with targets gets 'targets'
    """
    quests: list[dict[str, Any]] = []
    if not isinstance(raw_quests, list):
        return quests
    # The targets name their quest by its place in the list of the game, and a broken line is left out here
    quests_by_place: dict[int, dict[str, Any]] = {}
    for place, entry in enumerate(raw_quests):
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR)
        if len(parts) < QUEST_PART_COUNT_BEFORE_OBJECTIVES:
            continue
        tracked_text, type_text, name = parts[:QUEST_PART_COUNT_BEFORE_OBJECTIVES]
        objectives = [text.strip() for text in parts[QUEST_PART_COUNT_BEFORE_OBJECTIVES:] if text.strip() != ""]
        if len(objectives) == 0:
            continue
        quest_type = int(type_text) if type_text.isdecimal() else 0
        quests.append({
            "name": name.strip(),
            "tracked": tracked_text == "1",
            "type": quest_type,
            # A quest without a name cannot be shown under its name either
            "miscellaneous": quest_type == QUEST_TYPE_MISCELLANEOUS or name.strip() == "",
            "objectives": objectives,
        })
        quests_by_place[place] = quests[-1]
    _add_quest_targets(quests_by_place, raw_targets)
    return quests


def _add_quest_targets(quests_by_place: dict[int, dict[str, Any]], raw_targets: Any) -> None:
    """Gives every quest its targets as 'targets': each with 'objective' (place of its objective in 'objectives'), 'kind',
    'name', and depending on the kind 'distance_meters' and 'angle_degrees' (of the target or of the door to it), 'place'
    and 'ref_id' (of the target or of the door to it, to walk there)"""
    if not isinstance(raw_targets, list):
        return
    for entry in raw_targets:
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR)
        if len(parts) not in (TARGET_PART_COUNT, TARGET_PART_COUNT_WITHOUT_REF_ID):
            continue
        numbers = [_whole_number(text) for text in parts[:5]]
        if None in numbers:
            continue
        quest_place, objective_place, kind_number, distance, angle = numbers
        quest = quests_by_place.get(quest_place)
        if quest is None or kind_number not in TARGET_KINDS or not 0 <= objective_place < len(quest["objectives"]):
            continue
        target: dict[str, Any] = {"objective": objective_place, "kind": TARGET_KINDS[kind_number], "name": parts[5].strip()}
        if target["kind"] != TARGET_ELSEWHERE:
            target["distance_meters"] = distance
            target["angle_degrees"] = angle
        if target["kind"] != TARGET_HERE:
            target["place"] = parts[6].strip()
        if len(parts) == TARGET_PART_COUNT and target["kind"] != TARGET_ELSEWHERE:
            form_id = parse_ref_id(parts[7])
            if form_id is not None and form_id != 0:
                target["ref_id"] = format_ref_id(form_id)
        quest.setdefault("targets", []).append(target)


def _read_places(raw_places: Any) -> list[dict[str, Any]]:
    """The places around the viewer in the order the game sent them (map markers first, then doors, each nearest first)"""
    places: list[dict[str, Any]] = []
    if not isinstance(raw_places, list):
        return places
    for entry in raw_places:
        if not isinstance(entry, str):
            continue
        # The name comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, PLACE_PART_COUNT - 1)
        form_id: int | None = None
        if len(parts) == PLACE_PART_COUNT:
            form_id = parse_ref_id(parts[1])
            if form_id is None:
                continue
            del parts[1]
        if len(parts) != PLACE_PART_COUNT_WITHOUT_REF_ID:
            continue
        numbers = [_whole_number(text) for text in parts[:5]]
        name = parts[5].strip()
        if None in numbers or name == "":
            continue
        kind_number, distance, angle, marker_type, known = numbers
        if kind_number not in PLACE_KINDS:
            continue
        place: dict[str, Any] = {"kind": PLACE_KINDS[kind_number], "name": name, "distance_meters": distance, "angle_degrees": angle}
        if form_id is not None and form_id != 0:
            place["ref_id"] = format_ref_id(form_id)
        if place["kind"] == PLACE_MARKER:
            marker_name = _marker_type_name(marker_type)
            if marker_name != "":
                place["type"] = marker_name
            place["known"] = known != 0
        places.append(place)
    return places


def _read_things(raw_things: Any) -> list[dict[str, Any]]:
    """The things close to the viewer in the order the game sent them (furniture, containers, items, each nearest first)"""
    things: list[dict[str, Any]] = []
    if not isinstance(raw_things, list):
        return things
    for entry in raw_things:
        if not isinstance(entry, str):
            continue
        # The name comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, THING_PART_COUNT - 1)
        if len(parts) != THING_PART_COUNT:
            continue
        kind_number, distance, angle, flag = (_whole_number(text) for text in (parts[0], parts[2], parts[3], parts[4]))
        form_id = parse_ref_id(parts[1])
        name = parts[5].strip()
        if None in (kind_number, distance, angle, flag, form_id) or name == "" or kind_number not in THING_KINDS:
            continue
        thing: dict[str, Any] = {"kind": THING_KINDS[kind_number], "ref_id": format_ref_id(form_id), "name": _tidy_name(name),
                                 "distance_meters": distance, "angle_degrees": angle}
        if thing["kind"] == THING_FURNITURE:
            # 1 = a character can sit on it, 2 = it can sleep on it (a newer plugin of the game mod tells that)
            thing["seat"] = flag == FURNITURE_IS_SEAT
            if flag == FURNITURE_IS_BED:
                thing["bed"] = True
        elif thing["kind"] == THING_CONTAINER:
            # 1 = locked. A newer plugin of the game mod also tells how many different items an open one holds:
            # 2 x (that number + 1), so 2 = empty. 0 = it does not tell
            thing["locked"] = flag % 2 == 1
            if flag >= CONTAINER_COUNT_STEP and not thing["locked"]:
                thing["item_count"] = flag // CONTAINER_COUNT_STEP - 1
        things.append(thing)
    return things


def _read_traps(raw_traps: list) -> list[dict[str, Any]]:
    """The traps close to the viewer in the order the game sent them (nearest first)"""
    traps: list[dict[str, Any]] = []
    for entry in raw_traps:
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR, THING_PART_COUNT - 1)
        if len(parts) != THING_PART_COUNT:
            continue
        kind_number, distance, angle = (_whole_number(text) for text in (parts[0], parts[2], parts[3]))
        form_id = parse_ref_id(parts[1])
        name = parts[5].strip().lower()
        if None in (distance, angle, form_id) or name == "" or kind_number != THING_TRAP_NUMBER:
            continue
        traps.append({"ref_id": format_ref_id(form_id), "name": TRAP_NAMES.get(name, name),
                      "distance_meters": distance, "angle_degrees": angle})
    return traps


def _read_found(raw_found: Any) -> list[dict[str, Any]]:
    """What a search by name found, in the order the game sent it (nearest first)"""
    found: list[dict[str, Any]] = []
    if not isinstance(raw_found, list):
        return found
    for entry in raw_found:
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR, THING_PART_COUNT - 1)
        if len(parts) != THING_PART_COUNT:
            continue
        kind_number, distance, angle, flag = (_whole_number(text) for text in (parts[0], parts[2], parts[3], parts[4]))
        form_id = parse_ref_id(parts[1])
        name = parts[5].strip()
        if None in (distance, angle, flag, form_id) or name == "" or kind_number not in FOUND_KINDS:
            continue
        match: dict[str, Any] = {"kind": FOUND_KINDS[kind_number], "ref_id": format_ref_id(form_id), "name": _tidy_name(name),
                                 "distance_meters": distance, "angle_degrees": angle}
        if match["kind"] == FOUND_CHARACTER:
            match["dead"] = flag != 0
        found.append(match)
    return found


def _add_seat(character: dict[str, Any], raw: dict[str, Any], mount_key: str, seat_key: str) -> None:
    """Adds on what a character sits: 'riding' with the name of the horse under 'mount' (left out if the game mod could
    not tell which horse), or the name of the piece of furniture under 'seat'"""
    mount = raw.get(mount_key)
    if isinstance(mount, str) and mount.strip() != "":
        character["riding"] = True
        if mount.strip() != UNKNOWN_MOUNT_NAME:
            character["mount"] = _tidy_name(mount)
        return
    seat = raw.get(seat_key)
    if isinstance(seat, str) and seat.strip() != "":
        character["seat"] = _tidy_name(seat)


def _read_horse(raw: dict[str, Any]) -> dict[str, Any] | None:
    """The horse of the companion, or None if it has none (or the game mod does not know riding)"""
    values = _numbers(raw.get(KEY_HORSE))
    if values is None or len(values) != HORSE_VALUE_COUNT:
        return None
    form_id, distance, angle, ride_wanted, ride_locked = values
    horse: dict[str, Any] = {"ref_id": _ref_id(form_id), "ride_wanted": ride_wanted != 0, "ride_locked": ride_locked != 0}
    name = raw.get(KEY_HORSE_NAME)
    if isinstance(name, str) and name.strip() != "":
        horse["name"] = _tidy_name(name)
    if distance != HORSE_NOT_LOADED:
        horse["distance_meters"] = distance
        horse["angle_degrees"] = angle
    return horse


def _read_player_horse(raw: dict[str, Any]) -> dict[str, Any] | None:
    """The horse the player rode last, or None if there is none (or the game mod does not know it)"""
    values = _numbers(raw.get(KEY_PLAYER_HORSE))
    if values is None or len(values) != PLAYER_HORSE_VALUE_COUNT:
        return None
    form_id, distance, angle = values
    horse: dict[str, Any] = {"ref_id": _ref_id(form_id)}
    name = raw.get(KEY_PLAYER_HORSE_NAME)
    if isinstance(name, str) and name.strip() != "":
        horse["name"] = _tidy_name(name)
    if distance != HORSE_NOT_LOADED:
        horse["distance_meters"] = distance
        horse["angle_degrees"] = angle
    return horse


def _read_spells(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The spells the companion knows, or None if the game mod sent none (it only does with the own inventory)"""
    values = _numbers(raw.get(KEY_SPELLS))
    if values is None or len(values) == 0 or len(values) != 1 + values[0] * VALUES_PER_SPELL:
        return None
    names = _names_by_place(raw.get(KEY_SPELLS + NAMES_SUFFIX))
    spells: list[dict[str, Any]] = []
    for place in range(values[0]):
        form_id, magicka = values[1 + place * VALUES_PER_SPELL:1 + (place + 1) * VALUES_PER_SPELL]
        name = names.get(place, "")
        if name != "":
            spells.append({"id": format_ref_id(form_id & 0xFFFFFFFF), "name": _tidy_name(name), "magicka": magicka})
    return spells


def read_sight(raw_sight: Any) -> dict[str, Any] | None:
    """How far the companion sees (see KEY_SIGHT), or None if the game sent nothing that can be read"""
    values = _numbers(raw_sight)
    if values is None or len(values) < SIGHT_VALUE_COUNT or values[0] < 1:
        return None
    meters, weather, time_percent, is_indoors, light, is_torch, light_level, minutes = values[:SIGHT_VALUE_COUNT]
    sight: dict[str, Any] = {"meters": meters, "indoors": is_indoors != 0, "time_percent": time_percent, "torch": is_torch != 0,
                             "minutes": minutes}
    if weather in WEATHER_NAMES:
        sight["weather"] = WEATHER_NAMES[weather]
    if light in SIGHT_LIGHTS:
        sight["light"] = SIGHT_LIGHTS[light]
        sight["light_level"] = light_level
    return sight


def read_view(raw_view: Any, raw_names: Any) -> dict[str, Any] | None:
    """The picture in front of the companion (see KEY_VIEW), or None if the game sent none or one this server cannot read"""
    values = _numbers(raw_view)
    if values is None or len(values) < VIEW_HEAD_COUNT or values[0] != VIEW_FORMAT:
        return None
    _, columns, rows, degrees, range_meters, legend_count, per_legend, per_cell = values[:VIEW_HEAD_COUNT]
    if columns < 1 or rows < 1 or legend_count < 0 or per_legend < 4 or per_cell < 7:
        return None
    cells_start = VIEW_HEAD_COUNT + legend_count * per_legend
    if len(values) < cells_start + columns * rows * per_cell:
        return None
    names = raw_names if isinstance(raw_names, list) else []
    # Only what stands in a cell is in the legend: a cell keeps two entries, so the plugin may name more than the picture shows
    shown: set[int] = set()
    for cell_number in range(columns * rows):
        start = cells_start + cell_number * per_cell
        shown.update((values[start + 3], values[start + 5]))
    legend: list[dict[str, Any]] = []
    labels: dict[int, str] = {}
    creature_count = 0
    thing_count = 0
    for number in range(legend_count):
        if number + 1 not in shown:
            continue
        kind, high, low, flags = values[VIEW_HEAD_COUNT + number * per_legend: VIEW_HEAD_COUNT + number * per_legend + 4]
        name = str(names[number]).strip() if number < len(names) else ""
        entry: dict[str, Any] = {"ref_id": format_ref_id(((high & 0xFFFF) << 16) | (low & 0xFFFF)), "name": name or "unknown"}
        if kind == VIEW_IS_CREATURE:
            creature_count += 1
            entry.update({"label": f"C{creature_count}", "is_creature": True, "hostile": flags == VIEW_CREATURE_HOSTILE,
                          "dead": flags == VIEW_CREATURE_DEAD, "player": flags == VIEW_CREATURE_PLAYER})
        else:
            thing_count += 1
            entry.update({"label": f"O{thing_count}", "is_creature": False, "kind": VIEW_THING_KINDS.get(flags, "thing")})
        legend.append(entry)
        labels[number + 1] = entry["label"]
    grid: list[list[dict[str, Any]]] = []
    for row in range(rows):
        cells: list[dict[str, Any]] = []
        for column in range(columns):
            start = cells_start + (row * columns + column) * per_cell
            terrain, meters, is_drop, first, first_meters, second, second_meters = values[start: start + 7]
            cell: dict[str, Any] = {"drop": is_drop != 0, "entries": []}
            if terrain in VIEW_TERRAINS:
                cell["terrain"] = VIEW_TERRAINS[terrain]
                cell["meters"] = meters
                if terrain in VIEW_WATER_WAYS:
                    cell["water_by"] = VIEW_WATER_WAYS[terrain]
            for place, entry_meters in ((first, first_meters), (second, second_meters)):
                if place in labels:
                    cell["entries"].append({"label": labels[place], "meters": entry_meters})
            # The plugin puts characters first so that things do not push them out. Shown is the nearest first
            cell["entries"].sort(key=lambda entry: entry["meters"])
            cells.append(cell)
        grid.append(cells)
    return {"columns": columns, "rows": rows, "degrees": degrees, "range_meters": range_meters, "legend": legend, "cells": grid}


def _read_space_look(raw_info: Any) -> dict[str, int] | None:
    """How the rays looked, or None if the game mod did not say (its plugin is older than interface version 13)"""
    values = _numbers(raw_info)
    if values is None or len(values) != SPACE_INFO_COUNT:
        return None
    heading, angle, meters, facing = values
    return {"heading_degrees": heading % FULL_CIRCLE_DEGREES, "angle_degrees": min(max(angle, 0), SPACE_FULL_CIRCLE),
            "meters": meters, "facing_degrees": facing % FULL_CIRCLE_DEGREES}


def _read_space(raw_space: Any, look: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """What the rays found around the companion, one entry per ray. Without 'look' the rays go all around, clockwise from
    straight ahead. With it they went as it says, and every entry also carries its point of the compass ('compass_degrees')
    and how far it is turned away from the middle of the look ('offset_degrees', negative = left).
    An empty list if the game sent nothing that can be read"""
    values = _numbers(raw_space)
    if values is None or len(values) == 0 or len(values) % SPACE_VALUES_PER_DIRECTION != 0:
        return []
    direction_count = len(values) // SPACE_VALUES_PER_DIRECTION
    space: list[dict[str, Any]] = []
    for place in range(direction_count):
        obstacle, meters, drop_meters = values[place * SPACE_VALUES_PER_DIRECTION:(place + 1) * SPACE_VALUES_PER_DIRECTION]
        direction: dict[str, Any] = {"angle_degrees": _signed_angle(place * FULL_CIRCLE_DEGREES / direction_count),
                                     "obstacle": SPACE_OBSTACLES.get(obstacle, "")}
        if look is not None:
            if look["angle_degrees"] >= SPACE_FULL_CIRCLE:
                offset = place * FULL_CIRCLE_DEGREES / direction_count
            elif direction_count == 1:
                offset = 0.0
            else:
                offset = place * look["angle_degrees"] / (direction_count - 1) - look["angle_degrees"] / 2
            direction["offset_degrees"] = _signed_angle(offset)
            direction["compass_degrees"] = round(look["heading_degrees"] + offset) % FULL_CIRCLE_DEGREES
            # Seen from where the companion faces, like every other direction 'from you'
            direction["angle_degrees"] = _signed_angle(look["heading_degrees"] + offset - look["facing_degrees"])
        if direction["obstacle"] != "":
            direction["obstacle_meters"] = meters
        if drop_meters != SPACE_NO_DROP:
            direction["drop_meters"] = drop_meters
        space.append(direction)
    return space


def _marker_type_name(marker_type: int) -> str:
    """What kind of place a map marker stands for, or an empty text if that says nothing"""
    if FIRST_CAPITAL_MARKER_TYPE <= marker_type <= LAST_CAPITAL_MARKER_TYPE:
        # The game numbers them in pairs: first the castle of a hold capital, then the capital itself
        return "castle" if (marker_type - FIRST_CAPITAL_MARKER_TYPE) % 2 == 0 else "city"
    return MARKER_TYPES.get(marker_type, "")


def _whole_number(text: str) -> int | None:
    """Returns the number in the text (digits with an optional minus sign in front), or None"""
    cleaned = text.strip()
    digits = cleaned[1:] if cleaned.startswith("-") else cleaned
    if not digits.isdecimal():
        return None
    return int(cleaned)


def _hand_name(hand: Any) -> str:
    if not isinstance(hand, str):
        return ""
    return hand.partition(HAND_SEPARATOR)[2].strip()


# The fourth number of an item is put together from these: the owner wears or holds it, the player has it among the favorites
ITEM_FLAG_EQUIPPED: int = 1
ITEM_FLAG_FAVORITE: int = 2
# Above the flags the fourth number of an item carries what kind of weapon or armor it is, in steps of this
# (0 = the game mod does not tell, or it is neither)
ITEM_KIND_STEP: int = 16
ITEM_KINDS: dict[int, str] = {
    1: "fists", 2: "sword", 3: "dagger", 4: "war axe", 5: "mace", 6: "greatsword", 7: "battleaxe or warhammer", 8: "bow",
    9: "staff", 10: "crossbow", 20: "shield", 21: "body armor", 22: "helmet", 23: "gauntlets", 24: "boots", 25: "amulet",
    26: "ring", 27: "armor",
}
# What an item is called in a short list if the game mod did not tell its kind
ITEM_KINDS_BY_GROUP: dict[str, str] = {
    "Weapons": "weapon", "Ammunition": "ammunition", "Armor and clothing": "armor", "Potions and food": "potion or food",
    "Ingredients": "ingredient", "Books": "book", "Scrolls": "scroll", "Soul gems": "soul gem", "Keys": "key",
}


def _read_inventory(raw: dict[str, Any], key: str, is_gold_listed: bool = False) -> dict[str, Any] | None:
    """Returns 'total' (kinds of items the character has) and 'items' (the ones the game listed), or None

    Args:
        is_gold_listed: Whether gold is one of the items. Without it, gold is left out (the state carries it as a number)
    """
    lines = raw.get(key + LINES_SUFFIX)
    if isinstance(lines, list):
        return _read_inventory_lines(lines, is_gold_listed)
    values = _numbers(raw.get(key))
    if values is None or len(values) == 0 or (len(values) - 1) % VALUES_PER_ITEM != 0:
        return None
    names = _names_by_place(raw.get(key + NAMES_SUFFIX))
    items: list[dict[str, Any]] = []
    for place in range((len(values) - 1) // VALUES_PER_ITEM):
        start = 1 + place * VALUES_PER_ITEM
        form_id, count, form_type, flags = values[start:start + VALUES_PER_ITEM]
        item_id = _ref_id(form_id)
        if form_id == 0 or place not in names or (item_id == GOLD_ITEM_ID and not is_gold_listed):
            continue
        items.append({
            "id": item_id,
            "name": names[place],
            "count": count,
            "group": ITEM_GROUPS.get(form_type, OTHER_ITEM_GROUP),
            "equipped": flags & ITEM_FLAG_EQUIPPED != 0,
            "favorite": flags & ITEM_FLAG_FAVORITE != 0,
            "kind": ITEM_KINDS.get(flags // ITEM_KIND_STEP, ""),
        })
    return {"total": values[0], "items": items}


def _read_inventory_lines(raw_lines: list, is_gold_listed: bool) -> dict[str, Any] | None:
    """The same for an inventory the plugin of the game mod listed (see LINES_SUFFIX). None if the first text is no number"""
    if len(raw_lines) == 0 or not isinstance(raw_lines[0], str) or not raw_lines[0].strip().isdecimal():
        return None
    items: list[dict[str, Any]] = []
    for entry in raw_lines[1:]:
        if not isinstance(entry, str):
            continue
        # The name comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, INVENTORY_LINE_PART_COUNT - 1)
        if len(parts) != INVENTORY_LINE_PART_COUNT:
            continue
        id_text, count_text, type_text, flags_text, name = parts
        form_id = parse_ref_id(id_text)
        if form_id is None or form_id == 0 or not count_text.isdecimal() or not type_text.isdecimal() or not flags_text.isdecimal():
            continue
        item_id = format_ref_id(form_id)
        if name.strip() == "" or (item_id == GOLD_ITEM_ID and not is_gold_listed):
            continue
        flags = int(flags_text)
        items.append({
            "id": item_id,
            "name": _tidy_name(name),
            "count": int(count_text),
            "group": ITEM_GROUPS.get(int(type_text), OTHER_ITEM_GROUP),
            "equipped": flags & ITEM_FLAG_EQUIPPED != 0,
            "favorite": flags & ITEM_FLAG_FAVORITE != 0,
            "kind": ITEM_KINDS.get(flags // ITEM_KIND_STEP, ""),
        })
    return {"total": int(raw_lines[0].strip()), "items": items}


def _read_merchant(raw_lines: Any) -> dict[str, Any] | None:
    """What a merchant offers and what the companion could sell to it (see KEY_MERCHANT_LINES). None if the game sent none"""
    if not isinstance(raw_lines, list) or len(raw_lines) == 0 or not isinstance(raw_lines[0], str):
        return None
    # The name comes last and is not split any further
    head = raw_lines[0].split(QUEST_PART_SEPARATOR, MERCHANT_HEAD_PART_COUNT - 1)
    if len(head) != MERCHANT_HEAD_PART_COUNT:
        return None
    form_id = parse_ref_id(head[0])
    if form_id is None or not all(text.isdecimal() for text in head[1:-1]):
        return None
    status, gold, own_gold, start_hour, end_hour, ware_total, sell_total = (int(text) for text in head[1:-1])
    merchant: dict[str, Any] = {
        "ref_id": format_ref_id(form_id),
        "name": _tidy_name(head[-1]) if head[-1].strip() != "" else UNKNOWN_NAME,
        "status": MERCHANT_STATUSES.get(status, MERCHANT_IS_NONE),
        "gold": gold,
        "own_gold": own_gold,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "ware_total": ware_total,
        "sell_total": sell_total,
        "wares": [],
        "sellable": [],
    }
    for entry in raw_lines[1:]:
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR, MERCHANT_ITEM_PART_COUNT - 1)
        if len(parts) != MERCHANT_ITEM_PART_COUNT:
            continue
        side_text, id_text, count_text, type_text, price_text, name = parts
        item_form_id = parse_ref_id(id_text)
        if item_form_id is None or item_form_id == 0 or name.strip() == "" or not all(text.isdecimal() for text in (side_text, count_text, type_text, price_text)):
            continue
        item = {
            "id": format_ref_id(item_form_id),
            "name": _tidy_name(name),
            "count": int(count_text),
            "group": ITEM_GROUPS.get(int(type_text), OTHER_ITEM_GROUP),
            "price": int(price_text),
        }
        merchant["sellable" if int(side_text) == MERCHANT_SIDE_COMPANION else "wares"].append(item)
    return merchant


def _read_heard(raw_lines: Any) -> list[dict[str, Any]]:
    """What was said since the last state, oldest first"""
    heard: list[dict[str, Any]] = []
    if not isinstance(raw_lines, list):
        return heard
    for entry in raw_lines:
        if not isinstance(entry, str):
            continue
        # The text comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, HEARD_PART_COUNT - 1)
        if len(parts) != HEARD_PART_COUNT:
            continue
        age_text, ref_id_text, name, text = parts
        form_id = parse_ref_id(ref_id_text)
        if not age_text.isdecimal() or form_id is None or text.strip() in ("", OWN_LINE_TEXT):
            continue
        heard.append({
            "seconds_ago": int(age_text),
            "ref_id": format_ref_id(form_id),
            "name": _tidy_name(name),
            "text": text.strip(),
        })
    return heard


def expand_quick_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Turns a state the game mod sent packed (see KEY_QUICK_NUMBERS) back into the single entries a state always had.
    A state that is not packed comes back as it is. What cannot be read is left out, as if the game had not sent it"""
    if KEY_QUICK_NUMBERS not in raw and KEY_QUICK_TEXTS not in raw:
        return raw
    expanded: dict[str, Any] = {KEY_BUILT_QUICK: True}
    numbers = _numbers(raw.get(KEY_QUICK_NUMBERS))
    if numbers is not None and len(numbers) > 0 and 0 < numbers[0] <= len(numbers):
        place = numbers[0]
        while place + 1 < len(numbers):
            block, count = numbers[place], numbers[place + 1]
            values = numbers[place + 2:place + 2 + max(count, 0)]
            if count < 0 or len(values) != count:
                # Cut off. What came before it is whole
                break
            key = QUICK_BLOCK_KEYS.get(block)
            if key is not None:
                # The horse that was called is a single number in the state, everything else a list
                expanded[key] = values[0] if block == QUICK_BLOCK_OF_HORSE_CALLED and count == 1 else values
            place += 2 + count
    texts = raw.get(KEY_QUICK_TEXTS)
    if isinstance(texts, list):
        for entry in texts:
            if not isinstance(entry, str) or QUICK_TEXT_SEPARATOR not in entry:
                continue
            key, text = entry.split(QUICK_TEXT_SEPARATOR, 1)
            if key in QUICK_TEXT_LISTS:
                expanded.setdefault(key, []).append(text)
            elif key != "":
                expanded[key] = text
    # What the game mod sent as a single entry counts as it is
    for key, value in raw.items():
        if key not in (KEY_QUICK_NUMBERS, KEY_QUICK_TEXTS):
            expanded[key] = value
    return expanded


def read_menus(raw_lines: Any) -> list[tuple[str, float]]:
    """Which menus were open in the game since its request before, as the plugin of the game mod wrote them, oldest
    first: the name the game gives the menu and for how many seconds it was open. Empty if there is nothing readable"""
    menus: list[tuple[str, float]] = []
    if not isinstance(raw_lines, list):
        return menus
    for entry in raw_lines[:MAX_MENU_LINES]:
        if not isinstance(entry, str):
            continue
        parts = entry.split(QUEST_PART_SEPARATOR)
        if len(parts) != MENU_PART_COUNT or parts[0].strip() == "" or not parts[1].isdecimal():
            continue
        menus.append((parts[0].strip()[:MAX_MENU_NAME_LENGTH], int(parts[1]) / MILLISECONDS_PER_SECOND))
    return menus


def read_events(raw_lines: Any) -> list[dict[str, Any]]:
    """What happened in the game as the plugin of the game mod wrote it, oldest first: 'seconds_ago', 'game_time'
    (the time of day in the game, eg 14:35), 'urgent' and 'text'. Empty if there is nothing, or nothing readable"""
    events: list[dict[str, Any]] = []
    if not isinstance(raw_lines, list):
        return events
    for entry in raw_lines:
        if not isinstance(entry, str):
            continue
        # The text comes last and is not split any further
        parts = entry.split(QUEST_PART_SEPARATOR, EVENT_PART_COUNT - 1)
        if len(parts) != EVENT_PART_COUNT:
            continue
        age_text, minutes_text, urgent_text, text = parts
        if not age_text.isdecimal() or not minutes_text.isdecimal() or text.strip() == "":
            continue
        minutes = int(minutes_text) % MINUTES_PER_DAY
        events.append({
            "seconds_ago": int(age_text),
            "game_time": f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}",
            "urgent": urgent_text == "1",
            "text": text.strip(),
        })
    return events


def _read_book(raw_lines: Any) -> dict[str, Any] | None:
    """One page of a book as the plugin of the game mod wrote it: 'page', 'pages', 'kind' (book, note or spell tome),
    'id', 'title' and 'text' (its paragraphs, one per line). None if the game sent no book"""
    if not isinstance(raw_lines, list) or len(raw_lines) == 0 or not isinstance(raw_lines[0], str):
        return None
    # The title comes last and is not split any further
    header = raw_lines[0].split(QUEST_PART_SEPARATOR, BOOK_HEADER_PART_COUNT - 1)
    if len(header) != BOOK_HEADER_PART_COUNT:
        return None
    page_text, pages_text, kind_text, id_text, title = header
    form_id = parse_ref_id(id_text)
    if not page_text.isdecimal() or not pages_text.isdecimal() or not kind_text.isdecimal() or form_id is None:
        return None
    text = ""
    for entry in raw_lines[1:]:
        if not isinstance(entry, str):
            continue
        join, separator, piece = entry.partition(QUEST_PART_SEPARATOR)
        piece = piece.strip()
        if separator == "" or piece == "":
            continue
        if text == "":
            text = piece
        elif join == BOOK_PIECE_FOLLOWS_DIRECTLY:
            text += piece
        elif join == BOOK_PIECE_FOLLOWS_BLANK:
            text += " " + piece
        else:
            text += "\n" + piece
    return {
        "page": int(page_text),
        "pages": int(pages_text),
        "kind": BOOK_KINDS.get(int(kind_text), BOOK_KINDS[0]),
        "id": format_ref_id(form_id),
        "title": title.strip() or UNKNOWN_NAME,
        "text": text,
    }


def _add_view_of_companion(state: dict[str, Any]) -> None:
    """Adds where the player, the characters and things nearby and the targets of quests are, seen from the companion

    The game measures everything from the player. Asking it again from the companion would cost two more
    questions per character, so this is calculated here: from where the player faces, and from the distance
    and angle of the companion and of the other character. Heights are left out, and the game sends whole
    meters and whole degrees, so the result is a little less exact than what was measured from the player.

    If the AI asked for the lists as the companion sees them (state['around_companion']), the game has measured
    them from the companion already. Then only where the player is gets calculated.
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
    if state.get("around_companion"):
        return
    # A target with a distance is in the area of the player, or it is behind a door that is
    targets = [target for quest in state.get("quests", []) for target in quest.get("targets", []) if "distance_meters" in target]
    for character in state["nearby"] + state.get("things", []) + state.get("traps", []) + state.get("found", []) + targets:
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


def read_heights(raw_heights: Any) -> dict[str, int]:
    """How many meters above (negative: below) the viewer something stands, by its ref ID (see KEY_HEIGHTS).
    Empty if the game sent nothing that can be read"""
    values = _numbers(raw_heights)
    if values is None or len(values) % VALUES_PER_HEIGHT != 0:
        return {}
    return {_ref_id(values[place]): values[place + 1] for place in range(0, len(values), VALUES_PER_HEIGHT) if values[place + 1] != 0}


def _add_heights(state: dict[str, Any], heights: dict[str, int]) -> None:
    """Adds 'height_meters' to the characters and things the game told a height for. It counts from whoever the
    game measured the lists from"""
    if len(heights) == 0:
        return
    for name in LISTS_WITH_HEIGHTS:
        entries = state.get(name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            height = heights.get(entry.get("ref_id")) if isinstance(entry, dict) else None
            if height is not None:
                entry["height_meters"] = height


def _signed_angle(angle_degrees: float) -> int:
    """The angle in whole degrees from -180 to 179, so that negative means left"""
    return (round(angle_degrees) + 180) % 360 - 180


def _read_nearby(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The characters around the viewer. A list in another shape (eg from a game mod that is older than this server) is left out"""
    values = _numbers(raw.get(KEY_NEARBY_VALUES))
    if values is None or len(values) % VALUES_PER_NEARBY_CHARACTER != 0:
        return []
    names = _names_by_place(raw.get(KEY_NEARBY_NAMES))
    # A list of senses that does not fit the characters is left out
    senses = _numbers(raw.get(KEY_NEARBY_SENSES))
    if senses is not None and len(senses) != len(values) // VALUES_PER_NEARBY_CHARACTER:
        senses = None

    nearby: list[dict[str, Any]] = []
    for place in range(len(values) // VALUES_PER_NEARBY_CHARACTER):
        start = place * VALUES_PER_NEARBY_CHARACTER
        form_id, distance, angle, hostile, dead = values[start:start + VALUES_PER_NEARBY_CHARACTER]
        character: dict[str, Any] = {
            "name": names.get(place, UNKNOWN_NAME),
            "ref_id": _ref_id(form_id),
            "distance_meters": distance,
            "angle_degrees": angle,
            "hostile": hostile != 0 and dead == 0,
            "dead": dead != 0,
        }
        # The dead are never hostile. In that place a newer game mod tells how many different items a dead one
        # carries, plus 1 (0 = it does not tell)
        if dead != 0 and hostile > 0:
            character["item_count"] = hostile - 1
        if senses is not None and senses[place] in SENSE_NAMES:
            character["sense"] = SENSE_NAMES[senses[place]]
        nearby.append(character)
    # The living first and among them the hostile ones, then the dead, each nearest first
    nearby.sort(key=lambda character: (character["dead"], not character["hostile"], character["distance_meters"]))
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
