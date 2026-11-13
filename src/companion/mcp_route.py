import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.gestures import GESTURES, pick_animation
from src.companion.ref_ids import format_ref_id, parse_ref_id
from src.companion.state_text import describe_state
from src.companion.voice import MAX_TEXT_LENGTH, CompanionVoice, find_voice_folder

SERVER_NAME: str = "skyrim-companion"
SERVER_VERSION: str = "0.12"
SUPPORTED_PROTOCOL_VERSIONS: list[str] = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FALLBACK_PROTOCOL_VERSION: str = "2025-06-18"

TOOL_COMMAND: str = "COMMAND"
TOOL_STATE: str = "STATE"
ARGUMENT_DEBUG: str = "debug"
ARGUMENT_INVENTORY: str = "inventory"
ARGUMENT_CORPSE: str = "corpse"
ARGUMENT_THINGS: str = "things"
ARGUMENT_MAX: str = "max"
ARGUMENT_AROUND: str = "around"
# What the AI may also write for 'around' if it means its own character
AROUND_COMPANION_WORDS: list[str] = [consts.AROUND_COMPANION, "me", "companion", "self"]
GESTURE_NAMES: str = ", ".join(sorted(GESTURES))

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
    "The game's built-in follower AI handles moment-to-moment fighting and walking; you only set the direction."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": TOOL_COMMAND,
        "description": (
            "Make your companion character do something in the game. "
            "follow = follow the player, wait = stay here, "
            "attack = attack the target (works on peaceful characters too, so only when the player wants it), "
            "stand_down = stop fighting (whoever keeps attacking pulls you back into the fight), "
            "flee = retreat: you do not fight and run from enemies until your next command, "
            "move_to = walk to the target and wait there until 'follow'. The target is anything with a ref_id in STATE: "
            "a character, a thing, a door from 'Doors', a place from 'Places' (only if it is close enough to be loaded) "
            "or the target of a quest. You stop at a door, you do not go through it. "
            "loot = walk to the target, a dead character or a container (chest, barrel, sack ...), and take what is in it: "
            "everything, or only the kind of item whose ID is in 'detail' (see what is in it with STATE and 'corpse' first). "
            "The walk takes a while, your next STATE tells you what you got. Afterwards you follow or wait as before. "
            "Containers in a house or a shop belong to someone, so empty those only when the player wants it. "
            "sit = walk to the piece of furniture that is the target and sit down on it (no target = the seat nearest to you). "
            "That takes a few seconds, your next STATE says 'sitting' once you sit. "
            "You stay seated until 'follow' or another command that makes you move. "
            "look_at = keep your eyes on the target (a character or anything else with a ref_id; the player is 00000014). "
            "It holds until look_at without a target, which lets you look around freely again. "
            "gesture = play the gesture named in 'detail': " + GESTURE_NAMES + ". "
            "Some go on until 'detail' is 'stop' or until you move on. Gestures work best while you stand around. "
            "say = speak the text in 'detail' aloud in the game, with the voice of your character, so that the player hears "
            "you there. Write it in English: the voice cannot pronounce other languages. One or two sentences at a time. "
            "With your things (the target is the ID of an item from an inventory in STATE): "
            "equip = hold or wear the item, or drink or eat it if it is a potion or food, "
            "unequip = put the item away, "
            "give = hand the item to the player, take = take the item out of the player's inventory "
            "(only when the player wants it). For give and take 'detail' says how many: a number or 'all', nothing = 1. "
            "Not built into the game mod yet: "
            "cast_spell = cast the spell named in 'detail' on the target (no target = on yourself)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": consts.PLAYER_ACTIONS},
                "target": {"type": "string", "description": "ref_id of the target from STATE: a character (attack, move_to, loot, look_at), a container (loot, move_to), a seat (sit, move_to), a door, a place or a quest target (move_to, look_at). Or the ID of an item from an inventory (equip, unequip, give, take). Required for these actions, except sit and look_at."},
                "detail": {"type": "string", "description": "Extra information for the action: how many for give and take, the ID of the item for loot (nothing or 'all' = everything), the name of the gesture or 'stop' for gesture, the text to speak for say, the spell name for cast_spell."},
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
            "'Player': health, fighting or not, 'weapon drawn' if so, the compass direction the player faces, and the position x,y,z "
            "in game units (about 70 per meter, x grows to the east, y to the north, z upwards). "
            "'You': your own character with health, where it is, the compass direction it faces, where the player is "
            "seen from you, waiting, following, sitting or walking to someone, retreating, whom it fights, "
            "'weapon drawn' if so, and what it looks at because of look_at. "
            "Without a follower of the player you have no body. "
            "'Nearby': up to 10 lines (more with 'max'), one per character within about 50 meters of the player: first the living, hostile "
            "ones before the others, then the dead (marked 'dead'), each nearest first. "
            "ref_id, name, distance and direction from the player, after 'from you' the same seen from your own character, "
            "and 'hostile' if it is hostile to the player. "
            "'Things' only comes when you ask for it with 'things': what stands and lies within about 10 meters of the player, "
            "one per line with its ref_id: furniture ('seat' if you can sit on it, see the command sit), containers such as "
            "chests and barrels (see the command loot; 'locked' if you cannot open it) and items that lie around. "
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
            "A line under the player's name is what the player picked in a dialogue menu. 'You' is your own character speaking. "
            "'Gear': what you hold in your hands, your gold and the player's gold. "
            "The whole inventory only comes when you ask for it with 'inventory', because it takes the game a few seconds "
            "and is long: one line per kind of item, and after each item its ID for the commands equip, unequip, give and take. "
            "With 'corpse' you get what a dead character carries or a container holds, in the same form, for the command loot. "
            "'Since your last STATE': what became of a loot, move_to or sit you started (what you took, or that you did not get there)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                ARGUMENT_INVENTORY: {"type": "string", "enum": consts.INVENTORY_CHOICES, "description": "Also list an inventory: own = yours, player = the player's, both. Only when you need it."},
                ARGUMENT_CORPSE: {"type": "string", "description": "ref_id of a dead character or of a container from 'Nearby': also list what is in it. Replaces 'inventory' for this call."},
                ARGUMENT_THINGS: {"type": "boolean", "description": "true = also list the furniture, containers and loose items within about 10 meters of the player. Only when you need them."},
                ARGUMENT_AROUND: {"type": "string", "enum": consts.AROUND_CHOICES, "description": "Whose surroundings the lists show: player (the default) or you = your own character."},
                ARGUMENT_MAX: {"type": "integer", "description": f"How many characters 'Nearby' lists at most, and how many 'Things'. Nothing = {CompanionCommandQueue.DEFAULT_MAX_LINES}, at most {CompanionCommandQueue.HIGHEST_MAX_LINES}. More makes the answer longer and slower, so only for an overview."},
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
        return _result(message_id, {"tools": TOOLS})
    if method == "tools/call":
        return _result(message_id, await _call_tool(params.get("name"), params.get("arguments"), queue, voice))
    return _error(message_id, -32601, f"Method not found: {method}")


async def _call_tool(name: Any, arguments: Any, queue: CompanionCommandQueue, voice: CompanionVoice | None = None) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        arguments = {}

    if name == TOOL_STATE:
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
        state_result = await queue.get_state(inventory=inventory, corpse=corpse, things=arguments.get(ARGUMENT_THINGS) is True,
                                             max_lines=max_lines, around_companion=around in AROUND_COMPANION_WORDS)
        if arguments.get(ARGUMENT_DEBUG) is True:
            return _tool_json(state_result, False)
        return _tool_text(describe_state(state_result), False)

    if name == TOOL_COMMAND:
        action = str(arguments.get("action", "")).strip().lower()
        target = str(arguments.get("target", "") or "").strip()
        detail = str(arguments.get("detail", "") or "").strip()
        if action not in consts.PLAYER_ACTIONS:
            return _tool_json({"status": consts.STATUS_FAILED, "message": f"Unknown action '{action}'. Allowed: {', '.join(consts.PLAYER_ACTIONS)}"}, True)
        if action in consts.ACTIONS_REQUIRING_TARGET and target == "":
            return _tool_json({"status": consts.STATUS_FAILED, "message": f"Action '{action}' needs a target (ref_id from STATE)."}, True)
        if action == consts.ACTION_SIT and target == "":
            target = await _find_nearest_seat(queue)
            if target == "":
                return _tool_json({"status": consts.STATUS_FAILED, "message": "The game shows no seat within about 10 meters of you. Go closer to one, or name a seat from 'Things' in STATE as the target."}, True)
        if target != "":
            target_form_id = parse_ref_id(target)
            if target_form_id is None:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{target}' is not a ref_id or item ID. Use one from STATE, eg 000A2C94."}, True)
            target = format_ref_id(target_form_id)
        count = 1
        if action in consts.ACTIONS_WITH_COUNT and detail != "":
            if detail.lower() == consts.COUNT_ALL_TEXT:
                count = consts.COUNT_ALL
            elif detail.isdecimal() and int(detail) > 0:
                count = int(detail)
            else:
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"For {action} 'detail' is how many: a number or 'all'."}, True)
        item = ""
        if action == consts.ACTION_LOOT and detail != "" and detail.lower() != consts.COUNT_ALL_TEXT:
            item_form_id = parse_ref_id(detail)
            if item_form_id is None or item_form_id == 0:
                return _tool_json({"status": consts.STATUS_FAILED, "message": "For loot 'detail' is the ID of the item to take, or 'all' for everything."}, True)
            item = format_ref_id(item_form_id)
        if action == consts.ACTION_SAY:
            problem, line = await _prepare_speech(detail, queue, voice)
            if problem != "":
                return _tool_json({"status": consts.STATUS_FAILED, "message": problem}, True)
            # The game only gets the number of the line to speak. The sound is in its folder already
            result = await queue.send_command(action, count=line)
            return _tool_json(result, result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED))
        if action == consts.ACTION_GESTURE:
            # The game gets the ID of the animation, the name only goes along for its answer
            target = ""
            detail = detail.lower().replace(" ", "_")
            if detail == "":
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"gesture needs the name of the gesture in 'detail' ('{consts.GESTURE_STOP_TEXT}' ends one). The gestures: {GESTURE_NAMES}"}, True)
            if detail != consts.GESTURE_STOP_TEXT:
                item = pick_animation(detail)
                if item == "":
                    return _tool_json({"status": consts.STATUS_FAILED, "message": f"There is no gesture '{detail}'. The gestures: {GESTURE_NAMES}"}, True)
        if action == consts.ACTION_CAST_SPELL and detail == "":
            return _tool_json({"status": consts.STATUS_FAILED, "message": "cast_spell needs the spell name in 'detail'."}, True)
        result = await queue.send_command(action, target, detail, count=count, item=item)
        is_error = result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)
        return _tool_json(result, is_error)

    return _tool_json({"status": consts.STATUS_FAILED, "message": f"Unknown tool '{name}'."}, True)


async def _prepare_speech(text: str, queue: CompanionCommandQueue, voice: CompanionVoice | None) -> tuple[str, int]:
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
    # Piper takes a moment, and the server has to keep answering the game meanwhile
    is_ok, problem, line = await asyncio.to_thread(voice.speak, text, voice_folder)
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
