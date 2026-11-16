import asyncio
import copy
import json
import random
import re
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.command_chain import MAX_WAITING_STEPS, CompanionChain, describe_step, read_pause_seconds
from src.companion.game_state import KEY_INVENTORY_OWN, MERCHANT_IS_CLOSED, MERCHANT_IS_NONE
from src.companion.gestures import GESTURE_NAMES as ALL_GESTURE_NAMES, pick_gesture
from src.companion.ref_ids import format_ref_id, parse_ref_id, split_form_id
from src.companion.server_log import log, shorten
from src.companion.state_text import describe_state
from src.companion.voice import MAX_TEXT_LENGTH, PIPER_LANGUAGE_NAME, CompanionVoice, clean_text, find_voice_folder

SERVER_NAME: str = "skyrim-companion"
SERVER_VERSION: str = "0.49"
# say: the manners of speaking as the AI is told them
MANNER_NAMES: str = ", ".join(consts.MANNERS)
# say: the number of the sentence that was started last, the animation that was picked last (so that the next one is
# another one), and what runs beside the sentence that is being spoken (see _finish_speech)
_say_number: int = 0
_last_talk_animation: str = ""
_speech_task: asyncio.Task | None = None
SUPPORTED_PROTOCOL_VERSIONS: list[str] = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FALLBACK_PROTOCOL_VERSION: str = "2025-06-18"

TOOL_COMMAND: str = "COMMAND"
# What the AI is told about the language of say. Piper only pronounces English. With another voice (XTTS) the note
# is swapped for the language that voice speaks, see _tools_for
SAY_LANGUAGE_NOTE: str = "Write it in English: the voice cannot pronounce other languages. "
TOOL_STATE: str = "STATE"
ARGUMENT_DEBUG: str = "debug"
ARGUMENT_COMPARE: str = "compare"
# What differs by itself between a state built in one go and one built the old way: the mark of the first, and how
# long each took
COMPARE_SKIPPED_KEYS: tuple[str, ...] = ("quick", "build_ms", "scan_ms")
ARGUMENT_INVENTORY: str = "inventory"
ARGUMENT_FILTER: str = "filter"
ARGUMENT_CORPSE: str = "corpse"
ARGUMENT_MERCHANT: str = "merchant"
ARGUMENT_THINGS: str = "things"
ARGUMENT_MAX: str = "max"
ARGUMENT_AROUND: str = "around"
ARGUMENT_STATS: str = "stats"
ARGUMENT_VISIBLE: str = "visible"
ARGUMENT_SPACE: str = "space"
# A spell is named by its ID (this many hex digits, as STATE lists it) or by its name
SPELL_ID_LENGTH: int = 8
ARGUMENT_SPACE_DIRECTION: str = "space_direction"
ARGUMENT_SPACE_METERS: str = "space_meters"
ARGUMENT_SPACE_ANGLE: str = "space_angle"
ARGUMENT_FIND: str = "find"
ARGUMENT_FIND_GESTURE: str = "find_gesture"
ARGUMENT_READ: str = "read"
ARGUMENT_PAGE: str = "page"
SNEAK_NAMES: str = ", ".join(consts.SNEAK_CHOICES)
ARGUMENT_THEN: str = "then"
ARGUMENT_AFTER_PREVIOUS: str = "after_previous"
# What a step of 'then' can carry: the same as a command, without a chain of its own
STEP_FIELDS: list[str] = ["action", "target", "detail", "loud", "quiet", "run", "manner"]
# How long the sentence of the say that was sent last takes, for a chain that waits until it is spoken
_last_say_seconds: float = 0.0
MAX_FIND_LENGTH: int = 60
COMPASS_NAMES: str = ", ".join(consts.COMPASS_NAMES)
RELATIVE_NAMES: str = ", ".join(consts.RELATIVE_NAMES)
EXPRESSION_NAMES: str = ", ".join(consts.EXPRESSIONS)
ROOM_DIRECTION_NAMES: str = ", ".join(consts.ROOM_DIRECTIONS)
# What the AI may also write for 'around' if it means its own character
AROUND_COMPANION_WORDS: list[str] = [consts.AROUND_COMPANION, "me", "companion", "self"]
GESTURE_NAMES: str = ", ".join(ALL_GESTURE_NAMES)

SERVER_INSTRUCTIONS: str = (
    "You are playing a companion character inside the game Skyrim, fighting and travelling alongside the player. "
    "The player talks to you directly; you decide in character what your character does. "
    "Your character is whoever follows the player in the game at the moment; STATE tells you who that is. "
    "If the player has no follower yet, you have no body in the game. "
    "Use STATE to see where you are and who is around, and COMMAND to act. "
    "Targets are always the ref_id values from STATE, never names. "
    "When the player points you at someone (eg 'the archer behind me on the left'), get STATE "
    "and pick the matching character by its direction and distance. "
    "STATE takes the game about a second, and its answer stays in the conversation. So call it only when you need "
    "something you do not know yet: before you pick a target, or when the player asks about the surroundings. "
    "Do not call it again and again during a fight just to keep watching. "
    "The game's built-in follower AI handles moment-to-moment fighting and walking; you only set the direction. "
    "With say you speak as the character in the game. Say there only what the character itself can see or know: "
    "nothing behind its back, behind walls or too far away to see. What you know from STATE but the character cannot, "
    "tell the player outside the game instead: in the chat, as a narrator in the third person "
    "(eg 'to the left of our hero a wolf lies in wait')."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": TOOL_COMMAND,
        "description": (
            "Make your companion character do something in the game. "
            "follow = follow the player. With a target (the ref_id of a character) you follow that character instead, "
            "wherever it goes, until 'follow' without a target or another command that sends you somewhere. "
            "wait = stay here, "
            "attack = attack the target (works on peaceful characters too, so only when the player wants it), "
            "stand_down = stop fighting (whoever keeps attacking pulls you back into the fight), "
            "flee = retreat: you do not fight and run from enemies until your next command, "
            "move_to = walk to the target and wait there until 'follow'. The target is anything with a ref_id in STATE: "
            "a character, a thing, a door from 'Doors', a place from 'Places' (only if it is close enough to be loaded) "
            "or the target of a quest. You stop at a door, you do not go through it. "
            f"In front of a character you stop about {consts.MOVE_TO_PERSON_METERS} meters away; 'detail' can name another distance in meters "
            f"(0 to {consts.MOVE_TO_HIGHEST_KEEP_METERS}, also for a thing). If the target is not in your lists, look for it with STATE and 'find'. "
            "move = walk a number of meters in a direction and wait there until 'follow', for when there is nothing with a "
            f"ref_id to walk to. 'detail' is the direction and behind it the meters, eg 'north 10' or 'ahead-left 6' "
            f"(nothing = {consts.MOVE_DEFAULT_METERS}, at most {consts.MOVE_HIGHEST_METERS}). Directions: a point of the compass ({COMPASS_NAMES}) "
            f"or seen from where you face ({RELATIVE_NAMES}). Look first with STATE and 'space' where walls and drops are. "
            "Your next STATE tells you if you did not get that far. "
            "enter = walk to the target, a door from 'Doors', and go through it on your own. You wait on the other side "
            "until 'follow', which brings you back out. The player stays outside, so the game does not load the place you are in: "
            "STATE with around 'you' then only shows what the game keeps of it in memory (the characters who live there, the doors), "
            "not everything that is really there. Say so when you report what is inside. "
            "run = true: run instead of walk (move_to, move, enter, follow). With move_to, move and enter you run there. "
            "With follow you keep running after the player until your next command; when you fall more than about 10 meters "
            "behind the player you run to catch up on your own, also without run. "
            "make_room = step a few meters out of the way, for when you stand in the player's way. 'detail' says where to: "
            "back (away from the player, also if you name nothing), left or right (as the player sees it who looks at you), "
            f"with the meters behind it if you like, eg 'left 5' (nothing = {consts.ROOM_DEFAULT_METERS}, at most {consts.ROOM_HIGHEST_METERS}). "
            "If that side is blocked you try another one yourself, and your next STATE tells you. "
            "If you were following the player, you stay at the new spot for a moment and then follow again. "
            "face = turn your whole body (you turn on the spot as characters do, and the answer comes when you face there) to the target (anything with a ref_id, also a place or a quest target far away) "
            f"or to the point of the compass in 'detail' ({COMPASS_NAMES}). Do this before you point at something with a gesture. "
            "Only while you stand: not while you walk, fight or sit. "
            "loot = walk to the target, a dead character or a container (chest, barrel, sack ...), and take what is in it: "
            "everything, or only the kind of item whose ID is in 'detail' (see what is in it with STATE and 'corpse' first). "
            "The walk takes a while. At a body you kneel down, at a container you go through it, and only then you have "
            "the things; your next STATE tells you what you got. Afterwards you follow or wait as before. "
            "Containers in a house or a shop belong to someone, so empty those only when the player wants it. "
            "pick_up = walk to the target, an item that lies around (from 'Things' in STATE, also an arrow that was shot), "
            "and take it. Your next STATE tells you whether you got it. Afterwards you follow or wait as before. "
            "Several things in one go: put their ref_ids into 'target' with commas between them. You walk to the nearest, "
            "take everything of your list that lies within reach there with one bend, and go on to the rest. "
            "Or 'all' in 'detail' = everything loose around you, or around the one thing in 'target': 'all' (within "
            f"{consts.PICK_UP_USUAL_METERS} meters), 'all 4' (within 4 meters, at most {consts.PICK_UP_HIGHEST_METERS}), "
            f"'all 3 potions' (only that kind: {', '.join(consts.PICK_UP_KINDS)}), 'all 3 no clutter' (everything but plates, "
            "tankards and the like). 'all' leaves out what belongs to someone. "
            "What lies in a house or a shop belongs to someone, so name that by its ref_id only when the player wants it. "
            "sit = walk to the piece of furniture that is the target and sit down on it (no target = the seat nearest to you). "
            "That takes a few seconds, your next STATE says 'sitting' once you sit. "
            "You stay seated until 'follow' or another command that makes you move. "
            "look_at = keep your eyes on the target (a character or anything else with a ref_id; the player is 00000014). "
            "It holds until look_at without a target, which lets you look around freely again. "
            "gesture = play the gesture named in 'detail': " + GESTURE_NAMES + ". "
            "Some go on until 'detail' is 'stop' or until you move on. Gestures work best while you stand around. "
            "drink, eat, read_book and write_down also work while you sit on a chair, drink_at_table and pass_out only there. "
            "drunk makes you sway and stagger, also while you walk, until gesture with 'stop'. "
            "STATE with 'find_gesture' finds the gestures that fit a word (eg drink). "
            "expression = put a look on your face, named in 'detail': " + EXPRESSION_NAMES + ". A number behind it "
            "(1 to 100) makes it weaker, eg 'happy 50'. It stays until 'detail' is 'none'. "
            "say = speak the text in 'detail' aloud in the game, with the voice of your character, so that the player hears "
            "you there. " + SAY_LANGUAGE_NOTE + "One or two sentences at a time. "
            "With 'loud': true you call it out with a raised voice: for a warning, or when the player is far away. "
            "With 'quiet': true you say it in a low voice, with a calm face and still hands: while you sneak, or when "
            "nobody else is to listen. The player hears it less far, so stay close. Your voice only gets quieter, it "
            "does not whisper. Enemies can hear what you say: said normally it can give you and the player away, called "
            "out it carries far, in a low voice nobody but the player notices it. So while the player sneaks, or when "
            "enemies are near and have not noticed you, always speak with 'quiet'. "
            "target = whom you speak to; nothing = the player. You turn to them and look at them while you speak, and "
            "afterwards your eyes are free again (or back on what look_at holds; while look_at holds and you name no "
            "target, your eyes stay there). So name the target whenever you speak to someone other than the player. "
            "To another character you stop, walk over first if they are more than about 5 meters away, turn to them and "
            "then speak; afterwards you follow the player again if you did before. No wait or face is needed for that. "
            "manner = how you speak, it moves your hands and face to match for this sentence: " + MANNER_NAMES + ". "
            "A character you speak to (not the player) looks at you while you speak, and its face answers how you speak: "
            "startled when you are angry, glad when you joke or are excited, sad or puzzled with you. "
            "Nothing = neutral, with hands that vary on their own. A look you set with expression stays on your face. "
            "While you walk or fight you keep moving and only your lips, head and face move. "
            "sheathe = put your weapon away and keep it away even when the player draws one (otherwise you draw whenever "
            "the player does). In a fight you draw it all the same. draw = draw your weapon, and from then on draw it and "
            "put it away together with the player again. "
            "open and close = open or close a door that only swings, like the door of a room or a gate. The target is its "
            "ref_id, without a target it is the door nearest to you. You have to stand within about 5 meters of it; if not, "
            "the answer names its ref_id for move_to. The doors from 'Doors' lead into another place, those are for 'enter'. "
            "With your things (the target is the ID of an item from an inventory in STATE): "
            "equip = hold or wear the item, or drink or eat it if it is a potion or food (you play the drinking or eating "
            "animation with it while you stand around or sit). equip on a spell tome reads it: "
            "you know its spell from then on and the tome is used up, "
            "unequip = put the item away, "
            "favorite = mark a weapon, a shield or a piece of armor of yours as a favorite ('detail' on, the default) or "
            "take the mark off (off). unfavorite = take the mark off, the same as favorite with 'detail' off. The game hands you the strongest thing you carry on its own. Once you have favorites "
            "you switch back to them by yourself: a favorite weapon instead of another weapon (one for close combat, one "
            "for range if you mark both), a favorite shield, a favorite piece of armor per part of the body. So mark "
            "what you want to fight in, and what you only carry for the player stays baggage. "
            "give = hand the item to the player, take = take the item out of the player's inventory "
            "(only when the player wants it). In the player's inventory 'favorite' marks what the player keeps among the "
            "favorites: ask before you take a weapon or a piece of armor that is one, for potions and food it does not matter. "
            "For give and take 'detail' says how many: a number or 'all', nothing = 1. "
            "Several items in one go: the IDs in 'target' with commas between them, eg '00013987,000236A5,0002C672', and "
            "in 'detail' how many of each ('1,1,3'), one number for all of them, or 'all'. You hold out your hand once "
            f"and the answer lists what changed hands and what did not. At most {consts.HAND_OVER_HIGHEST_ITEMS} kinds of items at a time. "
            f"give with 'detail' '{consts.HAND_OVER_JUNK}' and no target hands the player everything you neither wear, hold nor have "
            "marked as a favorite, apart from ammunition, potions, food, keys and gold: for handing over loot. "
            "cast_spell = cast a spell you know: its ID or its exact name in 'detail', and whom you cast it on as the target "
            "(the player's ref_id to heal the player; no target for a spell on yourself). STATE with inventory 'own' lists "
            "the spells you know with their IDs. You only know spells you have learned from a spell tome, so ask the player "
            "for a tome if you know none. You take the spell into your right hand, cast it with the usual movement and "
            "hold again what you held before; that takes a few seconds and costs you magicka as it does the player (too "
            "little magicka = no spell). You turn to the target first. A spell that is held like flames or healing hands "
            f"runs for {consts.CAST_HOLD_SECONDS} seconds; a number behind the spell sets other seconds (1 to "
            f"{consts.CAST_HOLD_HIGHEST_SECONDS}), eg 'Flames 6'. In the saddle and on a seat it is cast without the movement, for the same magicka. "
            "auto_heal = whether you heal by yourself: when the player is below a part of full health (and close and in "
            "sight) you cast a healing spell on the player, and otherwise on yourself when you are that low. Only with a "
            "spell you know that heals (one for others, like healing hands, or one for yourself, like healing), and only "
            "while your magicka lasts. It is on from the start, below "
            f"{consts.AUTO_HEAL_USUAL_PERCENT}% of health. 'detail': '{consts.AUTO_HEAL_OFF}', '{consts.AUTO_HEAL_ON}', or a number from "
            f"{consts.AUTO_HEAL_LOWEST_PERCENT} to {consts.AUTO_HEAL_HIGHEST_PERCENT} (on, below that many percent). Each heal is told in your next STATE. "
            "lead = walk ahead of the player to the target (a character, a door, a place or a quest target, as for move_to, "
            "with the same 'detail' and 'run'): you stop whenever the player falls more than about 15 meters behind, look "
            "back, and go on once the player has caught up. When you are there you follow the player again. Use it when "
            "the player asks you to show the way. Not in the saddle. "
            "Trading: buy = buy something from a merchant, sell = sell something of yours to one. The target is the "
            "merchant (its ref_id from 'Nearby'), 'detail' the IDs of the items with commas between them, each with how many "
            "or 'all' behind it if not one, eg '00034CDF 2, 0003EADD' (one movement of the hand, and the answer lists what "
            f"was traded for how much and what was not). sell with 'detail' '{consts.HAND_OVER_JUNK}' sells everything the merchant "
            "buys that you neither wear, hold nor have as a favorite, apart from ammunition, potions, food and keys. The "
            "items may also stand in 'target' as for give and take: then 'detail' is the merchant, or how many, and "
            "without a merchant it is the one you looked at or traded with last. "
            "See what there is and what it costs with STATE and 'merchant' first. You walk over to the "
            "merchant if you are not there, and your next STATE tells you how it went. You pay with your own gold (the "
            "player can give you some with the gold's ID 0000000F), the merchant pays with its own. If the gold does not "
            "last for all of them, nothing is traded and the answer says for how many it lasts. "
            "Riding: claim_horse = the horse that is the target is yours from now on (its ref_id from 'Nearby'; do that when "
            "the player tells you which horse is yours). The player can still ride it. A horse that belongs to someone "
            "else is refused: that would be a stolen horse. The horse you had before goes back to whom it belonged to. "
            "release_horse = give your horse back (or, with a target, another horse you once took): it belongs to whom "
            "it belonged before again. mount = walk to your horse, get on and ride after the player; only a horse "
            "that is too far away to walk to is brought to your side. With a target you take that horse as yours first. dismount = get off, "
            "the horse stays where it is; if the game does not let you off, STATE tells you so afterwards. When the player mounts or dismounts, you do the same on your "
            "own. ride_lock with 'detail' 'on' switches that off: you stay in the saddle, or on foot, whatever the player "
            "does, until ride_lock 'off'. mount and dismount always work. In the saddle you can speak, look, cast and "
            "fight, and you can ride somewhere: move_to and move take you there on horseback and you wait there in the "
            "saddle (run: true = gallop), follow with a target makes you ride after that character, wait makes you stop "
            "where you are, still in the saddle. What is done on foot (enter, loot, pick_up, sit, open, close, make_room) "
            "needs dismount first. You cannot ride indoors. "
            "Only one horse can be called at a time: calling another one stops the one that is still on its way. "
            "call_horse = a horse runs to its owner and stays there: your horse comes to you, the horse of the player to the "
            "player (the target is its ref_id from 'Your horse' or 'Player's horse' in STATE; no target = your horse, or the "
            "player's if you have none). 'detail' 'player' or 'you' sends it to that one instead. Use it when the player "
            "calls a horse by its name or asks for it. A horse too far away to run is brought to the side of whom it comes to. "
            "The horse of the player is known once the player has ridden it. Both horses stay where they are left. "
            "rename_horse = give a horse a new name, the one in 'detail' (the target is the horse, no target = your horse). "
            "Use it when the player says what a horse is called from now on. "
            "sneak = how you sneak, in 'detail': auto (the way it is unless you change it) = you sneak whenever the player "
            "sneaks and stand up when the player does, on = you sneak whatever the player does (to wait crouched, or to "
            "creep ahead on your own), off = you walk upright whatever the player does. on and off hold until sneak with "
            "'auto'. STATE shows 'sneaking' for the player and for you. Not in the saddle, and in a fight it only takes "
            "hold afterwards. While you sneak, speak with 'quiet'. "
            "passive = whether you hold back, in 'detail': on = you follow as always but start no fight of your own accord "
            "(to pass a wolf by the road, or not to disturb a talk), off = you attack enemies as always. If you or the player "
            "are attacked you fight back all the same, and attack with a target still works. It holds until passive with "
            "'off'; STATE shows 'passive on' for you meanwhile. stand_down only ends the fight that is going on. "
            "Commands one after the other: 'then' is a list of up to " + str(MAX_WAITING_STEPS) + " more commands (each with "
            "action, target, detail and so on) that start one by one, each when you are done with the one before: move_to a "
            "bard and then gesture dance, sit and then say, say and then mount. 'after_previous': true puts a single command "
            "behind what you are doing and what waits already. pause = wait the seconds in 'detail' (1 to 60), for such a list. "
            "A new command that moves you (follow, wait, flee, attack, move_to ...) throws away what waits. say, look_at, "
            "gesture, expression and what you do with your things run at once and leave it alone. If a step fails, it is "
            "skipped and the rest goes on. STATE shows what waits ('Queue') and tells afterwards what became of it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": consts.PLAYER_ACTIONS},
                "target": {"type": "string", "description": "ref_id of the target from STATE: a character (attack, move_to, follow, loot, look_at, face), a container (loot, move_to), a seat (sit, move_to), an item that lies around (pick_up), a horse (claim_horse, release_horse, mount, call_horse, rename_horse), a door (enter, move_to, look_at), a place or a quest target (move_to, look_at, face). For say: whom you speak to (a character; nothing = the player). Or the ID of an item from an inventory (equip, unequip, give, take); for give and take also several IDs with commas between them, for pick_up several ref_ids. Required for these actions, except sit and look_at, and pick_up with 'all'."},
                "detail": {"type": "string", "description": "Extra information for the action: how many for give and take, the ID of the item for loot (nothing or 'all' = everything), 'all' with the meters and maybe a kind for pick_up, the name of the gesture or 'stop' for gesture, the look or 'none' for expression, the direction (back, left, right) for make_room, the direction and the meters for move, how far to stop in front of the target for move_to, the point of the compass for face, the text to speak for say, the spell name for cast_spell, 'on' or 'off' for ride_lock, the new name for rename_horse, auto, on or off for sneak, the seconds for pause."},
                "loud": {"type": "boolean", "description": "Only for say: true = call it out with a raised voice instead of speaking normally."},
                "quiet": {"type": "boolean", "description": "Only for say: true = say it in a low voice instead of speaking normally, eg while you sneak. Not together with 'loud'."},
                "run": {"type": "boolean", "description": "Only for move_to, move, enter and follow: true = run instead of walk."},
                "manner": {"type": "string", "enum": list(consts.MANNERS), "description": "Only for say: how you speak. It picks how your hands move and the look on your face while you speak. Nothing = neutral."},
                ARGUMENT_THEN: {"type": "array", "maxItems": MAX_WAITING_STEPS, "description": "More commands that start one after the other, each when you are done with the one before.",
                                "items": {"type": "object", "properties": {
                                    "action": {"type": "string", "enum": consts.PLAYER_ACTIONS},
                                    "target": {"type": "string"}, "detail": {"type": "string"}, "loud": {"type": "boolean"},
                                    "quiet": {"type": "boolean"},
                                    "run": {"type": "boolean"}, "manner": {"type": "string", "enum": list(consts.MANNERS)}},
                                    "required": ["action"]}},
                ARGUMENT_AFTER_PREVIOUS: {"type": "boolean", "description": "true = do not start this command at once: put it behind what you are doing and what waits already."},
            },
            "required": ["action"],
        },
    },
    {
        "name": TOOL_STATE,
        "description": (
            "Get the current situation in the game as a few short lines. "
            "First the place, whether it is indoors or outdoors, the time of day in the game and outdoors the weather "
            "(clear, cloudy, rain or snow). "
            "'Player': health, fighting or not, 'weapon drawn' if so, 'sneaking' if so, 'sitting on' with the piece of furniture, 'riding' with the name of the horse or 'lying asleep' if so, the compass direction the player faces, and the position x,y,z "
            "in game units (about 70 per meter, x grows to the east, y to the north, z upwards). "
            "'You': your own character with health, where it is, the compass direction it faces, where the player is "
            "seen from you, 'riding' with the name of the horse, waiting, following (the player, or the character named behind it), "
            "'sitting on' with the piece of furniture, or walking to someone, "
            "retreating, whom it fights, 'running' while you run, 'sneaking' while you sneak ('sneak on' or 'sneak off' behind it after the command sneak), 'weapon drawn' if so, 'weapon kept away' after sheathe, the look on its face and what it looks at because of look_at. "
            "If you are not where the player is, the line names the place you are in instead. "
            "'Player's horse' comes once the player has ridden a horse: its name and ref_id and where it is seen from the player. "
            "'running to the player' stands behind a horse that was called with call_horse. "
            "'Your horse' comes once you have one (see claim_horse): its name and ref_id, where it is seen from you, "
            "and 'ride_lock on' while you do not mount and dismount together with the player. "
            "Without a follower of the player you have no body. "
            "'Nearby': up to 10 lines (more with 'max'), one per character, people and animals, within about 50 meters of the player (outdoors about 200 meters, so that "
            "archers on a tower and game far off are in it): first the living, hostile "
            "ones before the others, then the dead (marked 'dead'), each nearest first. "
            "ref_id, name, distance and direction from the player, after 'from you' the same seen from your own character, "
            "and 'hostile' if it is hostile to the player. "
            "'Traps': tripwires, pressure plates, bear traps and other parts of traps within about 15 meters, each with its "
            "ref_id, distance and direction. The lines only come when there are any, without asking. Warn the player in time: "
            "a tripwire or a pressure plate sets the trap off for whoever steps on it, you included. "
            "'Found for' only comes when you ask for it with 'find': everything in the loaded area (up to about 300 meters) "
            "whose name holds the text you gave: characters, doors (by the place they lead to), places of the map and things, "
            "nearest first, each with its ref_id, distance and direction. Use it when someone or a shop is not in 'Nearby' or 'Doors', "
            "then walk there with move_to or enter. Names are as the game shows them (in the language of the game), a part of the name "
            "is enough. A character inside a building is only found from inside: from outside, find the door to the building. "
            "'Space around you' only comes when you ask for it with 'space': what rays from your character find within about "
            "15 meters in eight directions seen from where you face: a wall or another upright obstacle with its distance, "
            "ground that rises, a DROP (the ground falls away after that many meters) and the directions that are open. "
            "The rays go at chest height, so low things like a table or a fence do not show. Ask for it before you walk "
            "somewhere with the command move, or when the player asks what the place is like. "
            f"You can also look into one direction and further: 'space_direction' (a point of the compass or seen from where you face, "
            f"like for move), 'space_meters' (up to {consts.SPACE_HIGHEST_METERS}) and 'space_angle' (how wide the rays fan out, "
            f"{consts.SPACE_DEFAULT_LOOK_DEGREES} degrees if you say nothing, a small angle for a narrow look). Each of them asks for the space by itself. "
            "Outdoors the game only knows about 120 meters around the player, further out a ray finds nothing. "
            "'Things' only comes when you ask for it with 'things': what stands and lies within about 10 meters of the player, "
            "one per line with its ref_id: furniture ('seat' if you can sit on it, see the command sit; tables are just called 'Table'), containers such as "
            "chests and barrels (see the command loot; 'locked' if you cannot open it, 'empty' or how many different items are in it if the game tells) and items that lie around "
            "(see the command pick_up; arrows that were shot are among them). "
            "Ask for it when you look for a seat or a chest or the player asks what is around, not every time. "
            "Directions are relative to where the player (after 'from you': your character) faces: "
            "ahead, ahead-right, right, behind-right, behind, behind-left, left, ahead-left. "
            "Use the player's view when the player points at someone, and your own view when you talk about where you stand. "
            "With 'around' set to 'you' the game lists what is around your own character instead (characters, things, places "
            "and quest targets, each with distance and direction 'from you' only). Use that when you are away from the player, "
            "or when you look for something near yourself. "
            "Outdoors the list can miss someone, so get STATE once more if a character the player talks about is not in it. "
            "'Places': the nearest places of the map within about 300 meters of the player (farms, mills, stables, towers, "
            "caves ...), also ones the player has not discovered yet, with distance and direction from the player. "
            "'Doors': the nearest doors that lead into a building, a cave or out of town, named by where they lead. "
            "Use both to say where you are and where to go instead of coordinates. Indoors there are only doors. "
            "After each name is its ref_id, for the commands move_to and look_at. "
            "'Quests': the open quests from the player's journal, each with its open objectives. The ones the player "
            "tracks come first and are marked. 'Miscellaneous' collects the small objectives without a quest name. "
            "The lines are missing if the game cannot tell the quests. "
            "Behind an objective of a tracked quest, in brackets, is its target (what the compass of the game points to): "
            "its name, its ref_id (for move_to and look_at), distance and direction from the player and after 'from you' "
            "from your character, so you can lead the way. "
            "'behind the door to ...' means the ref_id, distance and direction are those of the door you have to go through, "
            "'not in this area' that the target is somewhere else, with the name of that place if the game has one. "
            "'Heard': what characters near the player said since your last STATE (the subtitles of the game), oldest first, "
            "each with how long ago. At most 10 lines, none older than 5 minutes, and each line is given only once. "
            "That covers dialogues and what characters say in passing, but only while both kinds of subtitles are switched on "
            "in the settings of the game. "
            "A line under the player's name is what the player picked in a dialogue menu. 'You' is your own character speaking. "
            "'Events': what happened since your last STATE, oldest first, each with how long ago and the time of day in the "
            "game: who started fighting you or the player and when the player's fight began and ended, who died and who "
            "killed whom, the hits you and the player took (counted together, with the health they cost), where the player "
            "went, what the player used (doors, chests, levers), read (with the ID of the book), took, put on, drank and "
            "cast, whom the player talked to, and when the player started or stopped sneaking. At most 25 lines, none older "
            "than 10 minutes, each given only once. The urgent ones (a fight begins, someone dies, someone is badly hurt) "
            "also hang onto the answer of your next COMMAND as 'Meanwhile'. "
            "While the player is in a menu (trading, the inventory, the map) or at a loading screen, the game stands still "
            "and answers nothing: STATE then tells for how long it has been silent and shows the last state it knows, and "
            f"a COMMAND waits up to {int(CompanionCommandQueue.PAUSE_WAIT_SECONDS)} seconds for the game to go on. "
            "With 'read' you read a book or a note: the ID of the book from an inventory (under 'Books'), from 'Things' "
            "(a book that lies around) or from 'Events' (what the player just read). You get one page of its text, and "
            "'page' gets you the next ones. Reading this way changes nothing in the game. If you carry the book, you open "
            "it and read. "
            "'Gear': what you hold in your hands, your gold and the player's gold. "
            "The whole inventory only comes when you ask for it with 'inventory', because it takes the game a few seconds "
            "and is long: one line per kind of item, and after each item its ID for the commands equip, unequip, give and take. "
            "With 'filter' you get a short list instead, which is quick: 'favorites' or 'equipped', each item with its ID and "
            "what it is (bow, sword, shield, helmet ...). Use that to switch weapons in a fight, eg equip your bow. "
            "'equipped' marks what is worn or held, 'favorite' what the player has among the favorites and, in your own inventory, what you marked with the command favorite. "
            "With 'corpse' you get what a dead character carries or a container holds, in the same form, for the command loot. "
            "With 'merchant' you get what a merchant sells, with the price of each thing, how much gold the merchant and "
            "you have, and what of your things the merchant would buy and for how much: for the commands buy and sell. "
            "'Queue' comes while commands wait for each other (see 'then' of COMMAND): what you do now and what follows. "
            "With 'find_gesture' you get no state at all, only the gestures whose name has that word, for the command gesture. "
            "'Since your last STATE': what became of a loot, pick_up, move_to, move, enter, make_room or sit you started (what you took, or that you did not get there). "
            "With 'visible' the lists only hold what can really be seen from where 'around' says (the player by default): "
            "characters in front with no wall in between, living first, then the dead, then the things in front, and only the "
            "doors and places in front with no wall in between. "
            "From your own view ('around' you) the characters also hold whom you notice without seeing: 'close' = within about "
            "2 meters of you wherever they stand, 'heard (not seen)' = someone you hear, of whom your character only knows that "
            "something is there and roughly where. "
            "Use it when the talk is about what someone sees, not for an overview. "
            "With 'stats' you get the level of the player, whether a level-up is waiting, perk points to spend, health, magicka, "
            "stamina, the weight carried and all skills (highest first), and your own level, health, magicka and stamina."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                ARGUMENT_INVENTORY: {"type": "string", "enum": consts.INVENTORY_CHOICES, "description": "Also list an inventory: own = yours, player = the player's, both. Only when you need it."},
                ARGUMENT_FILTER: {"type": "string", "enum": consts.INVENTORY_FILTERS, "description": "Only a part of the inventory, as one short line (name, ID and kind of each item): favorites = what is marked as a favorite, equipped = what is worn and held, all = everything (the default). Without 'inventory' it is your own. Use it to switch weapons in a fight."},
                ARGUMENT_CORPSE: {"type": "string", "description": "ref_id of a dead character or of a container from 'Nearby': also list what is in it. Replaces 'inventory' for this call."},
                ARGUMENT_MERCHANT: {"type": "string", "description": "ref_id of a merchant from 'Nearby': also list what it sells and what it would buy from you, with prices. Replaces 'inventory' for this call."},
                ARGUMENT_THINGS: {"type": "boolean", "description": "true = also list the furniture, containers and loose items within about 10 meters of the player. Only when you need them."},
                ARGUMENT_AROUND: {"type": "string", "enum": consts.AROUND_CHOICES, "description": "Whose surroundings the lists show: player (the default) or you = your own character."},
                ARGUMENT_STATS: {"type": "boolean", "description": "true = also give the level, the skills and so on of the player, and your own level. Only when the talk is about that."},
                ARGUMENT_FIND: {"type": "string", "description": "A name or a part of one: also list everything in the loaded area with that name (characters, doors, places, things), eg 'Warmaiden' or 'Balgruuf'."},
                ARGUMENT_SPACE: {"type": "boolean", "description": "true = also say where walls, rising ground and drops are around your own character (rays in eight directions). Only when you need it."},
                ARGUMENT_SPACE_DIRECTION: {"type": "string", "description": f"Look into one direction instead of all around: a point of the compass ({COMPASS_NAMES}) or seen from where you face ({RELATIVE_NAMES}). Asks for the space by itself."},
                ARGUMENT_SPACE_METERS: {"type": "integer", "description": f"How far the rays of 'space' reach in meters. Nothing = {consts.SPACE_DEFAULT_METERS}, at most {consts.SPACE_HIGHEST_METERS}."},
                ARGUMENT_SPACE_ANGLE: {"type": "integer", "description": f"With a direction: over how many degrees the rays fan out around it (0 = one ray, nothing = {consts.SPACE_DEFAULT_LOOK_DEGREES}, 360 = all around). Without a direction it looks straight ahead."},
                ARGUMENT_VISIBLE: {"type": "boolean", "description": "true = only what can be seen: in front of the viewer and not behind a wall. The viewer is the one 'around' names."},
                ARGUMENT_MAX: {"type": "integer", "description": f"How many characters 'Nearby' lists at most, and how many 'Things'. Nothing = {CompanionCommandQueue.DEFAULT_MAX_LINES}, at most {CompanionCommandQueue.HIGHEST_MAX_LINES}. More makes the answer longer and slower, so only for an overview."},
                ARGUMENT_FIND_GESTURE: {"type": "string", "description": "A word like drink, dance or lean: list the animations of the game with that word in their name, for the command gesture. The answer is only that list, no state."},
                ARGUMENT_READ: {"type": "string", "description": "The ID of a book or a note (from an inventory, from 'Things' or from 'Events'): also give one page of its text."},
                ARGUMENT_PAGE: {"type": "integer", "description": "With 'read': which page of the book. Nothing = the first one."},
                ARGUMENT_DEBUG: {"type": "boolean", "description": "true = all data as JSON with timings. Only for testing, never while playing."},
                ARGUMENT_COMPARE: {"type": "boolean", "description": "Only for testing the game mod, never while playing: true = fetch the state twice, built in one go by the plugin and built the old way, and list what differs. The player should stand still meanwhile."},
            },
        },
    },
]


def add_mcp_route(app: FastAPI, queue: CompanionCommandQueue, token: str, voice: CompanionVoice | None = None):
    """Adds a minimal MCP server (Streamable HTTP, stateless, JSON replies) that the AI app connects to

    This is written by hand instead of using the MCP SDK on purpose: the SDK needs newer
    versions of packages that Mantella pins (eg pywin32), and only two tools are needed.

    Args:
        token: Secret part of the URL. The route is reachable from the internet through a tunnel,
            so only someone who knows the full URL can control the companion.
        voice: Makes the sound for the command say. Without it the companion cannot speak
    """
    route = consts.ROUTE_MCP_PREFIX + token

    @app.post(route)
    async def mcp_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content=_error(None, -32700, "Parse error"))

        if isinstance(body, list):
            replies = [reply for reply in [await _handle_message(message, queue, voice) for message in body] if reply is not None]
            if len(replies) == 0:
                return Response(status_code=202)
            return JSONResponse(content=replies)

        reply = await _handle_message(body, queue, voice)
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(content=reply)

    @app.get(route)
    async def mcp_get():
        # No server-initiated messages, so there is no event stream to open
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.delete(route)
    async def mcp_delete():
        return Response(status_code=405, headers={"Allow": "POST"})


async def _handle_message(message: Any, queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> dict[str, Any] | None:
    """Handles one JSON-RPC message. Returns None for notifications (they get no reply)"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid request")

    method = message.get("method")
    if "id" not in message:
        return None  # notification (eg notifications/initialized) or a response from the client
    if not isinstance(method, str):
        return None
    message_id = message["id"]
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else FALLBACK_PROTOCOL_VERSION
        return _result(message_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(message_id, {"tools": _tools_for(voice)})
    if method == "tools/call":
        return _result(message_id, await _call_tool(params.get("name"), params.get("arguments"), queue, voice))
    return _error(message_id, -32601, f"Method not found: {method}")


def _tools_for(voice: CompanionVoice | None) -> list[dict[str, Any]]:
    """The tools as the AI gets them: with the language the voice of the companion speaks in the note about say"""
    language = voice.spoken_language if voice is not None else PIPER_LANGUAGE_NAME
    if language == PIPER_LANGUAGE_NAME:
        return TOOLS
    note = (f"Write it in {language}: that is the language your voice speaks. "
            "Keep it short, the voice needs a few seconds for every sentence. ")
    tools = copy.deepcopy(TOOLS)
    for tool in tools:
        tool["description"] = str(tool.get("description", "")).replace(SAY_LANGUAGE_NOTE, note)
    return tools


async def _call_tool(name: Any, arguments: Any, queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> dict[str, Any]:
    """One call of a tool by the AI. What was asked, how long it took and how the answer begins go into the log file"""
    started = time.monotonic()
    log.info("AI -> %s %s", name, shorten(json.dumps(arguments, ensure_ascii=False, default=str)))
    try:
        result = await _run_tool(name, arguments, queue, voice)
    except Exception:
        log.exception("AI <- %s broke off after %.1f s with an error of the server.", name, time.monotonic() - started)
        raise
    answer = ""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
        answer = str(content[0].get("text", ""))
    log.info("AI <- %s after %.1f s%s: %s", name, time.monotonic() - started, " (ERROR)" if isinstance(result, dict) and result.get("isError") else "",
             shorten(answer))
    return result


async def _run_tool(name: Any, arguments: Any, queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        arguments = {}

    if name == TOOL_STATE:
        find_gesture = " ".join(str(arguments.get(ARGUMENT_FIND_GESTURE, "") or "").split())[:MAX_FIND_LENGTH]
        if find_gesture != "":
            # The server knows the gestures itself, the game is not asked
            return _tool_text(_describe_found_gestures(find_gesture), False)
        if arguments.get(ARGUMENT_COMPARE) is True:
            compared, is_compare_error = await _compare_states(queue)
            return _tool_text(compared, is_compare_error)
        inventory = str(arguments.get(ARGUMENT_INVENTORY, "") or "").strip().lower()
        inventory_filter = str(arguments.get(ARGUMENT_FILTER, "") or "").strip().lower()
        if inventory_filter not in consts.INVENTORY_FILTERS:
            inventory_filter = ""
        if inventory_filter == consts.FILTER_ALL:
            inventory_filter = ""
        # A filter without an inventory means the own one
        if inventory_filter != "" and inventory == "":
            inventory = consts.INVENTORY_OWN
        if inventory not in consts.INVENTORY_CHOICES:
            inventory = ""
        corpse = str(arguments.get(ARGUMENT_CORPSE, "") or "").strip()
        if corpse != "":
            corpse_form_id = parse_ref_id(corpse)
            if corpse_form_id is None:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{corpse}' is not a ref_id. Use the one of a dead character or a container from 'Nearby' in STATE."}, True)
            corpse = format_ref_id(corpse_form_id)
        merchant = str(arguments.get(ARGUMENT_MERCHANT, "") or "").strip()
        if merchant != "":
            merchant_form_id = parse_ref_id(merchant)
            if merchant_form_id is None:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{merchant}' is not a ref_id. Use the one of the merchant from 'Nearby' in STATE."}, True)
            merchant = format_ref_id(merchant_form_id)
        max_lines = arguments.get(ARGUMENT_MAX)
        if not isinstance(max_lines, int) or isinstance(max_lines, bool):
            max_lines = CompanionCommandQueue.DEFAULT_MAX_LINES
        max_lines = min(max(max_lines, 1), CompanionCommandQueue.HIGHEST_MAX_LINES)
        around = str(arguments.get(ARGUMENT_AROUND, "") or "").strip().lower()
        # The text goes into a line for the game mod, so it stays one short line
        find = " ".join(str(arguments.get(ARGUMENT_FIND, "") or "").split())[:MAX_FIND_LENGTH]
        # space: all around as always, or into one direction, further and as wide as the AI says
        space_meters = arguments.get(ARGUMENT_SPACE_METERS)
        if not isinstance(space_meters, int) or isinstance(space_meters, bool) or space_meters < 1:
            space_meters = 0
        space_meters = min(space_meters, consts.SPACE_HIGHEST_METERS)
        space_angle = arguments.get(ARGUMENT_SPACE_ANGLE)
        has_space_angle = isinstance(space_angle, int) and not isinstance(space_angle, bool)
        space_angle = min(max(space_angle, 0), consts.SPACE_FULL_CIRCLE_DEGREES) if has_space_angle else consts.SPACE_FULL_CIRCLE_DEGREES
        space_degrees = consts.SPACE_NO_DIRECTION
        is_space_from_facing = False
        space_direction = "-".join(str(arguments.get(ARGUMENT_SPACE_DIRECTION, "") or "").lower().replace("_", " ").split())
        if space_direction != "":
            compass_word = consts.COMPASS_OTHER_WORDS.get(space_direction, space_direction)
            relative_word = consts.RELATIVE_OTHER_WORDS.get(space_direction, space_direction)
            if compass_word in consts.COMPASS_NAMES:
                space_direction = compass_word
                space_degrees = consts.COMPASS_NAMES.index(compass_word) * consts.DEGREES_PER_DIRECTION
            elif relative_word in consts.RELATIVE_NAMES:
                space_direction = relative_word
                space_degrees = consts.RELATIVE_NAMES.index(relative_word) * consts.DEGREES_PER_DIRECTION
                is_space_from_facing = True
            else:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"There is no direction '{space_direction}' to look into. 'space_direction' takes a point of the compass ({COMPASS_NAMES}) or a direction seen from where you face ({RELATIVE_NAMES})."}, True)
            if not has_space_angle:
                space_angle = consts.SPACE_DEFAULT_LOOK_DEGREES
        elif has_space_angle and space_angle < consts.SPACE_FULL_CIRCLE_DEGREES:
            # An angle without a direction: the look goes straight ahead
            space_direction = consts.RELATIVE_NAMES[0]
            space_degrees = 0
            is_space_from_facing = True
        is_space_wanted = arguments.get(ARGUMENT_SPACE) is True or space_direction != "" or space_meters > 0 or has_space_angle
        # A book to read, by its ID, and the page of it
        read = str(arguments.get(ARGUMENT_READ, "") or "").strip()
        if read != "":
            read_form_id = parse_ref_id(read)
            if read_form_id is None or read_form_id == 0:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{read}' is not the ID of a book. Take it from an inventory ('Books'), from 'Things' or from 'Events' in STATE."}, True)
            read = format_ref_id(read_form_id)
        read_page = arguments.get(ARGUMENT_PAGE)
        if not isinstance(read_page, int) or isinstance(read_page, bool) or read_page < 1:
            read_page = 1
        state_result = await queue.get_state(inventory=inventory, corpse=corpse, things=arguments.get(ARGUMENT_THINGS) is True,
                                             max_lines=max_lines, around_companion=around in AROUND_COMPANION_WORDS,
                                             stats=arguments.get(ARGUMENT_STATS) is True,
                                             visible=arguments.get(ARGUMENT_VISIBLE) is True,
                                             space=is_space_wanted, find=find, space_meters=space_meters,
                                             space_degrees=space_degrees, space_from_facing=is_space_from_facing,
                                             space_angle=space_angle, space_direction=space_direction,
                                             inventory_filter=inventory_filter, read=read, read_page=read_page, merchant=merchant)
        shown_state = state_result.get("state")
        shown_merchant = shown_state.get("merchant") if isinstance(shown_state, dict) and state_result.get("merchant_wanted") else None
        if isinstance(shown_merchant, dict) and shown_merchant.get("status") != MERCHANT_IS_NONE:
            queue.last_merchant = str(shown_merchant.get("ref_id", ""))
        if queue.chain is not None:
            state_result["queue"] = queue.chain.describe()
        if arguments.get(ARGUMENT_DEBUG) is True:
            return _tool_json(state_result, False)
        return _tool_text(describe_state(state_result), False)

    if name == TOOL_COMMAND:
        return await _call_command(arguments, queue, voice)

    return _tool_json({"status": consts.STATUS_FAILED, "message": f"Unknown tool '{name}'."}, True)


async def _run_command(arguments: dict[str, Any], queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> tuple[dict[str, Any], bool]:
    """Checks one command of the AI, sends it to the game and returns what the game answered and whether that is an error"""
    action = str(arguments.get("action", "")).strip().lower()
    target = str(arguments.get("target", "") or "").strip()
    detail = str(arguments.get("detail", "") or "").strip()
    if action == consts.ACTION_UNFAVORITE:
        # Another name for favorite with 'off'. The game mod only knows favorite
        action = consts.ACTION_FAVORITE
        detail = consts.FAVORITE_OFF
    if action not in consts.PLAYER_ACTIONS:
        return {"status": consts.STATUS_FAILED, "message": f"Unknown action '{action}'. Allowed: {', '.join(consts.PLAYER_ACTIONS)}"}, True
    if action == consts.ACTION_PAUSE:
        return {"status": consts.STATUS_FAILED, "message": "pause only waits between commands that follow each other (see 'then' and 'after_previous')."}, True
    if action in consts.TRADE_ACTIONS:
        return await _run_trade(action, target, detail, queue)
    if action == consts.ACTION_PICK_UP:
        # Several things, or everything around. A single thing goes the usual way further down
        picked_up = await _run_pick_up(target, detail, queue)
        if picked_up is not None:
            return picked_up
    # give with 'junk' names no item. take with it is told further down that only give knows it
    is_junk_wanted = action in consts.ACTIONS_WITH_COUNT and detail.lower() == consts.HAND_OVER_JUNK
    if action in consts.ACTIONS_REQUIRING_TARGET and target == "" and not is_junk_wanted:
        return {"status": consts.STATUS_FAILED, "message": f"Action '{action}' needs a target (ref_id from STATE)."}, True
    if action in consts.ACTIONS_WITH_COUNT:
        # Several items in one command, or everything that is junk
        problem, hand_over, note = await _read_hand_over(action, target, detail, queue)
        if problem != "":
            return {"status": consts.STATUS_FAILED, "message": problem}, True
        if len(hand_over) > 0:
            return await _hand_over(action, hand_over, note, queue)
    if action == consts.ACTION_SIT and target == "":
        target, seat_problem = await _find_nearest_seat(queue)
        if seat_problem != "":
            return {"status": consts.STATUS_FAILED, "message": seat_problem}, True
        if target == "":
            return {"status": consts.STATUS_FAILED, "message": "The game shows no seat within about 10 meters of you. Go closer to one, or name a seat from 'Things' in STATE as the target."}, True
    if target != "":
        target_form_id = parse_ref_id(target)
        if target_form_id is None:
            return {"status": consts.STATUS_FAILED, "message": f"'{target}' is not a ref_id or item ID. Use one from STATE, eg 000A2C94."}, True
        target = format_ref_id(target_form_id)
    count = 1
    if action in (consts.ACTION_MOVE_TO, consts.ACTION_LEAD):
        # How far in front of the target to stop. Without a number the game mod decides
        count = consts.MOVE_TO_USUAL_DISTANCE
        words = detail.lower().split()
        if len(words) > 1 and words[-1] in ("m", "meter", "meters"):
            words.pop()
        if len(words) > 0:
            number = words[0].removesuffix("m")
            if len(words) != 1 or not number.isdecimal() or int(number) > consts.MOVE_TO_HIGHEST_KEEP_METERS:
                return {"status": consts.STATUS_FAILED, "message": f"For {action} 'detail' is how many meters in front of the target you stop: a number from 0 to {consts.MOVE_TO_HIGHEST_KEEP_METERS}, or nothing."}, True
            count = int(number)
            detail = number
    if action == consts.ACTION_FAVORITE:
        detail = detail.lower()
        if detail not in ("", consts.FAVORITE_ON, consts.FAVORITE_OFF):
            return {"status": consts.STATUS_FAILED, "message": f"For favorite 'detail' is '{consts.FAVORITE_ON}' (the default) or '{consts.FAVORITE_OFF}'."}, True
        count = 0 if detail == consts.FAVORITE_OFF else 1
    if action in consts.ACTIONS_WITH_COUNT and detail != "":
        if detail.lower() == consts.COUNT_ALL_TEXT:
            count = consts.COUNT_ALL
        elif detail.isdecimal() and int(detail) > 0:
            count = int(detail)
        else:
            return {"status": consts.STATUS_FAILED, "message": f"For {action} 'detail' is how many: a number or 'all'."}, True
    item = ""
    gesture_event = ""
    extra: dict[str, Any] = {}
    if action == consts.ACTION_LOOT and detail != "" and detail.lower() != consts.COUNT_ALL_TEXT:
        item_form_id = parse_ref_id(detail)
        if item_form_id is None or item_form_id == 0:
            return {"status": consts.STATUS_FAILED, "message": "For loot 'detail' is the ID of the item to take, or 'all' for everything."}, True
        item = format_ref_id(item_form_id)
    if action == consts.ACTION_AUTO_HEAL:
        target = ""
        detail = detail.lower().removesuffix("%").strip()
        count = 0
        if detail.isdecimal() and consts.AUTO_HEAL_LOWEST_PERCENT <= int(detail) <= consts.AUTO_HEAL_HIGHEST_PERCENT:
            # Below how many percent of health. That turns it on as well
            count = int(detail)
            detail = consts.AUTO_HEAL_ON
        elif detail not in (consts.AUTO_HEAL_ON, consts.AUTO_HEAL_OFF):
            return {"status": consts.STATUS_FAILED, "message": f"auto_heal needs '{consts.AUTO_HEAL_ON}', '{consts.AUTO_HEAL_OFF}' or a number from "
                               f"{consts.AUTO_HEAL_LOWEST_PERCENT} to {consts.AUTO_HEAL_HIGHEST_PERCENT} in 'detail' (heal below that many percent of health)."}, True
    if action == consts.ACTION_SAY:
        is_loud = arguments.get("loud") is True
        is_quiet = arguments.get("quiet") is True
        if is_loud and is_quiet:
            return {"status": consts.STATUS_FAILED, "message": "say takes 'loud' or 'quiet', not both."}, True
        manner = str(arguments.get("manner", "") or "").strip().lower()
        if manner != "" and manner not in consts.MANNERS:
            return {"status": consts.STATUS_FAILED, "message": f"There is no manner '{manner}'. The manners: {MANNER_NAMES}. Nothing = neutral."}, True
        problem, line = await _prepare_speech(detail, queue, voice, is_loud, is_quiet)
        if problem != "":
            return {"status": consts.STATUS_FAILED, "message": problem}, True
        # The game gets the number of the line to speak, the sound is in its folder already.
        # The text goes along for the subtitle, which would only show the "..." of the line otherwise
        # The target is whom the companion speaks to: the game mod makes it look there while it speaks.
        # Hands that move while talking: the game mod plays the animations the server picks, the first one with say
        seconds = voice.spoken_seconds if voice is not None else 0.0
        animations = _pick_talk_animations(manner, seconds) if voice is not None and voice.has_talk_gestures else []
        # In a low voice the hands stay still, unless the AI names how it speaks
        if is_quiet and manner == "":
            animations = []
        face_name = consts.MANNERS[manner][1] if manner != "" else ""
        talk, idle = _split_animation(animations[0]) if len(animations) > 0 else ("", "")
        # How loud it is for the game: enemies can hear what is said normally and, further away, what is called out
        say_extra: dict[str, Any] = {}
        # Whom the companion speaks to looks at it, with a face that fits how it speaks
        if target != "" and manner in consts.LISTENER_FACES:
            say_extra[consts.KEY_COMMAND_LISTENER_FACE] = consts.EXPRESSIONS[consts.LISTENER_FACES[manner]]
        if is_quiet:
            say_extra[consts.KEY_COMMAND_QUIET] = 1
        if is_loud:
            say_extra[consts.KEY_COMMAND_LOUD] = 1
        global _say_number, _speech_task, _last_say_seconds
        _last_say_seconds = seconds
        _say_number += 1
        number = _say_number
        # The sentence before this one is cut off by the new one, so nothing of it is to follow any more
        if _speech_task is not None:
            _speech_task.cancel()
            _speech_task = None
        result = await queue.send_command(action, target=target, detail=clean_text(detail), count=line, talk=talk, item=idle,
                                          say_number=number, face=consts.EXPRESSIONS.get(face_name, consts.FACE_NONE),
                                          say_ms=int(seconds * 1000), extra=say_extra if len(say_extra) > 0 else None)
        is_said = result["status"] in (consts.STATUS_DONE, consts.STATUS_DELIVERED)
        state = await queue.peek_state()
        protocol = state.get("protocol") if isinstance(state, dict) else 0
        if is_said and isinstance(protocol, int) and protocol >= consts.SAY_END_PROTOCOL:
            _speech_task = asyncio.create_task(_finish_speech(queue, number, seconds, animations[1:]))
        return result, not is_said
    if action == consts.ACTION_EXPRESSION:
        # The game gets the number of the look and how strong it is, the name only goes along for its answer
        target = ""
        words = detail.lower().split()
        if len(words) == 0:
            return {"status": consts.STATUS_FAILED, "message": f"expression needs the look in 'detail' ('{consts.EXPRESSION_NONE_TEXTS[0]}' gives you your own face back). The looks: {EXPRESSION_NAMES}"}, True
        detail = words[0]
        if detail in consts.EXPRESSION_NONE_TEXTS:
            count = consts.EXPRESSION_NONE
        elif detail in consts.EXPRESSIONS:
            count = consts.EXPRESSIONS[detail]
            strength = consts.EXPRESSION_FULL_STRENGTH
            if len(words) > 1:
                if not words[1].isdecimal() or not 1 <= int(words[1]) <= consts.EXPRESSION_FULL_STRENGTH:
                    return {"status": consts.STATUS_FAILED, "message": "What stands behind the look is how strong it is: a number from 1 to 100."}, True
                strength = int(words[1])
            item = format_ref_id(strength)
        else:
            return {"status": consts.STATUS_FAILED, "message": f"There is no look '{detail}'. The looks: {EXPRESSION_NAMES}"}, True
    if action == consts.ACTION_FACE:
        # With a target the game works out the direction. Without one it gets the point of the compass in degrees
        count = consts.FACE_NO_DIRECTION
        if target == "":
            word = _direction_word(detail.split())
            word = consts.COMPASS_OTHER_WORDS.get(word, word)
            if word not in consts.COMPASS_NAMES:
                return {"status": consts.STATUS_FAILED, "message": f"face needs a target (a ref_id from STATE) or a point of the compass in 'detail': {COMPASS_NAMES}"}, True
            count = consts.COMPASS_NAMES.index(word) * consts.DEGREES_PER_DIRECTION
            detail = word
        else:
            detail = ""
    if action == consts.ACTION_MOVE:
        # The game gets the direction in degrees, whether it counts from where the companion faces, and how far
        target = ""
        words = detail.split()
        meters = consts.MOVE_DEFAULT_METERS
        # "10", "10m" and "10 m" all mean ten meters
        if len(words) > 1 and words[-1].lower() in ("m", "meter", "meters"):
            words.pop()
        if len(words) > 0 and words[-1].lower().removesuffix("m").isdecimal():
            meters = int(words.pop().lower().removesuffix("m"))
            if not 1 <= meters <= consts.MOVE_HIGHEST_METERS:
                return {"status": consts.STATUS_FAILED, "message": f"What stands behind the direction is how far in meters: a number from 1 to {consts.MOVE_HIGHEST_METERS}."}, True
        word = _direction_word(words)
        compass_word = consts.COMPASS_OTHER_WORDS.get(word, word)
        relative_word = consts.RELATIVE_OTHER_WORDS.get(word, word)
        if compass_word in consts.COMPASS_NAMES:
            count = consts.COMPASS_NAMES.index(compass_word) * consts.DEGREES_PER_DIRECTION
            detail = "to the " + compass_word
            item = format_ref_id(meters)
        elif relative_word in consts.RELATIVE_NAMES:
            count = consts.RELATIVE_NAMES.index(relative_word) * consts.DEGREES_PER_DIRECTION
            detail = consts.RELATIVE_PHRASES[consts.RELATIVE_NAMES.index(relative_word)]
            item = format_ref_id((1 << 16) | meters)
        else:
            return {"status": consts.STATUS_FAILED, "message": f"move needs a direction in 'detail', and behind it the meters if you like: a point of the compass ({COMPASS_NAMES}) or seen from where you face ({RELATIVE_NAMES})."}, True
    if action == consts.ACTION_MAKE_ROOM:
        # The game gets the number of the direction and how far, the name only goes along for its answer
        target = ""
        words = detail.lower().split()
        detail = words[0] if len(words) > 0 else consts.ROOM_DIRECTION_BACK
        if detail not in consts.ROOM_DIRECTIONS:
            return {"status": consts.STATUS_FAILED, "message": f"There is no direction '{detail}' to make room in. The directions: {ROOM_DIRECTION_NAMES}"}, True
        count = consts.ROOM_DIRECTIONS[detail]
        meters = consts.ROOM_DEFAULT_METERS
        if len(words) > 1:
            if not words[1].isdecimal() or not 1 <= int(words[1]) <= consts.ROOM_HIGHEST_METERS:
                return {"status": consts.STATUS_FAILED, "message": f"What stands behind the direction is how far in meters: a number from 1 to {consts.ROOM_HIGHEST_METERS}."}, True
            meters = int(words[1])
        item = format_ref_id(meters)
    if action == consts.ACTION_GESTURE:
        # The game gets the ID of the animation, the name only goes along for its answer
        target = ""
        detail = detail.lower().replace(" ", "_")
        if detail == "":
            return {"status": consts.STATUS_FAILED, "message": f"gesture needs the name of the gesture in 'detail' ('{consts.GESTURE_STOP_TEXT}' ends one). The gestures: {GESTURE_NAMES}"}, True
        if detail != consts.GESTURE_STOP_TEXT:
            gesture = pick_gesture(detail)
            if gesture is not None:
                # A gesture with a short name: the game mod picks the animation for sitting if the companion sits, and
                # if the game refuses to play the animation by its ID, the game mod sends the event of the animation instead
                item = gesture["id"]
                gesture_event = gesture["event"]
                sit_high, sit_low = split_form_id(parse_ref_id(gesture["sit_id"]) or 0)
                extra = {consts.KEY_COMMAND_STOP_EVENT: gesture["stop_event"], consts.KEY_COMMAND_SIT_ITEM_HIGH: sit_high,
                         consts.KEY_COMMAND_SIT_ITEM_LOW: sit_low, consts.KEY_COMMAND_SIT_EVENT: gesture["sit_event"],
                         consts.KEY_COMMAND_SIT_STOP_EVENT: gesture["sit_stop_event"],
                         consts.KEY_COMMAND_GESTURE_STATE: 1 if gesture["state"] != "" else 0}
            else:
                # Only the gestures with a short name are played: the other animations of the game mostly did nothing
                # when they were tried, and the game said that it played them all the same
                return {"status": consts.STATUS_FAILED, "message": f"There is no gesture '{detail}'. The gestures: {GESTURE_NAMES}."}, True
    if action == consts.ACTION_CAST_SPELL:
        if detail == "":
            return {"status": consts.STATUS_FAILED, "message": "cast_spell needs the ID or the name of the spell in 'detail'. STATE with inventory 'own' lists the spells you know."}, True
        # Eight hex digits are the ID of the spell from STATE, anything else is its name
        # A number behind the spell is for how many seconds a spell that is held is held
        words = detail.split()
        if len(words) > 1 and words[-1].isdecimal():
            seconds = int(words[-1])
            if not 1 <= seconds <= consts.CAST_HOLD_HIGHEST_SECONDS:
                return {"status": consts.STATUS_FAILED, "message": f"The number behind the spell is for how many seconds you hold it: 1 to {consts.CAST_HOLD_HIGHEST_SECONDS}."}, True
            extra[consts.KEY_COMMAND_SECONDS] = seconds
            detail = " ".join(words[:-1])
        spell_form_id = parse_ref_id(detail) if len(detail) == SPELL_ID_LENGTH else None
        if spell_form_id is not None and spell_form_id != 0:
            item = format_ref_id(spell_form_id)
    if action == consts.ACTION_RIDE_LOCK:
        target = ""
        detail = detail.lower()
        if detail not in (consts.RIDE_LOCK_ON, consts.RIDE_LOCK_OFF):
            return {"status": consts.STATUS_FAILED, "message": f"ride_lock needs '{consts.RIDE_LOCK_ON}' or '{consts.RIDE_LOCK_OFF}' in 'detail': "
                               f"'{consts.RIDE_LOCK_ON}' = you no longer mount and dismount when the player does, '{consts.RIDE_LOCK_OFF}' = you do again."}, True
    if action == consts.ACTION_DISMOUNT:
        target = ""
    if action == consts.ACTION_SNEAK:
        target = ""
        detail = detail.lower()
        if detail not in consts.SNEAK_CHOICES:
            return {"status": consts.STATUS_FAILED, "message": f"sneak needs one of these in 'detail': {SNEAK_NAMES}. "
                               f"'{consts.SNEAK_ON}' = you sneak whatever the player does, '{consts.SNEAK_OFF}' = you walk upright whatever the player does, "
                               f"'{consts.SNEAK_AUTO}' = you sneak whenever the player sneaks again."}, True
    if action == consts.ACTION_PASSIVE:
        target = ""
        detail = detail.lower()
        if detail not in (consts.PASSIVE_ON, consts.PASSIVE_OFF):
            return {"status": consts.STATUS_FAILED, "message": f"passive needs '{consts.PASSIVE_ON}' or '{consts.PASSIVE_OFF}' in 'detail': "
                               f"'{consts.PASSIVE_ON}' = you start no fight of your own accord, '{consts.PASSIVE_OFF}' = you attack enemies as always."}, True
    if action == consts.ACTION_RENAME_HORSE:
        # One line without the breaks and double blanks a name does not have
        detail = " ".join(detail.split())
        if detail == "" or len(detail) > consts.HORSE_NAME_MAX_LENGTH:
            return {"status": consts.STATUS_FAILED, "message": f"rename_horse needs the new name in 'detail', at most {consts.HORSE_NAME_MAX_LENGTH} characters."}, True
    if action == consts.ACTION_CALL_HORSE:
        detail = detail.lower()
        if detail not in ("", consts.CALL_TO_PLAYER, consts.CALL_TO_COMPANION):
            return {"status": consts.STATUS_FAILED, "message": f"For call_horse 'detail' is whom the horse runs to: '{consts.CALL_TO_PLAYER}' or '{consts.CALL_TO_COMPANION}'. Nothing = to its owner."}, True
    is_run = arguments.get("run") is True and action in consts.ACTIONS_WITH_RUN
    result = await queue.send_command(action, target, detail, count=count, item=item, run=is_run, talk=gesture_event, extra=extra)
    is_error = result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)
    return result, is_error


def _list_differences(quick: Any, slow: Any, path: str = "") -> list[str]:
    """Where two states differ, one line per place: its path, what the state built in one go has and what the other has"""
    if isinstance(quick, dict) and isinstance(slow, dict):
        lines: list[str] = []
        for key in sorted(set(quick) | set(slow), key=str):
            if path == "" and key in COMPARE_SKIPPED_KEYS:
                continue
            where = f"{path}.{key}" if path != "" else str(key)
            if key not in quick:
                lines.append(f"{where}: only the old way has it: {shorten(json.dumps(slow[key], ensure_ascii=False))}")
            elif key not in slow:
                lines.append(f"{where}: only in one go has it: {shorten(json.dumps(quick[key], ensure_ascii=False))}")
            else:
                lines.extend(_list_differences(quick[key], slow[key], where))
        return lines
    if isinstance(quick, list) and isinstance(slow, list) and len(quick) == len(slow):
        lines = []
        for place, (quick_entry, slow_entry) in enumerate(zip(quick, slow)):
            lines.extend(_list_differences(quick_entry, slow_entry, f"{path}[{place}]"))
        return lines
    if quick != slow:
        return [f"{path}: in one go {shorten(json.dumps(quick, ensure_ascii=False))} / the old way {shorten(json.dumps(slow, ensure_ascii=False))}"]
    return []


async def _compare_states(queue: CompanionCommandQueue) -> tuple[str, bool]:
    """STATE with 'compare': asks the game for its state twice, built in one go by the plugin of the game mod and built
    the old way, question by question, and lists where the two differ. Nothing here can check the plugin, only the
    game can. Returns the text and whether it is an error. What was heard and what happened is not used up"""
    quick_result = await queue.get_state(is_for_the_ai=False)
    quick_state = quick_result.get("state")
    if not quick_result.get("fresh") or not isinstance(quick_state, dict):
        return "STATE compare: the game sent no fresh state, so there is nothing to compare. Is it running, with no menu open?", True
    if quick_state.get("quick") is not True:
        return ("STATE compare: the game mod built its state the old way. Its plugin (KI_MCP_Gefaehrte.dll) is older than interface version 24, "
                "or the game mod switched the way in one go off after it failed (until the game is loaded again). Nothing to compare."), True
    slow_result = await queue.get_state(is_for_the_ai=False, slow=True)
    slow_state = slow_result.get("state")
    if not slow_result.get("fresh") or not isinstance(slow_state, dict):
        return "STATE compare: the game sent the state built in one go, but then no second one built the old way. Try again.", True
    if slow_state.get("quick") is True:
        return "STATE compare: the game mod was asked for the old way and built the state in one go again. It is older than this server.", True
    differences = _list_differences(quick_state, slow_state)
    head = (f"STATE compare: in one go it took the script {quick_state.get('build_ms', '?')} ms (of that the plugin {quick_state.get('scan_ms', '?')} ms), "
            f"the old way {slow_state.get('build_ms', '?')} ms.")
    if len(differences) == 0:
        log.info("%s Both ways tell the same.", head)
        return head + " Both ways tell the same.", False
    log.info("%s %d differences:", head, len(differences))
    for line in differences:
        log.info("STATE compare: %s", line)
    return (head + f" {len(differences)} differences (what moved in between differs by itself: positions, distances, the time):\n"
            + "\n".join("- " + line for line in differences)), False


def _read_pick_up_all(detail: str) -> tuple[str, int, str] | None:
    """pick_up with 'all' in 'detail': within how many meters and which kind. Returns what is wrong as text for the AI
    (empty = nothing), the meters and the kind as the game mod knows it ("" = everything). None if 'detail' does not ask for that"""
    words = detail.lower().replace(",", " ").replace("_", " ").split()
    if len(words) == 0 or words[0] != consts.PICK_UP_ALL:
        return None
    words = words[1:]
    problem = (f"For pick_up with '{consts.PICK_UP_ALL}' 'detail' is: '{consts.PICK_UP_ALL}' (everything loose within {consts.PICK_UP_USUAL_METERS} meters), "
               f"'{consts.PICK_UP_ALL} 4' (within 4 meters, at most {consts.PICK_UP_HIGHEST_METERS}), '{consts.PICK_UP_ALL} 3 potions' (only one kind: "
               f"{', '.join(consts.PICK_UP_KINDS)}) or '{consts.PICK_UP_ALL} 3 no clutter' (everything but plates, tankards and the like).")
    meters = consts.PICK_UP_USUAL_METERS
    if len(words) > 0 and words[0].removesuffix("m").isdecimal():
        meters = int(words[0].removesuffix("m"))
        words = words[1:]
        if len(words) > 0 and words[0] in ("m", "meter", "meters"):
            words = words[1:]
        if not 1 <= meters <= consts.PICK_UP_HIGHEST_METERS:
            return problem, 0, ""
    kind = ""
    if len(words) > 0:
        kind_text = " ".join(words)
        kind = consts.PICK_UP_KIND_WORDS.get(kind_text) or consts.PICK_UP_KIND_WORDS.get(kind_text.removesuffix("s")) or ""
        if kind == "":
            return problem, 0, ""
    return "", meters, kind


async def _run_pick_up(target: str, detail: str, queue: CompanionCommandQueue) -> tuple[dict[str, Any], bool] | None:
    """pick_up with several things in 'target', or with 'all' in 'detail' for everything loose around. Returns what the
    game answered and whether that is an error, or None for a single thing: that goes the way it always went"""
    texts = target.replace(",", " ").replace(";", " ").split()
    collecting = _read_pick_up_all(detail)
    if collecting is None and len(texts) <= 1:
        return None
    ref_ids: list[str] = []
    for text in texts:
        form_id = parse_ref_id(text)
        if form_id is None or form_id == 0:
            return {"status": consts.STATUS_FAILED, "message": f"'{text}' is not a ref_id. Take the ref_ids of the things from 'Things' in STATE, "
                    "with commas between them."}, True
        if format_ref_id(form_id) not in ref_ids:
            ref_ids.append(format_ref_id(form_id))
    meters = 0
    kind = ""
    if collecting is not None:
        problem, meters, kind = collecting
        if problem != "":
            return {"status": consts.STATUS_FAILED, "message": problem}, True
        if len(ref_ids) > 1:
            return {"status": consts.STATUS_FAILED, "message": f"With '{consts.PICK_UP_ALL}' the target is the one thing to look around, or nothing "
                    f"(then around you). You named {len(ref_ids)}. To pick up just these, leave 'detail' empty."}, True
    elif len(ref_ids) > consts.PICK_UP_HIGHEST_ITEMS:
        return {"status": consts.STATUS_FAILED, "message": f"At most {consts.PICK_UP_HIGHEST_ITEMS} things at a time. You named {len(ref_ids)}."}, True
    # A game mod from before this would walk to the first thing only, or look for a thing without a ref_id
    state = await queue.peek_state()
    if isinstance(state, dict):
        protocol = state.get("protocol")
        if not isinstance(protocol, int) or protocol < consts.PICK_UP_LIST_PROTOCOL:
            return {"status": consts.STATUS_FAILED, "message": f"Several things in one go and '{consts.PICK_UP_ALL}' need a newer game mod than the "
                    "one that is running (build and install it again with build.py --install). Nothing was picked up. One thing at a time still works."}, True
    if collecting is not None:
        result = await queue.send_command(consts.ACTION_PICK_UP, target=ref_ids[0] if len(ref_ids) > 0 else "",
                                          detail=" ".join(part for part in (consts.PICK_UP_ALL, str(meters), kind) if part != ""),
                                          extra={consts.KEY_COMMAND_PICK_ALL: meters, consts.KEY_COMMAND_PICK_FILTER: kind},
                                          result_timeout_seconds=consts.HAND_OVER_TIMEOUT_SECONDS)
    else:
        numbers: list[int] = []
        for ref_id in ref_ids:
            numbers.extend(split_form_id(parse_ref_id(ref_id) or 0))
        # The first thing goes along the usual way as well. The game mod reads the list
        result = await queue.send_command(consts.ACTION_PICK_UP, target=ref_ids[0], detail=",".join(ref_ids),
                                          extra={consts.KEY_COMMAND_ITEMS: numbers}, result_timeout_seconds=consts.HAND_OVER_TIMEOUT_SECONDS)
    return result, result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)


def _is_id(text: str) -> bool:
    """Whether a text of a trade is an ID (of an item or a character) and not how many"""
    return len(text) >= consts.TRADE_ID_LENGTH and parse_ref_id(text) not in (None, 0)


def _read_counts(texts: list[str]) -> list[int] | None:
    """How many, for each text: a number above 0, or COUNT_ALL_TEXT. None if one of them is neither"""
    counts: list[int] = []
    for text in texts:
        if text.lower() == consts.COUNT_ALL_TEXT:
            counts.append(consts.COUNT_ALL)
        elif text.isdecimal() and int(text) > 0:
            counts.append(int(text))
        else:
            return None
    return counts


async def _read_trade_junk(merchant: str, queue: CompanionCommandQueue) -> tuple[str, list[tuple[str, int]]]:
    """sell with 'junk': everything the merchant buys that the companion neither wears, holds nor has as a favorite, apart
    from ammunition, potions, food and keys. Returns what is wrong as text for the AI (empty = nothing) and the items"""
    merchant_result = await queue.get_state(merchant=merchant, is_for_the_ai=False)
    state = merchant_result.get("state")
    known = state.get("merchant") if isinstance(state, dict) and merchant_result.get("merchant_wanted") else None
    if not isinstance(known, dict) or known.get("ref_id") != merchant:
        return "The game did not tell in time what this merchant buys, so nothing was sold. Try again.", []
    if known.get("status") == MERCHANT_IS_NONE:
        return f"{known.get('name')} is no merchant.", []
    if known.get("status") == MERCHANT_IS_CLOSED:
        return f"{known.get('name')} does not trade at this hour.", []
    # The merchant's list leaves out what the companion wears. Its favorites are only marked in its own inventory
    own_result = await queue.get_state(inventory=consts.INVENTORY_OWN, is_for_the_ai=False)
    own_state = own_result.get("state")
    inventory = own_state.get(KEY_INVENTORY_OWN) if isinstance(own_state, dict) and own_result.get("inventory_wanted") else None
    if not isinstance(inventory, dict):
        return "The game did not send your inventory in time, so nothing was sold. Try again.", []
    kept = {item["id"] for item in inventory["items"] if item["favorite"] or item["equipped"]}
    junk = [(str(item["id"]), consts.COUNT_ALL) for item in known.get("sellable", [])
            if item["id"] not in kept and item["group"] not in consts.JUNK_KEPT_GROUPS]
    if len(junk) == 0:
        return "You carry nothing that counts as junk and that this merchant buys.", []
    return "", junk[:consts.HAND_OVER_HIGHEST_ITEMS]


async def _run_trade(action: str, target: str, detail: str, queue: CompanionCommandQueue) -> tuple[dict[str, Any], bool]:
    """buy and sell. The usual form: the merchant as the target, the items in 'detail'. The items may also stand in
    'target' as for give and take: then the merchant is the one in 'detail', or the one the AI dealt with last"""
    is_selling = action == consts.ACTION_SELL
    usage = (f"For {action} the target is the merchant (its ref_id from 'Nearby') and 'detail' the IDs of the items with commas between them, "
             f"each with how many or '{consts.COUNT_ALL_TEXT}' behind it if not one: eg '00034CDF 2, 0003EADD'.")
    if is_selling:
        usage += (f" Or 'detail' '{consts.HAND_OVER_JUNK}': everything the merchant buys that you neither wear, hold nor have as a favorite, "
                  "apart from ammunition, potions, food and keys.")
    usage += " STATE with 'merchant' lists the IDs and the prices."

    def failed(message: str) -> tuple[dict[str, Any], bool]:
        return {"status": consts.STATUS_FAILED, "message": message}, True

    target_words = [word for word in re.split(r"[,;\s]+", target) if word != ""]
    detail_words = [word for word in re.split(r"[,;\s]+", detail) if word != ""]
    if not all(_is_id(word) for word in target_words):
        return failed(f"'{target}' is not made of IDs. {usage}")
    target_ids = [format_ref_id(parse_ref_id(word) or 0) for word in target_words]
    detail_ids = [format_ref_id(parse_ref_id(word) or 0) for word in detail_words if _is_id(word)]
    last_merchant = queue.last_merchant
    merchant = ""
    items: list[tuple[str, int]] = []
    if detail.lower() == consts.HAND_OVER_JUNK:
        if not is_selling:
            return failed(f"Only sell knows '{consts.HAND_OVER_JUNK}'. For buy, name the items.")
        if len(target_ids) > 1 or (len(target_ids) == 0 and last_merchant == ""):
            return failed(f"sell with '{consts.HAND_OVER_JUNK}' needs the merchant as the target (its ref_id from 'Nearby').")
        merchant = target_ids[0] if len(target_ids) == 1 else last_merchant
        problem, items = await _read_trade_junk(merchant, queue)
        if problem != "":
            return failed(problem)
    elif len(detail_ids) > 0 and len(target_ids) <= 1 and not (len(target_ids) == 1 and detail_ids == [last_merchant] and target_ids[0] != last_merchant):
        # The usual form: the items in 'detail', each with how many behind it if not one
        merchant = target_ids[0] if len(target_ids) == 1 else last_merchant
        if merchant == "":
            return failed(f"Action '{action}' needs a target: the merchant. {usage}")
        if not _is_id(detail_words[0]):
            return failed(f"'{detail_words[0]}' is not the ID of an item. {usage}")
        for word in detail_words:
            if _is_id(word):
                items.append((format_ref_id(parse_ref_id(word) or 0), 1))
                continue
            counts = _read_counts([word])
            if counts is None:
                return failed(f"'{word}' is neither the ID of an item nor how many (a number above 0 or '{consts.COUNT_ALL_TEXT}'). {usage}")
            items[-1] = (items[-1][0], counts[0])
    elif len(target_ids) > 0:
        # The items in 'target', as for give and take. 'detail' is the merchant, or how many
        if target_ids == [last_merchant] or (len(target_ids) == 1 and len(detail_words) == 0 and last_merchant == ""):
            return failed(f"{action} needs the items. {usage}")
        counts = [] if len(detail_ids) > 0 else _read_counts(detail_words)
        if counts is None:
            return failed(f"'{detail}' is neither IDs of items nor how many. {usage}")
        merchant = detail_ids[0] if len(detail_ids) == 1 and len(detail_words) == 1 else last_merchant
        if merchant == "":
            return failed(f"{action} needs the merchant. {usage} Once you have looked at a merchant with STATE and 'merchant', the items alone are enough.")
        if len(counts) not in (0, 1, len(target_ids)):
            return failed(f"With the items in 'target', 'detail' is the merchant, or how many of each ('1,1,3'), one number for all of them, or '{consts.COUNT_ALL_TEXT}'. {usage}")
        for place, item_id in enumerate(target_ids):
            items.append((item_id, 1 if len(counts) == 0 else counts[0] if len(counts) == 1 else counts[place]))
    else:
        return failed(f"Action '{action}' needs a target: the merchant. {usage}")

    if len(items) > consts.HAND_OVER_HIGHEST_ITEMS:
        return failed(f"At most {consts.HAND_OVER_HIGHEST_ITEMS} kinds of items at a time. You named {len(items)}.")
    first_id, first_count = items[0]
    if len(items) == 1:
        result = await queue.send_command(action, target=merchant, detail=first_id, count=first_count, item=first_id)
    else:
        state = await queue.peek_state()
        protocol = state.get("protocol") if isinstance(state, dict) else 0
        if not isinstance(protocol, int) or protocol < consts.TRADE_LIST_PROTOCOL:
            return failed("Several items in one go need a newer game mod than the one that is running (build and install it again with "
                          "build.py --install). Nothing was traded. One item at a time still works.")
        numbers: list[int] = []
        for item_id, count in items:
            high, low = split_form_id(parse_ref_id(item_id) or 0)
            numbers.extend([high, low, count])
        result = await queue.send_command(action, target=merchant, detail=",".join(item_id for item_id, _ in items), count=first_count, item=first_id,
                                          extra={consts.KEY_COMMAND_ITEMS: numbers}, result_timeout_seconds=consts.HAND_OVER_TIMEOUT_SECONDS)
    is_error = result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)
    if not is_error:
        queue.last_merchant = merchant
    return result, is_error


async def _read_hand_over(action: str, target: str, detail: str, queue: CompanionCommandQueue) -> tuple[str, list[tuple[str, int]], str]:
    """Reads a give or take that names several items, or give with 'junk'

    Returns:
        What is wrong with it as text for the AI (empty = nothing), the items as (ID, how many; COUNT_ALL = all),
        and a sentence to add to the answer. No items and no problem: the command names one item, as always
    """
    if detail.lower() == consts.HAND_OVER_JUNK:
        if action != consts.ACTION_GIVE:
            return f"Only give knows '{consts.HAND_OVER_JUNK}'. For take, name the items.", [], ""
        # The server works out itself what is junk, from the inventory of the companion
        state_result = await queue.get_state(inventory=consts.INVENTORY_OWN, is_for_the_ai=False)
        state = state_result.get("state")
        inventory = state.get(KEY_INVENTORY_OWN) if isinstance(state, dict) and state_result.get("inventory_wanted") else None
        if not isinstance(inventory, dict):
            return "The game did not send your inventory in time, so nothing was handed over. Try again.", [], ""
        junk = [(str(item["id"]), consts.COUNT_ALL) for item in inventory["items"]
                if not item["equipped"] and not item["favorite"] and item["group"] not in consts.JUNK_KEPT_GROUPS]
        if len(junk) == 0:
            return "You carry nothing that counts as junk: everything is worn, held, a favorite, ammunition, a potion, food or a key.", [], ""
        note = ""
        if len(junk) > consts.HAND_OVER_HIGHEST_ITEMS or inventory.get("total", 0) > len(inventory["items"]):
            note = "That was not all of it: you carry more kinds of items than fit into one go. Give 'junk' once more."
        return "", junk[:consts.HAND_OVER_HIGHEST_ITEMS], note

    ids = [part for part in re.split(r"[,;\s]+", target) if part != ""]
    if len(ids) < 2:
        return "", [], ""
    counts = [part.lower() for part in re.split(r"[,;\s]+", detail) if part != ""]
    if len(counts) not in (0, 1, len(ids)):
        return (f"You named {len(ids)} items and {len(counts)} numbers. 'detail' is how many of each ('1,1,3'), one number for "
                f"all of them, '{consts.COUNT_ALL_TEXT}', or nothing for one of each."), [], ""
    items: list[tuple[str, int]] = []
    for place, id_text in enumerate(ids):
        form_id = parse_ref_id(id_text)
        if form_id is None or form_id == 0:
            return f"'{id_text}' is not the ID of an item. Take the IDs from an inventory in STATE.", [], ""
        count_text = "1" if len(counts) == 0 else counts[0] if len(counts) == 1 else counts[place]
        if count_text == consts.COUNT_ALL_TEXT:
            count = consts.COUNT_ALL
        elif count_text.isdecimal() and int(count_text) > 0:
            count = int(count_text)
        else:
            return f"'{count_text}' is not how many: a number above 0 or '{consts.COUNT_ALL_TEXT}'.", [], ""
        # An item that is named twice changes hands once
        if format_ref_id(form_id) not in [item_id for item_id, _ in items]:
            items.append((format_ref_id(form_id), count))
    if len(items) > consts.HAND_OVER_HIGHEST_ITEMS:
        return f"At most {consts.HAND_OVER_HIGHEST_ITEMS} kinds of items at a time. You named {len(items)}.", [], ""
    return "", items, ""


async def _hand_over(action: str, items: list[tuple[str, int]], note: str, queue: CompanionCommandQueue) -> tuple[dict[str, Any], bool]:
    """Sends a give or take with several items to the game and returns what it answered and whether that is an error"""
    state = await queue.peek_state()
    protocol = state.get("protocol") if isinstance(state, dict) else 0
    if not isinstance(protocol, int) or protocol < consts.HAND_OVER_PROTOCOL:
        return {"status": consts.STATUS_FAILED, "message": "Several items in one go need a newer game mod than the one that is running "
                "(build and install it again with build.py --install). Nothing was handed over. One item at a time still works."}, True
    numbers: list[int] = []
    for item_id, count in items:
        high, low = split_form_id(parse_ref_id(item_id) or 0)
        numbers.extend([high, low, count])
    # The first item goes along the usual way as well. The game mod reads the list
    result = await queue.send_command(action, target=items[0][0], detail=",".join(item_id for item_id, _ in items), count=items[0][1],
                                      extra={consts.KEY_COMMAND_ITEMS: numbers}, result_timeout_seconds=consts.HAND_OVER_TIMEOUT_SECONDS)
    if note != "":
        result = dict(result)
        result["message"] = (str(result.get("message", "")) + " " + note).strip()
    return result, result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)


def _chain_of(queue: CompanionCommandQueue, voice: CompanionVoice | None) -> CompanionChain:
    """The chain of commands that wait for each other, made when it is needed for the first time"""
    if queue.chain is None:
        async def run_step(step: dict[str, Any]) -> tuple[dict[str, Any], bool, float]:
            global _last_say_seconds
            _last_say_seconds = 0.0
            payload, is_error = await _run_command(step, queue, voice)
            is_spoken = str(step.get("action", "")).strip().lower() == consts.ACTION_SAY and not is_error
            return payload, is_error, _last_say_seconds if is_spoken else 0.0
        queue.chain = CompanionChain(queue, run_step)
    return queue.chain


def _check_step(step: Any) -> str:
    """What is wrong with a command that is to wait in the chain, as far as that shows without the game. Empty = nothing"""
    if not isinstance(step, dict):
        return "is not a command with an 'action'"
    action = str(step.get("action", "")).strip().lower()
    if action not in consts.PLAYER_ACTIONS:
        return f"has the unknown action '{action}'"
    if action == consts.ACTION_UNFAVORITE:
        action = consts.ACTION_FAVORITE
    is_junk_wanted = action in consts.ACTIONS_WITH_COUNT and str(step.get("detail", "") or "").strip().lower() == consts.HAND_OVER_JUNK
    # pick_up with 'all' collects around the companion if no target is named
    is_collecting = action == consts.ACTION_PICK_UP and _read_pick_up_all(str(step.get("detail", "") or "")) is not None
    if action in consts.ACTIONS_REQUIRING_TARGET and str(step.get("target", "") or "").strip() == "" and not is_junk_wanted and not is_collecting:
        return f"needs a target for '{action}' (ref_id from STATE)"
    if action == consts.ACTION_PAUSE and read_pause_seconds(step) is None:
        return "needs the seconds of the pause in 'detail': a number from 1 to 60"
    return ""


async def _call_command(arguments: dict[str, Any], queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> dict[str, Any]:
    """One call of COMMAND: the command itself, and with 'then' and 'after_previous' the commands that wait for each other"""
    global _last_say_seconds
    chain = _chain_of(queue, voice)
    then = arguments.get(ARGUMENT_THEN)
    steps: list[dict[str, Any]] = []
    if then is not None:
        if not isinstance(then, list) or len(then) > MAX_WAITING_STEPS:
            return _tool_json({"status": consts.STATUS_FAILED, "message": f"'then' is a list of at most {MAX_WAITING_STEPS} commands, each with an 'action'."}, True)
        for number, step in enumerate(then, start=1):
            problem = _check_step(step)
            if problem != "":
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"Step {number} of 'then' {problem}. Nothing was started."}, True)
            steps.append({key: step[key] for key in STEP_FIELDS if key in step})
    first = {key: value for key, value in arguments.items() if key not in (ARGUMENT_THEN, ARGUMENT_AFTER_PREVIOUS)}
    action = str(first.get("action", "")).strip().lower()
    # A pause as the command itself can only mean: behind what runs now
    is_after_previous = arguments.get(ARGUMENT_AFTER_PREVIOUS) is True or action == consts.ACTION_PAUSE
    if is_after_previous or len(steps) > 0:
        problem = _check_step(first)
        if problem != "":
            return _tool_json({"status": consts.STATUS_FAILED, "message": f"The command {problem}. Nothing was started."}, True)
        if not queue.is_busy_known:
            return _tool_json({"status": consts.STATUS_FAILED, "message": "Commands can only wait for each other with a game mod that tells when your character is done with something. The game is not connected, or its mod is older than that: build and install the mod again."}, True)
        waiting_count = chain.waiting_count if chain.is_active else 0
        if waiting_count + len(steps) + (1 if is_after_previous else 0) > MAX_WAITING_STEPS:
            return _tool_json({"status": consts.STATUS_FAILED, "message": f"At most {MAX_WAITING_STEPS} commands can wait. Waiting already: {chain.describe()}. Nothing was added."}, True)
    if is_after_previous:
        chain.add([first] + steps)
        return _tool_json({"status": consts.STATUS_QUEUED, "message": f"It waits its turn. Queue: {chain.describe()}."}, False)

    notes: list[str] = []
    if action in consts.PLAYER_ACTIONS and action not in consts.ACTIONS_KEEPING_QUEUE:
        dropped = await chain.clear()
        if len(dropped) > 0:
            notes.append("This threw away what waited in the queue: " + ", ".join(dropped) + ".")
    fails_before = queue.fail_count
    _last_say_seconds = 0.0
    payload, is_error = await _run_command(first, queue, voice)
    if len(steps) > 0:
        if is_error:
            # What failed is skipped, the rest goes on once the companion is done with what it was doing
            chain.add(steps, fails_before=queue.fail_count, can_running_fail=False)
            notes.append(f"It was skipped, what you put behind it goes on. Queue: {chain.describe()}.")
        else:
            chain.add(steps, running_text=describe_step(first), fails_before=fails_before,
                      first_wait_seconds=_last_say_seconds if action == consts.ACTION_SAY else 0.0,
                      can_running_fail=action not in consts.ACTIONS_KEEPING_QUEUE)
            notes.append(f"Queue: {chain.describe()}.")
    # What cannot wait until the next STATE: a fight that began, someone who died (see 'Events' of STATE)
    urgent_events = await queue.take_urgent_events()
    if len(urgent_events) > 0:
        notes.append("Meanwhile: " + " ".join(f"{event['text']} ({event['seconds_ago']} s ago)" for event in urgent_events))
    if len(notes) > 0:
        payload = dict(payload)
        payload["message"] = " ".join([str(payload.get("message", ""))] + notes).strip()
    return _tool_json(payload, is_error)


def _describe_found_gestures(word: str) -> str:
    """The answer to find_gesture: the gestures whose name has the word. Only these can be played (see gestures.py)"""
    wanted = word.lower().replace(" ", "_")
    names = [name for name in ALL_GESTURE_NAMES if wanted in name]
    if len(names) == 0:
        return f"No gesture has '{word}' in its name. The gestures: {GESTURE_NAMES}"
    return f"Gestures for '{word}': {', '.join(names)}. Play one with the command gesture and its name in 'detail'."


def _direction_word(words: list[str]) -> str:
    """The words the AI wrote for a direction as one word the way the lists of directions have it: 'North East' -> 'north-east'"""
    return "-".join(" ".join(words).lower().replace("_", " ").replace("-", " ").split())


def _pick_talk_animations(manner: str, seconds: float) -> list[str]:
    """Picks the animations for the hands of the companion for one sentence

    Args:
        manner: How the companion speaks (a name from MANNERS), or empty for neutral
        seconds: How long the sound of the sentence is

    Returns:
        The animations in the order they are played: the first one with say, the others TALK_GESTURE_SECONDS apart.
        Never the same one twice in a row, also not from one sentence to the next. Empty if the manner has none
    """
    global _last_talk_animation
    pool = consts.MANNERS[manner][0] if manner != "" else consts.TALK_ANIMATIONS
    if len(pool) == 0:
        return []
    count = 1
    while count * consts.TALK_GESTURE_SECONDS + consts.TALK_GESTURE_LEAST_SECONDS <= seconds:
        count += 1
    picked: list[str] = []
    for _ in range(count):
        choices = [animation for animation in pool if animation != _last_talk_animation] or list(pool)
        _last_talk_animation = random.choice(choices)
        picked.append(_last_talk_animation)
    return picked


def _split_animation(animation: str) -> tuple[str, str]:
    """An animation as the game mod wants it: the name of an animation event, or the ID of an animation. One of the two is empty"""
    if len(animation) == 8 and parse_ref_id(animation) is not None:
        return "", animation
    return animation, ""


async def _finish_speech(queue: CompanionCommandQueue, number: int, seconds: float, later_animations: list[str]) -> None:
    """Runs beside a sentence the companion speaks: sends the further animations of a long sentence one after the other,
    and tells the game mod when the sound is over, so that the companion's eyes, face and stance go back to what they were

    Args:
        number: The number of the sentence. The game mod ignores what comes for an older sentence
        seconds: How long the sound of the sentence is
        later_animations: The animations after the first one, which went along with say
    """
    start = time.monotonic()
    for index, animation in enumerate(later_animations, start=1):
        await asyncio.sleep(max(index * consts.TALK_GESTURE_SECONDS - (time.monotonic() - start), 0.0))
        talk, idle = _split_animation(animation)
        await queue.send_command(consts.ACTION_SAY_TALK, talk=talk, item=idle, say_number=number)
    await asyncio.sleep(max(seconds + consts.SAY_END_EXTRA_SECONDS - (time.monotonic() - start), 0.0))
    await queue.send_command(consts.ACTION_SAY_END, say_number=number)


async def _prepare_speech(text: str, queue: CompanionCommandQueue, voice: CompanionVoice | None, is_loud: bool = False,
                          is_quiet: bool = False) -> tuple[str, int]:
    """Makes the sound of what the companion says and puts it into the game folder (see voice.py)

    Returns:
        Why that did not work as text for the AI (empty if it worked), and the number of the line the game has to speak
    """
    if voice is None:
        return "This server was started without a voice, so you cannot speak in the game.", 0
    if text == "":
        return "say needs the text to speak in 'detail'.", 0
    if len(text) > MAX_TEXT_LENGTH:
        return f"That is too long to say in one go ({len(text)} characters, at most {MAX_TEXT_LENGTH}). Say it in parts.", 0
    state = await queue.peek_state()
    companion = state.get("companion") if isinstance(state, dict) else None
    if not isinstance(companion, dict):
        return "The game has not told who your character is yet. Get STATE first. Without a follower of the player you have no voice.", 0
    # The plugin of the game mod tells the voice. For an older one, Mantella's list of characters knows the voice by the name
    voice_folder = str(companion.get("voice") or find_voice_folder(str(companion.get("name", ""))))
    if voice_folder == "":
        return (f"The game did not tell which voice {companion.get('name')} has. That needs the newest plugin of the game mod "
                "(KI_MCP_Gefaehrte.dll, interface version 6)."), 0
    # Making the sound takes a moment (seconds with XTTS), and the server has to keep answering the game meanwhile
    is_ok, problem, line = await asyncio.to_thread(voice.speak, text, voice_folder, is_loud, is_quiet)
    return ("" if is_ok else problem), line


async def _find_nearest_seat(queue: CompanionCommandQueue) -> tuple[str, str]:
    """Returns the ref_id of the seat that is nearest to the companion (or to the player, if the companion's place is
    not known), or an empty text if there is none, and why no seat could be looked for (empty = it could)"""
    # The game is asked for the things around the companion. An older game mod, or one whose companion is far away,
    # answers with the things around the player, and then the distance from the companion is the calculated one
    state_result = await queue.get_state(is_for_the_ai=False, things=True, around_companion=True)
    if state_result.get("connected") and not state_result.get("fresh"):
        # The state the server still has can be from another place: in a test a seat was looked for in a tavern among
        # the things of the street outside, and "no seat" was the answer
        return "", ("The game did not send the things around you in time, so no seat could be picked. Try again in a moment, "
                    "or name a seat from 'Things' in STATE as the target.")
    state = state_result.get("state")
    things = state.get("things") if isinstance(state, dict) else None
    if not isinstance(things, list):
        return "", ""
    seats = [thing for thing in things if thing.get("seat")]
    if len(seats) == 0:
        return "", ""
    nearest = min(seats, key=lambda seat: seat.get("distance_from_companion_meters", seat.get("distance_meters", 0)))
    return str(nearest.get("ref_id", "")), ""


def _tool_json(payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return _tool_text(json.dumps(payload, ensure_ascii=False), is_error)


def _tool_text(text: str, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}
