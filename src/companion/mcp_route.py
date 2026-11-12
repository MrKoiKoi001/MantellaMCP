import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.ref_ids import format_ref_id, parse_ref_id
from src.companion.state_text import describe_state

SERVER_NAME: str = "skyrim-companion"
SERVER_VERSION: str = "0.8"
SUPPORTED_PROTOCOL_VERSIONS: list[str] = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FALLBACK_PROTOCOL_VERSION: str = "2025-06-18"

TOOL_COMMAND: str = "COMMAND"
TOOL_STATE: str = "STATE"
ARGUMENT_DEBUG: str = "debug"
ARGUMENT_INVENTORY: str = "inventory"
ARGUMENT_CORPSE: str = "corpse"

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
            "move_to = walk to the target and wait there until 'follow', "
            "loot = walk to the dead character that is the target and take what its body carries: everything, or only "
            "the kind of item whose ID is in 'detail' (see what it carries with STATE and 'corpse' first). "
            "The walk takes a while, your next STATE tells you what you got. Afterwards you follow or wait as before. "
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
                "target": {"type": "string", "description": "ref_id of the target character (attack, move_to, loot) or ID of the item (equip, unequip, give, take), taken from STATE. Required for these actions."},
                "detail": {"type": "string", "description": "Extra information for the action: how many for give and take, the ID of the item for loot (nothing or 'all' = everything), the spell name for cast_spell."},
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
            "'Player': health, fighting or not, the compass direction the player faces, and the position x,y,z "
            "in game units (about 70 per meter, x grows to the east, y to the north, z upwards). "
            "'You': your own character with health, where it is, the compass direction it faces, where the player is "
            "seen from you, waiting, following or walking to someone, retreating, and whom it fights. "
            "Without a follower of the player you have no body. "
            "'Nearby': up to 10 characters within about 50 meters of the player, one per line: the living first, then "
            "the dead (marked 'dead'), each nearest first. "
            "ref_id, name, distance and direction from the player, after 'from you' the same seen from your own character, "
            "and 'hostile' if it is hostile to the player. "
            "Directions are relative to where the player (after 'from you': your character) faces: "
            "ahead, ahead-right, right, behind-right, behind, behind-left, left, ahead-left. "
            "Use the player's view when the player points at someone, and your own view when you talk about where you stand. "
            "Outdoors the list can miss someone, so get STATE once more if a character the player talks about is not in it. "
            "'Places': the nearest places of the map within about 300 meters of the player (farms, mills, stables, towers, "
            "caves ...), also ones the player has not discovered yet, with distance and direction from the player. "
            "'Doors': the nearest doors that lead into a building, a cave or out of town, named by where they lead. "
            "Use both to say where you are and where to go instead of coordinates. Indoors there are only doors. "
            "'Quests': the open quests from the player's journal, each with its open objectives. The ones the player "
            "tracks come first and are marked. 'Miscellaneous' collects the small objectives without a quest name. "
            "The lines are missing if the game cannot tell the quests. "
            "Behind an objective of a tracked quest, in brackets, is its target (what the compass of the game points to): "
            "its name, distance and direction from the player and after 'from you' from your character, so you can lead the way. "
            "'behind the door to ...' means the distance and direction are those of the door you have to go through, "
            "'not in this area' that the target is somewhere else, with the name of that place if the game has one. "
            "'Heard': what characters near the player said since your last STATE (the subtitles of the game), oldest first, "
            "each with how long ago. At most 10 lines, none older than 5 minutes, and each line is given only once. "
            "A line under the player's name is what the player picked in a dialogue menu. 'You' is your own character speaking. "
            "'Gear': what you hold in your hands, your gold and the player's gold. "
            "The whole inventory only comes when you ask for it with 'inventory', because it takes the game a few seconds "
            "and is long: one line per kind of item, and after each item its ID for the commands equip, unequip, give and take. "
            "With 'corpse' you get what a dead character carries, in the same form, for the command loot. "
            "'Since your last STATE': what became of a loot or move_to you started (what you took, or that you did not get there)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                ARGUMENT_INVENTORY: {"type": "string", "enum": consts.INVENTORY_CHOICES, "description": "Also list an inventory: own = yours, player = the player's, both. Only when you need it."},
                ARGUMENT_CORPSE: {"type": "string", "description": "ref_id of a dead character from 'Nearby': also list what its body carries. Replaces 'inventory' for this call."},
                ARGUMENT_DEBUG: {"type": "boolean", "description": "true = all data as JSON with timings. Only for testing, never while playing."},
            },
        },
    },
]


def add_mcp_route(app: FastAPI, queue: CompanionCommandQueue, token: str):
    """Adds a minimal MCP server (Streamable HTTP, stateless, JSON replies) that the AI app connects to

    This is written by hand instead of using the MCP SDK on purpose: the SDK needs newer
    versions of packages that Mantella pins (eg pywin32), and only two tools are needed.

    Args:
        token: Secret part of the URL. The route is reachable from the internet through a tunnel,
            so only someone who knows the full URL can control the companion.
    """
    route = consts.ROUTE_MCP_PREFIX + token

    @app.post(route)
    async def mcp_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content=_error(None, -32700, "Parse error"))

        if isinstance(body, list):
            replies = [reply for reply in [await _handle_message(message, queue) for message in body] if reply is not None]
            if len(replies) == 0:
                return Response(status_code=202)
            return JSONResponse(content=replies)

        reply = await _handle_message(body, queue)
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


async def _handle_message(message: Any, queue: CompanionCommandQueue) -> dict[str, Any] | None:
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
        return _result(message_id, await _call_tool(params.get("name"), params.get("arguments"), queue))
    return _error(message_id, -32601, f"Method not found: {method}")


async def _call_tool(name: Any, arguments: Any, queue: CompanionCommandQueue) -> dict[str, Any]:
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
                return _tool_json({"status": consts.STATUS_FAILED, "message": f"'{corpse}' is not a ref_id. Use the one of a dead character from 'Nearby' in STATE."}, True)
            corpse = format_ref_id(corpse_form_id)
        state_result = await queue.get_state(inventory=inventory, corpse=corpse)
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
        if action == consts.ACTION_CAST_SPELL and detail == "":
            return _tool_json({"status": consts.STATUS_FAILED, "message": "cast_spell needs the spell name in 'detail'."}, True)
        result = await queue.send_command(action, target, detail, count=count, item=item)
        is_error = result["status"] not in (consts.STATUS_DONE, consts.STATUS_DELIVERED)
        return _tool_json(result, is_error)

    return _tool_json({"status": consts.STATUS_FAILED, "message": f"Unknown tool '{name}'."}, True)


def _tool_json(payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return _tool_text(json.dumps(payload, ensure_ascii=False), is_error)


def _tool_text(text: str, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}
