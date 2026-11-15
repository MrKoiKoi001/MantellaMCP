import asyncio
import copy
import json
import random
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.command_chain import MAX_WAITING_STEPS, CompanionChain, describe_step, read_pause_seconds
from src.companion.gestures import GESTURE_NAMES as ALL_GESTURE_NAMES, pick_gesture
from src.companion.idle_search import IdleLibrary
from src.companion.ref_ids import format_ref_id, parse_ref_id, split_form_id
from src.companion.state_text import describe_state
from src.companion.voice import MAX_TEXT_LENGTH, PIPER_LANGUAGE_NAME, CompanionVoice, clean_text, find_voice_folder

SERVER_NAME: str = "skyrim-companion"
SERVER_VERSION: str = "0.31"
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
ARGUMENT_INVENTORY: str = "inventory"
ARGUMENT_CORPSE: str = "corpse"
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
ARGUMENT_THEN: str = "then"
ARGUMENT_AFTER_PREVIOUS: str = "after_previous"
# What a step of 'then' can carry: the same as a command, without a chain of its own
STEP_FIELDS: list[str] = ["action", "target", "detail", "loud", "quiet", "run", "manner"]
# How long the sentence of the say that was sent last takes, for a chain that waits until it is spoken
_last_say_seconds: float = 0.0
# The animations of the game, per folder of the game (see idle_search.py)
_idle_libraries: dict[str, IdleLibrary] = {}
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
            "face = turn your whole body to the target (anything with a ref_id, also a place or a quest target far away) "
            f"or to the point of the compass in 'detail' ({COMPASS_NAMES}). Do this before you point at something with a gesture. "
            "Only while you stand: not while you walk, fight or sit. "
            "loot = walk to the target, a dead character or a container (chest, barrel, sack ...), and take what is in it: "
            "everything, or only the kind of item whose ID is in 'detail' (see what is in it with STATE and 'corpse' first). "
            "The walk takes a while, your next STATE tells you what you got. Afterwards you follow or wait as before. "
            "Containers in a house or a shop belong to someone, so empty those only when the player wants it. "
            "pick_up = walk to the target, an item that lies around (from 'Things' in STATE, also an arrow that was shot), "
            "and take it. Your next STATE tells you whether you got it. Afterwards you follow or wait as before. "
            "What lies in a house or a shop belongs to someone, so take that only when the player wants it. "
            "sit = walk to the piece of furniture that is the target and sit down on it (no target = the seat nearest to you). "
            "That takes a few seconds, your next STATE says 'sitting' once you sit. "
            "You stay seated until 'follow' or another command that makes you move. "
            "look_at = keep your eyes on the target (a character or anything else with a ref_id; the player is 00000014). "
            "It holds until look_at without a target, which lets you look around freely again. "
            "gesture = play the gesture named in 'detail': " + GESTURE_NAMES + ". "
            "Some go on until 'detail' is 'stop' or until you move on. Gestures work best while you stand around. "
            "drink, eat, read_book and write_down also work while you sit on a chair, drink_at_table and pass_out only there. "
            "drunk makes you sway and stagger, also while you walk, until gesture with 'stop'. "
            "STATE with 'find_gesture' finds more animations of the game by a word; play one with its name or its ID in "
            "'detail' (those are untried: some do nothing). "
            "expression = put a look on your face, named in 'detail': " + EXPRESSION_NAMES + ". A number behind it "
            "(1 to 100) makes it weaker, eg 'happy 50'. It stays until 'detail' is 'none'. "
            "say = speak the text in 'detail' aloud in the game, with the voice of your character, so that the player hears "
            "you there. " + SAY_LANGUAGE_NOTE + "One or two sentences at a time. "
            "With 'loud': true you call it out with a raised voice: for a warning, or when the player is far away. "
            "With 'quiet': true you say it in a low voice, with a calm face and still hands: while you sneak, or when "
            "nobody else is to listen. The player hears it less far, so stay close. Your voice only gets quieter, it "
            "does not whisper. "
            "target = whom you speak to; nothing = the player. You turn to them and look at them while you speak, and "
            "afterwards your eyes are free again (or back on what look_at holds; while look_at holds and you name no "
            "target, your eyes stay there). So name the target whenever you speak to someone other than the player. "
            "To another character you stop, walk over first if they are more than about 5 meters away, turn to them and "
            "then speak; afterwards you follow the player again if you did before. No wait or face is needed for that. "
            "manner = how you speak, it moves your hands and face to match for this sentence: " + MANNER_NAMES + ". "
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
            "give = hand the item to the player, take = take the item out of the player's inventory "
            "(only when the player wants it). In the player's inventory 'favorite' marks what the player keeps among the "
            "favorites: ask before you take a weapon or a piece of armor that is one, for potions and food it does not matter. "
            "For give and take 'detail' says how many: a number or 'all', nothing = 1. "
            "cast_spell = cast a spell you know: its ID or its exact name in 'detail', and whom you cast it on as the target "
            "(the player's ref_id to heal the player; no target for a spell on yourself). STATE with inventory 'own' lists "
            "the spells you know with their IDs. You only know spells you have learned from a spell tome, so ask the player "
            "for a tome if you know none. The spell works at once and costs you no magicka. "
            "Riding: claim_horse = the horse that is the target is yours from now on (its ref_id from 'Nearby'; do that when "
            "the player tells you which horse is yours). mount = get on your horse and ride after the player; if the horse is "
            "far away it is brought to your side. With a target you take that horse as yours first. dismount = get off, "
            "the horse stays where it is. When the player mounts or dismounts, you do the same on your "
            "own. ride_lock with 'detail' 'on' switches that off: you stay in the saddle, or on foot, whatever the player "
            "does, until ride_lock 'off'. mount and dismount always work. In the saddle you can speak, look, cast and "
            "fight, and you can ride somewhere: move_to and move take you there on horseback and you wait there in the "
            "saddle (run: true = gallop), follow with a target makes you ride after that character, wait makes you stop "
            "where you are, still in the saddle. What is done on foot (enter, loot, pick_up, sit, open, close, make_room) "
            "needs dismount first. You cannot ride indoors. "
            "call_horse = a horse runs to its owner and stays there: your horse comes to you, the horse of the player to the "
            "player (the target is its ref_id from 'Your horse' or 'Player's horse' in STATE; no target = your horse, or the "
            "player's if you have none). 'detail' 'player' or 'you' sends it to that one instead. Use it when the player "
            "calls a horse by its name or asks for it. A horse too far away to run is brought to the side of whom it comes to. "
            "The horse of the player is known once the player has ridden it. Both horses stay where they are left. "
            "rename_horse = give a horse a new name, the one in 'detail' (the target is the horse, no target = your horse). "
            "Use it when the player says what a horse is called from now on. "
            "Commands one after the other: 'then' is a list of up to " + str(MAX_WAITING_STEPS) + " more commands (each with "
            "action, target, detail and so on) that start one by one, each when you are done with the one before: move_to a "
            "bard and then gesture dance, sit and then say, say and then mount. 'after_previous': true puts a single command "
            "behind what you are doing and what waits already. pause = wait the seconds in 'detail' (1 to 60), for such a list. "
            "A new command that moves you (follow, wait, flee, attack, move_to ...) throws away what waits. say, look_at, "
            "gesture, expression and what you do with your things run at once and leave it alone. If a step fails, the ones "
            "behind it are dropped. STATE shows what waits ('Queue') and tells afterwards what became of it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": consts.PLAYER_ACTIONS},
                "target": {"type": "string", "description": "ref_id of the target from STATE: a character (attack, move_to, follow, loot, look_at, face), a container (loot, move_to), a seat (sit, move_to), an item that lies around (pick_up), a horse (claim_horse, mount, call_horse, rename_horse), a door (enter, move_to, look_at), a place or a quest target (move_to, look_at, face). For say: whom you speak to (a character; nothing = the player). Or the ID of an item from an inventory (equip, unequip, give, take). Required for these actions, except sit and look_at."},
                "detail": {"type": "string", "description": "Extra information for the action: how many for give and take, the ID of the item for loot (nothing or 'all' = everything), the name of the gesture or 'stop' for gesture, the look or 'none' for expression, the direction (back, left, right) for make_room, the direction and the meters for move, how far to stop in front of the target for move_to, the point of the compass for face, the text to speak for say, the spell name for cast_spell, 'on' or 'off' for ride_lock, the new name for rename_horse, the seconds for pause."},
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
            "'Player': health, fighting or not, 'weapon drawn' if so, 'sitting on' with the piece of furniture, 'riding' with the name of the horse or 'lying asleep' if so, the compass direction the player faces, and the position x,y,z "
            "in game units (about 70 per meter, x grows to the east, y to the north, z upwards). "
            "'You': your own character with health, where it is, the compass direction it faces, where the player is "
            "seen from you, 'riding' with the name of the horse, waiting, following (the player, or the character named behind it), "
            "'sitting on' with the piece of furniture, or walking to someone, "
            "retreating, whom it fights, 'running' while you run, 'weapon drawn' if so, 'weapon kept away' after sheathe, the look on its face and what it looks at because of look_at. "
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
            "chests and barrels (see the command loot; 'locked' if you cannot open it) and items that lie around "
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
            "'Gear': what you hold in your hands, your gold and the player's gold. "
            "The whole inventory only comes when you ask for it with 'inventory', because it takes the game a few seconds "
            "and is long: one line per kind of item, and after each item its ID for the commands equip, unequip, give and take. "
            "'equipped' marks what is worn or held, 'favorite' what the player has among the favorites (only in the player's inventory). "
            "With 'corpse' you get what a dead character carries or a container holds, in the same form, for the command loot. "
            "'Queue' comes while commands wait for each other (see 'then' of COMMAND): what you do now and what follows. "
            "With 'find_gesture' you get no state at all, only the animations of the game whose name has that word, for gesture. "
            "'Since your last STATE': what became of a loot, pick_up, move_to, move, enter, make_room or sit you started (what you took, or that you did not get there). "
            "With 'visible' the lists only hold what can really be seen from where 'around' says (the player by default): "
            "characters in front with no wall in between, living first, then the dead, then the things in front, and only the "
            "doors and places in front with no wall in between. "
            "Use it when the talk is about what someone sees, not for an overview. "
            "With 'stats' you get the level of the player, whether a level-up is waiting, perk points to spend, health, magicka, "
            "stamina, the weight carried and all skills (highest first), and your own level, health, magicka and stamina."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                ARGUMENT_INVENTORY: {"type": "string", "enum": consts.INVENTORY_CHOICES, "description": "Also list an inventory: own = yours, player = the player's, both. Only when you need it."},
                ARGUMENT_CORPSE: {"type": "string", "description": "ref_id of a dead character or of a container from 'Nearby': also list what is in it. Replaces 'inventory' for this call."},
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
                ARGUMENT_DEBUG: {"type": "boolean", "description": "true = all data as JSON with timings. Only for testing, never while playing."},
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
    if not isinstance(arguments, dict):
        arguments = {}

    if name == TOOL_STATE:
        find_gesture = " ".join(str(arguments.get(ARGUMENT_FIND_GESTURE, "") or "").split())[:MAX_FIND_LENGTH]
        if find_gesture != "":
            # The server knows the animations itself, the game is not asked
            return _tool_text(_describe_found_gestures(find_gesture, _idle_library(queue)), False)
        inventory = str(arguments.get(ARGUMENT_INVENTORY, "") or "").strip().lower()
        if inventory not in consts.INVENTORY_CHOICES:
            inventory = ""
        corpse = str(arguments.get(ARGUMENT_CORPSE, "") or "").strip()
        if corpse != "":
            corpse_form_id = parse_ref_id(corpse)
            if corpse_form_id is None:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{corpse}' is not a ref_id. Use the one of a dead character or a container from 'Nearby' in STATE."}, True)
            corpse = format_ref_id(corpse_form_id)
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
        state_result = await queue.get_state(inventory=inventory, corpse=corpse, things=arguments.get(ARGUMENT_THINGS) is True,
                                             max_lines=max_lines, around_companion=around in AROUND_COMPANION_WORDS,
                                             stats=arguments.get(ARGUMENT_STATS) is True,
                                             visible=arguments.get(ARGUMENT_VISIBLE) is True,
                                             space=is_space_wanted, find=find, space_meters=space_meters,
                                             space_degrees=space_degrees, space_from_facing=is_space_from_facing,
                                             space_angle=space_angle, space_direction=space_direction)
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
    if action not in consts.PLAYER_ACTIONS:
        return {"status": consts.STATUS_FAILED, "message": f"Unknown action '{action}'. Allowed: {', '.join(consts.PLAYER_ACTIONS)}"}, True
    if action == consts.ACTION_PAUSE:
        return {"status": consts.STATUS_FAILED, "message": "pause only waits between commands that follow each other (see 'then' and 'after_previous')."}, True
    if action in consts.ACTIONS_REQUIRING_TARGET and target == "":
        return {"status": consts.STATUS_FAILED, "message": f"Action '{action}' needs a target (ref_id from STATE)."}, True
    if action == consts.ACTION_SIT and target == "":
        target = await _find_nearest_seat(queue)
        if target == "":
            return {"status": consts.STATUS_FAILED, "message": "The game shows no seat within about 10 meters of you. Go closer to one, or name a seat from 'Things' in STATE as the target."}, True
    if target != "":
        target_form_id = parse_ref_id(target)
        if target_form_id is None:
            return {"status": consts.STATUS_FAILED, "message": f"'{target}' is not a ref_id or item ID. Use one from STATE, eg 000A2C94."}, True
        target = format_ref_id(target_form_id)
    count = 1
    if action == consts.ACTION_MOVE_TO:
        # How far in front of the target to stop. Without a number the game mod decides
        count = consts.MOVE_TO_USUAL_DISTANCE
        words = detail.lower().split()
        if len(words) > 1 and words[-1] in ("m", "meter", "meters"):
            words.pop()
        if len(words) > 0:
            number = words[0].removesuffix("m")
            if len(words) != 1 or not number.isdecimal() or int(number) > consts.MOVE_TO_HIGHEST_KEEP_METERS:
                return {"status": consts.STATUS_FAILED, "message": f"For move_to 'detail' is how many meters in front of the target you stop: a number from 0 to {consts.MOVE_TO_HIGHEST_KEEP_METERS}, or nothing."}, True
            count = int(number)
            detail = number
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
                                          say_ms=int(seconds * 1000), extra={consts.KEY_COMMAND_QUIET: 1} if is_quiet else None)
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
                # An animation of the game by its own name or its ID (see find_gesture of STATE). These are untried,
                # so the game only plays them the usual way
                entry = _idle_library(queue).get(detail)
                if entry is None:
                    return {"status": consts.STATUS_FAILED, "message": f"There is no gesture '{detail}'. The gestures: {GESTURE_NAMES}. STATE with 'find_gesture' finds more animations of the game by a word."}, True
                item = entry.ref_id
                detail = entry.name
    if action == consts.ACTION_CAST_SPELL:
        if detail == "":
            return {"status": consts.STATUS_FAILED, "message": "cast_spell needs the ID or the name of the spell in 'detail'. STATE with inventory 'own' lists the spells you know."}, True
        # Eight hex digits are the ID of the spell from STATE, anything else is its name
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
    if action in consts.ACTIONS_REQUIRING_TARGET and str(step.get("target", "") or "").strip() == "":
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
            notes.append("So what you put behind it was not started.")
        else:
            chain.add(steps, running_text=describe_step(first), fails_before=fails_before,
                      first_wait_seconds=_last_say_seconds if action == consts.ACTION_SAY else 0.0)
            notes.append(f"Queue: {chain.describe()}.")
    if len(notes) > 0:
        payload = dict(payload)
        payload["message"] = " ".join([str(payload.get("message", ""))] + notes).strip()
    return _tool_json(payload, is_error)


def _idle_library(queue: CompanionCommandQueue) -> IdleLibrary:
    """The animations of the game in the folder the server was started with"""
    if queue.game_folder not in _idle_libraries:
        _idle_libraries[queue.game_folder] = IdleLibrary(queue.game_folder)
    return _idle_libraries[queue.game_folder]


def _describe_found_gestures(word: str, library: IdleLibrary) -> str:
    """The answer to find_gesture: the gestures with a short name that fit, and the animations of the game that do"""
    lines: list[str] = []
    short_names = [name for name in ALL_GESTURE_NAMES if word.lower().replace(" ", "_") in name]
    if len(short_names) > 0:
        lines.append(f"Gestures with a short name for '{word}': {', '.join(short_names)}")
    matches, total = library.find(word)
    if library.problem != "":
        lines.append(library.problem)
    elif total == 0:
        lines.append(f"The game has no animation with '{word}' in its name. Try another word, in English: the names are those of the game (eg drink, dance, lean, sit, read).")
    else:
        more = "" if total <= len(matches) else f", the first {len(matches)} of {total}"
        lines.append(f"Animations of the game for '{word}'{more}. Play one with gesture and its name or ID in 'detail'. They are untried: some do nothing, 'gesture' with 'stop' ends one that goes on.")
        for entry in matches:
            note = ""
            if entry.needs_sitting:
                note = ", only while sitting"
            elif entry.has_conditions:
                note = ", has conditions (may not play)"
            lines.append(f"- {entry.name} {entry.ref_id}{note}")
    return "\n".join(lines)


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


async def _find_nearest_seat(queue: CompanionCommandQueue) -> str:
    """Returns the ref_id of the seat that is nearest to the companion (or to the player, if the companion's place is not known), or an empty text"""
    # The game is asked for the things around the companion. An older game mod, or one whose companion is far away,
    # answers with the things around the player, and then the distance from the companion is the calculated one
    state_result = await queue.get_state(is_for_the_ai=False, things=True, around_companion=True)
    state = state_result.get("state")
    things = state.get("things") if isinstance(state, dict) else None
    if not isinstance(things, list):
        return ""
    seats = [thing for thing in things if thing.get("seat")]
    if len(seats) == 0:
        return ""
    nearest = min(seats, key=lambda seat: seat.get("distance_from_companion_meters", seat.get("distance_meters", 0)))
    return str(nearest.get("ref_id", ""))


def _tool_json(payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return _tool_text(json.dumps(payload, ensure_ascii=False), is_error)


def _tool_text(text: str, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}
