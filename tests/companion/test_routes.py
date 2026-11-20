import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient

from companion_main import create_app
from src.companion.companion_constants import companion_constants as consts
from src.companion.mcp_route import manual_text

TOKEN = "test-token"
MCP_ROUTE = consts.ROUTE_MCP_PREFIX + TOKEN


IDLE_REPLY = {
    consts.KEY_MANTELLA_REPLY_TYPE: consts.MANTELLA_REPLY_TYPE_IGNORED,
    consts.KEY_REPLY_TYPE: consts.REPLY_TYPE_IDLE,
}


def rpc(client: TestClient, method: str, params: dict | None = None, message_id: int = 1):
    return client.post(MCP_ROUTE, json={"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})


def tool_text(response) -> str:
    return response.json()["result"]["content"][0]["text"]


def tool_payload(response) -> dict:
    return json.loads(tool_text(response))


def game_state_for(nearby: list[dict]) -> dict:
    """A state in the short form the game mod sends, with these characters nearby (see game_state.py)"""
    from src.companion import game_state
    from src.companion.fake_game import to_papyrus_number

    values: list[int] = []
    names: list[str] = []
    for place, character in enumerate(nearby):
        values.extend([to_papyrus_number(character["ref_id"]), character["distance_meters"], character["angle_degrees"], int(character["hostile"]), int(character.get("dead", False))])
        names.append(f"{place}:{character['name']}")
    return {
        game_state.KEY_LOCATION: "Whiterun",
        game_state.KEY_PLAYER_NAME: "Koi",
        game_state.KEY_PLAYER_VALUES: [90, 1, 100, -200, 30, 315, 0],
        game_state.KEY_NEARBY_VALUES: values,
        game_state.KEY_NEARBY_NAMES: names,
        game_state.KEY_TIMES: [640, 410],
    }


def test_initialize_and_tools_list():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in init.json()["result"]["capabilities"]

    notification = client.post(MCP_ROUTE, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notification.status_code == 202

    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["COMMAND", "STATE", "HELP", "WAIT_FOR_EVENT"]


def test_wrong_token_is_not_found():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    assert client.post(consts.ROUTE_MCP_PREFIX + "wrong", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404


def test_unknown_method_is_an_error():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    assert rpc(client, "resources/list").json()["error"]["code"] == -32601


def test_command_without_game_reports_not_connected():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "follow"}})
    assert response.json()["result"]["isError"] is True
    assert tool_payload(response)["status"] == consts.STATUS_GAME_NOT_CONNECTED


def test_command_validation():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "dance"}}))["status"] == consts.STATUS_FAILED
    assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "attack"}}))["status"] == consts.STATUS_FAILED
    assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell"}}))["status"] == consts.STATUS_FAILED
    # A name instead of a ref_id is refused before it reaches the game
    not_a_ref_id = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "attack", "target": "Bandit"}}))
    assert not_a_ref_id["status"] == consts.STATUS_FAILED
    assert "ref_id" in not_a_ref_id["message"]


def test_ref_ids_are_parsed_and_split_for_the_game():
    from src.companion.ref_ids import format_ref_id, parse_ref_id, split_form_id

    assert parse_ref_id("000A2C94") == 0x000A2C94
    assert parse_ref_id(" 0xff000c2c ") == 0xFF000C2C
    assert parse_ref_id("14") == 0x14
    assert parse_ref_id("Bandit") is None
    assert parse_ref_id("") is None
    assert parse_ref_id("123456789") is None
    assert format_ref_id(0x14) == "00000014"
    assert format_ref_id(0xFF000C2C) == "FF000C2C"
    # Both halves stay small positive numbers, even for IDs that would be negative in Papyrus
    assert split_form_id(0xFF000C2C) == (0xFF00, 0x0C2C)
    assert split_form_id(0) == (0, 0)


def test_state_lists_nearby_characters_nearest_first():
    nearby = [
        {"name": "Bandit", "ref_id": "FF000A02", "distance_meters": 20, "angle_degrees": 170, "hostile": True},
        {"name": "Whiterun Guard", "ref_id": "000F6845", "distance_meters": 8, "angle_degrees": 90, "hostile": False},
        {"name": "bandit", "ref_id": "FF000A01", "distance_meters": 12, "angle_degrees": -40, "hostile": True},
    ]
    # 'with' keeps both requests in one event loop, like in the running server
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: game_state_for(nearby)})
        state = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True}}))["state"]
    # Hostile characters come before the others, however near those are
    assert [character["distance_meters"] for character in state["nearby"]] == [12, 20, 8]
    # The ref ID came as a negative number, and the name in lower case
    assert state["nearby"][0] == {"name": "Bandit", "ref_id": "FF000A01", "distance_meters": 12, "angle_degrees": -40, "hostile": True, "dead": False}
    assert state["player"] == {"name": "Koi", "health_percent": 90, "in_combat": True, "position": [100, -200, 30], "facing_degrees": 315}
    assert state["indoors"] is False
    assert (state["build_ms"], state["scan_ms"]) == (640, 410)
    # Without the debug argument the AI gets these few short lines instead of JSON, and no timings
    from src.companion.state_text import describe_state

    assert describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines() == [
        "Whiterun (outdoors)",
        "Player Koi: health 90%, fighting, faces north-west, position 100,-200,30",
        "You have no body in the game: the player has no follower.",
        "Nearby:",
        "FF000A01 Bandit, 12 m ahead-left, hostile",
        "FF000A02 Bandit, 20 m behind, hostile",
        "000F6845 Whiterun Guard, 8 m right",
    ]


def test_state_without_nearby_from_the_game_has_an_empty_list():
    # The game mod leaves 'nearby' out when nobody is around
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}})
        state = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True}}))["state"]
    assert state == {"location": "Whiterun", "nearby": []}


def test_state_without_any_game_state_stays_empty():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    assert tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True}}))["state"] is None
    assert tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {}})) == "The game is not connected. Is Skyrim running with a loaded save?"


def test_request_without_state_keeps_the_last_state():
    # The game mod only sends its state when it was asked for it
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}})
        client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 2})
        state = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True}}))["state"]
    assert state["location"] == "Whiterun"


def test_game_state_names_survive_a_dropped_name():
    from src.companion import game_state

    # The HTTP plugin of the game drops a name it cannot send, so the second of three names is missing here
    state = game_state.read_game_state({
        game_state.KEY_NEARBY_VALUES: [1, 5, 0, 0, 0, 2, 6, 0, 0, 0, 3, 7, 0, 0, 0],
        game_state.KEY_NEARBY_NAMES: ["0:Sven", "2:wolf"],
    })
    assert [character["name"] for character in state["nearby"]] == ["Sven", "unknown", "Wolf"]
    # Numbers that do not come in fives are not a list of characters
    assert game_state.read_game_state({game_state.KEY_NEARBY_VALUES: [1, 5, 0]})["nearby"] == []
    assert game_state.read_game_state(None) is None


def test_directions_seen_from_the_player_and_on_the_map():
    from src.companion.state_text import compass_direction, relative_direction

    assert [relative_direction(angle) for angle in [0, 22, 23, 90, 135, 180, -180, -135, -90, -45, -22]] == [
        "ahead", "ahead", "ahead-right", "right", "behind-right", "behind", "behind", "behind-left", "left", "ahead-left", "ahead"]
    assert [compass_direction(angle) for angle in [0, 45, 90, 180, 270, 359, 360]] == [
        "north", "north-east", "east", "south", "west", "north", "north"]


def test_state_text_describes_the_companion():
    from src.companion.fake_game import build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(state: dict | None, connected: bool = True, fresh: bool = True) -> list[str]:
        return describe_state({"connected": connected, "fresh": fresh, "state_age_seconds": 12.5, "state": state}).splitlines()

    fighting = text_for(read_game_state(build_state(False, "FF000A01")))
    # The player faces north-east and Lydia stands behind him facing south, so she sees everything the other way round
    assert fighting == [
        "Whiterun, Plains District (outdoors), 14:35, snow",
        "Player Dragonborn: health 85%, fighting, faces north-east, position 23500,-7900,-3300",
        "You, Lydia: health 100%, 3 m behind-left, faces south, player left of you, following, fighting Bandit FF000A01",
        "Gear: you hold Steel Sword and Iron Shield, your gold 30, the player's gold 250",
        "Nearby:",
        "FF000A01 Bandit, 12 m ahead-left, from you 13 m behind, hostile",
        "FF000A02 Bandit, 20 m behind-right, from you 19 m ahead, hostile",
        "000F6845 Whiterun Guard, 8 m right, from you 10 m ahead-left",
        "FF000A03 Bandit Outlaw, dead, 5 m ahead, from you 8 m behind-left",
        "Places: Pelagia Farm 000C0001 (farm) 120 m ahead-left, Whiterun Stables 000C0002 (stable) 260 m behind",
        "Doors: Warmaiden's 0001A6F0 20 m right, Dragonsreach 0001A6F4 140 m ahead-left",
        "Quests:",
        # The Jarl is north of the player, who faces north-east. Lydia faces south, so the door is behind her
        "- Before the Storm (tracked): Talk to the Jarl of Whiterun "
        "[target Jarl Balgruuf: behind the door to Dragonsreach 0001A6F4, 140 m ahead-left, from you 141 m behind]",
        "- The Golden Claw: Find the secret of Bleak Falls Barrow; Retrieve the Golden Claw",
        "- Miscellaneous: Buy a sword from Alvor",
    ]
    you_start = "You, Lydia: health 100%, 3 m behind-left, faces south, player left of you, "
    assert text_for(read_game_state(build_state(True)))[2] == you_start + "waiting"
    # While walking somewhere the game has the companion on 'waiting', which would only confuse here
    assert text_for(read_game_state(build_state(True, "", False, "000F6845")))[2] == you_start + "walking to Whiterun Guard 000F6845"
    assert text_for(read_game_state(build_state(False, "", True)))[2] == you_start + "following, retreating"

    stale = text_for(read_game_state(build_state()), fresh=False)
    assert stale[0] == "The game did not answer in time. What follows is 12.5 seconds old:"
    assert text_for(None) == ["The game has not sent its state yet. Ask again in a moment."]
    assert text_for({"location": "Whiterun", "nearby": []})[-1] == "Nearby: nobody"
    assert text_for(read_game_state(build_state()), connected=False, fresh=False)[:2] == [
        "The game is not connected. Is Skyrim running with a loaded save?", "Last known state, 12.5 seconds old:"]


def test_game_route_is_idle_without_commands():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}})
    assert response.status_code == 200
    assert response.json() == IDLE_REPLY


def test_game_route_sends_the_poll_id_back():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 4711, consts.KEY_STATE: {"location": "Whiterun"}})
    assert response.json()[consts.KEY_POLL_ID] == 4711
    assert response.json()[consts.KEY_REPLY_TYPE] == consts.REPLY_TYPE_IDLE


def test_game_route_reply_is_ignored_by_mantella():
    # MantellaConversation.psc does nothing for exactly this reply type
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = client.post(consts.ROUTE_GAME, json={})
    assert response.json()["mantella_reply_type"] == "mantella_init_completed"


def test_game_route_answers_valid_json_even_for_broken_request():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = client.post(consts.ROUTE_GAME, content=b"this is not json")
    assert response.status_code == 200
    assert response.json() == IDLE_REPLY


def test_game_route_rejects_requests_that_came_through_a_tunnel():
    client = TestClient(create_app(TOKEN, hold_seconds=0.1))
    response = client.post(consts.ROUTE_GAME, json={}, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert response.status_code == 403


def test_fake_game_companion_waits_and_follows_like_the_game():
    from src.companion.fake_game import build_state, is_waiting_after
    from src.companion.game_state import read_game_state

    assert read_game_state(build_state())["companion"]["waiting"] is False
    is_waiting = is_waiting_after(consts.ACTION_WAIT, False)
    assert read_game_state(build_state(is_waiting))["companion"]["waiting"] is True
    assert is_waiting_after(consts.ACTION_ATTACK, is_waiting) is True
    assert is_waiting_after(consts.ACTION_FOLLOW, is_waiting) is False


def test_fake_game_companion_attacks_and_stands_down_like_the_game():
    from src.companion.fake_game import build_state, run_command
    from src.companion.game_state import read_game_state

    is_ok, message, target_ref_id = run_command(consts.ACTION_ATTACK, "FF000A01", "")
    assert is_ok is True and target_ref_id == "FF000A01" and "Bandit" in message
    fighting = read_game_state(build_state(False, target_ref_id))
    assert fighting["companion"]["in_combat"] is True
    assert fighting["companion"]["target_ref_id"] == "FF000A01"

    is_ok, message, target_ref_id = run_command(consts.ACTION_ATTACK, "00000099", target_ref_id)
    assert is_ok is False and target_ref_id == "FF000A01"

    is_ok, message, target_ref_id = run_command(consts.ACTION_STAND_DOWN, "", target_ref_id)
    assert is_ok is True and target_ref_id == ""
    assert "target_ref_id" not in read_game_state(build_state())["companion"]
    # The simulator sends Lydia's ref ID like every other one, and the companion never shows up in its own list
    assert read_game_state(build_state())["companion"]["ref_id"] == "000A2C94"


def test_command_travels_from_mcp_to_game_and_result_travels_back():
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Lydia attacks"}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}, consts.KEY_RESULTS: results})

        game_thread = threading.Thread(target=game, daemon=True)
        game_thread.start()
        time.sleep(0.3)
        response = rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "attack", "target": "ff000a01"}})

        assert tool_payload(response) == {"status": consts.STATUS_DONE, "message": "Lydia attacks"}
        assert response.json()["result"]["isError"] is False
        assert received_commands[0][consts.KEY_COMMAND_ACTION] == "attack"
        assert received_commands[0][consts.KEY_COMMAND_TARGET] == "FF000A01"
        assert received_commands[0][consts.KEY_COMMAND_TARGET_HIGH] == 0xFF00
        assert received_commands[0][consts.KEY_COMMAND_TARGET_LOW] == 0x0A01


def test_view_of_the_companion_is_calculated_from_the_view_of_the_player():
    from src.companion import game_state

    def state_for(companion_values: list[int]) -> dict:
        return game_state.read_game_state({
            # The player faces north
            game_state.KEY_PLAYER_VALUES: [100, 0, 0, 0, 0, 0, 0],
            game_state.KEY_COMPANION_NAME: "Lydia",
            game_state.KEY_COMPANION_VALUES: companion_values,
            # One character 10 m to the right of the player, and one exactly where Lydia stands
            game_state.KEY_NEARBY_VALUES: [1, 10, 90, 0, 0, 2, 10, 0, 0, 0],
            game_state.KEY_NEARBY_NAMES: ["0:Sven", "1:Chicken"],
        })

    # Lydia stands 10 m ahead of the player and looks back at him (south)
    state = state_for([20, 100, 0, 0, 0, 1, 10, 0, 0, 180, 0, 0])
    assert state["companion"]["player_angle_degrees"] == 0
    sven, chicken = state["nearby"]
    # Sven is south-east of her: 14 m away, half left of where she looks
    assert (sven["distance_from_companion_meters"], sven["angle_from_companion_degrees"]) == (14, -45)
    # No direction to a character that stands on the same spot
    assert chicken["distance_from_companion_meters"] == 0 and "angle_from_companion_degrees" not in chicken

    # A companion outside the loaded area has no position, so nothing can be calculated
    far_away = state_for([20, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    assert "player_angle_degrees" not in far_away["companion"] and "facing_degrees" not in far_away["companion"]
    assert "distance_from_companion_meters" not in far_away["nearby"][0]
    # A game mod from before this round sends nine numbers for the companion, which is no longer understood
    assert "companion" not in state_for([20, 100, 0, 0, 0, 1, 10, 0, 0])


def test_weather_and_time_of_day():
    from src.companion import game_state
    from src.companion.state_text import describe_state

    def first_line(world_values: list[int], indoors: int) -> str:
        state = game_state.read_game_state({
            game_state.KEY_LOCATION: "Riverwood",
            game_state.KEY_PLAYER_VALUES: [100, 0, 0, 0, 0, 0, indoors],
            game_state.KEY_WORLD_VALUES: world_values,
        })
        lines = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines()
        # A game mod that sends so few numbers is older than the server, and the text starts with a warning about that
        assert lines[0].startswith("Warning: the game mod and this server do not fit together (the mod speaks protocol 0, ")
        return lines[1]

    assert first_line([3, 875], 0) == "Riverwood (outdoors), 14:35, snow"
    assert first_line([0, 5], 0) == "Riverwood (outdoors), 00:05, clear"
    # Indoors nobody sees the weather, and -1 means the game does not know it
    assert first_line([2, 1439], 1) == "Riverwood (indoors), 23:59"
    assert first_line([-1, 720], 0) == "Riverwood (outdoors), 12:00"


def test_quests_of_the_journal():
    from src.companion import game_state
    from src.companion.state_text import describe_state

    def quest_lines(world_values: list[int], quests: list[str] | None) -> list[str]:
        raw: dict = {game_state.KEY_LOCATION: "Riverwood", game_state.KEY_WORLD_VALUES: world_values}
        if quests is not None:
            raw[game_state.KEY_QUESTS] = quests
        state = game_state.read_game_state(raw)
        lines = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines()
        # What comes after the characters nearby
        return lines[lines.index("Nearby: nobody") + 1:]

    def quest(tracked: int, quest_type: int, name: str, *objectives: str) -> str:
        return "\t".join([str(tracked), str(quest_type), name, *objectives])

    # Without the plugin of the game mod (old mod with two numbers, or version 0) nothing is said about quests
    assert quest_lines([0, 600], None) == []
    assert quest_lines([0, 600, 0], [quest(1, 1, "Unbound", "Escape Helgen")]) == []
    # With the plugin, no list from the game means that the journal has no open quests
    assert quest_lines([0, 600, 1], None) == ["Quests: none"]

    state = game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 1], game_state.KEY_QUESTS: [
        quest(1, 1, "Before the Storm", "Talk to the Jarl of Whiterun"), "broken line", quest(0, 8, "Empty quest")]})
    assert state["quests"] == [{"name": "Before the Storm", "tracked": True, "type": 1, "miscellaneous": False,
                                "objectives": ["Talk to the Jarl of Whiterun"]}]

    # Small objectives (type 6, or a quest without a name) share one line, however many quests they come from
    assert quest_lines([0, 600, 1], [
        quest(0, 6, "Hidden name", "Visit the inn"), quest(1, 8, "The Golden Claw", "Find the claw", "Bring it back"),
        quest(0, 0, "", "Talk to Alvor"),
    ]) == ["Quests:", "- The Golden Claw (tracked): Find the claw; Bring it back", "- Miscellaneous: Visit the inn; Talk to Alvor"]

    # A long journal is cut off, and so is a long list of small objectives
    many = [quest(0, 8, f"Quest {number}", "Do it") for number in range(11)] + [quest(0, 6, "", f"Errand {number}") for number in range(8)]
    lines = quest_lines([0, 600, 1], many)
    assert len(lines) == 11
    assert lines[8] == "- Quest 7: Do it"
    assert lines[9] == "- and 3 more"
    assert lines[10] == "- Miscellaneous: Errand 0; Errand 1; Errand 2; Errand 3; Errand 4; Errand 5; and 2 more"


def test_heard_lines_are_kept_until_the_ai_asks_and_given_only_once():
    from src.companion import game_state
    from src.companion.fake_game import build_state

    def state_text(client: TestClient) -> list[str]:
        return tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {}})).splitlines()

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(has_heard_lines=True)})
        # A later state without lines must not wipe out what has not been given to the AI yet
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        first = state_text(client)
        second = state_text(client)
        debug = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True}}))
    # The player picked a line in the dialogue menu, the guard answered, and the own character is 'You'.
    # The ages have grown by the seconds the server waited for the game, so only their order is checked
    heard = first[first.index("Heard:") + 1:]
    assert [line.partition(" s ago, ")[2] for line in heard] == [
        "Dragonborn: What do you know about dragons?",
        "Whiterun Guard: Only what the old tales say.",
        "You: I am sworn to carry your burdens.",
    ]
    ages = [int(line.partition(" s ago, ")[0].removeprefix("- ")) for line in heard]
    assert ages[0] - ages[1] == 3 and ages[1] - ages[2] == 6 and 3 <= ages[2] < 30
    assert "Heard:" not in second
    assert debug["heard"] == [] and debug["state"]["hearing"] is True and "heard" not in debug["state"]

    # Old game mods say nothing about listening. A broken line is left out
    assert "hearing" not in game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 1]})
    state = game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 2, 1], game_state.KEY_HEARD: [
        "5\tFF000A01\tbandit\tNever should have come here!\t", "broken", "x\t00000014\tKoi\tHello"]})
    assert state["heard"] == [{"seconds_ago": 5, "ref_id": "FF000A01", "name": "Bandit", "text": "Never should have come here!"}]
    assert game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 2, 0], game_state.KEY_HEARD: ["5\t00000014\tKoi\tHello"]}) == {
        "time": "10:00", "weather": "clear", "quests": [], "plugin_version": 2, "protocol": 0, "around_companion": False, "visible_only": False, "hearing": False, "nearby": []}


def test_heard_lines_of_one_speaker_are_joined_and_old_ones_dropped():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue

    def line(seconds_ago: int, ref_id: str, name: str, text: str) -> dict:
        return {"seconds_ago": seconds_ago, "ref_id": ref_id, "name": name, "text": text}

    async def scenario(heard: list[dict]) -> list[dict]:
        # Without a grace period the game counts as gone right after its request, so nothing waits for it here
        queue = CompanionCommandQueue(hold_seconds=0.05, connection_grace_seconds=0.0)
        await queue.poll({"location": "Whiterun", "heard": heard}, [])
        return (await queue.get_state())["heard"]

    # A speech in three pieces becomes one line with the age of its last piece. A later remark stays its own line
    speech = asyncio.run(scenario([
        line(40, "0001A66B", "Balgruuf", "So, you were at Helgen?"), line(34, "0001A66B", "Balgruuf", "You saw this dragon with your own eyes?"),
        line(30, "00000014", "Koi", "Yes."), line(2, "0001A66B", "Balgruuf", "By Ysmir, Irileth was right!"),
    ]))
    assert [(entry["name"], entry["text"]) for entry in speech] == [
        ("Balgruuf", "So, you were at Helgen? You saw this dragon with your own eyes?"), ("Koi", "Yes."), ("Balgruuf", "By Ysmir, Irileth was right!")]
    assert [entry["seconds_ago"] for entry in speech] == [34, 30, 2]

    # Only the newest ten lines, and nothing older than five minutes
    many = [line(301 + number, "FF000A01", "Bandit", "Too old") for number in range(3)]
    many += [line(280 - 20 * number, f"FF000B{number:02d}", f"Guard {number}", f"Line {number}") for number in range(12)]
    kept = asyncio.run(scenario(many))
    assert [entry["text"] for entry in kept] == [f"Line {number}" for number in range(2, 12)]


def test_inventory_only_comes_when_asked_for():
    from src.companion.fake_game import build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(state: dict, wanted: str = "", missed: bool = False) -> list[str]:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state,
                               "inventory_wanted": wanted, "inventory_missed": missed}).splitlines()

    both = read_game_state(build_state(inventory=consts.INVENTORY_BOTH))
    assert both["inventory_own"]["total"] == 6
    # Gold is a number in the 'Gear' line and not an item in the list
    assert {"id": "0000000F", "name": "Gold", "count": 30, "group": "Other", "equipped": False} not in both["inventory_own"]["items"]
    lines = text_for(both, consts.INVENTORY_BOTH)
    assert lines[lines.index("Your inventory:"):] == [
        "Your inventory:",
        "- Weapons: Steel Sword 00013989 (equipped), Hunting Bow 00013985",
        "- Ammunition: Iron Arrow 0001397D x24",
        "- Armor and clothing: Iron Shield 00012EB6 (equipped)",
        "- Potions and food: Potion of Minor Healing 0003EADD x2",
        "Spells you know (cast_spell): Healing Hands 0004D3F2",
        "The player's inventory:",
        "- Weapons: Iron Sword 00012E49 (equipped)",
        "- Potions and food: Potion of Minor Healing 0003EADD x5",
        "- Other: Lockpick 0000000A x12",
    ]
    assert not any("inventory" in line for line in text_for(both))
    both["inventory_own"]["total"] = 143
    assert text_for(both, consts.INVENTORY_OWN)[-2] == "- The list is cut off: only the first 120 of 143 kinds of items were checked."
    assert "The player's inventory:" not in text_for(both, consts.INVENTORY_OWN)
    assert text_for(read_game_state(build_state()), "", True)[-1] == "The game did not send the inventory in time. Ask again."
    assert "inventory_own" not in read_game_state(build_state(inventory=consts.INVENTORY_PLAYER))

    # Without a follower there are no hands, and an empty hand is left out
    alone = read_game_state({"gold": [12, 0], "hands": ["R:Iron Dagger", "L:"]})
    assert alone["player"] == {"gold": 12} and "companion" not in alone
    assert "Gear: the player's gold 12" in text_for(alone)
    raw = build_state()
    raw["hands"] = ["R:", "L:"]
    assert "Gear: you hold nothing, your gold 30, the player's gold 250" in text_for(read_game_state(raw))


def test_item_commands_carry_how_many():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "needs a target" in command({"action": "equip"})["message"]
        assert "how many" in command({"action": "give", "target": "0003EADD", "detail": "a few"})["message"]
        assert command({"action": "give", "target": "0003EADD", "detail": "all"})["status"] == consts.STATUS_GAME_NOT_CONNECTED

    async def scenario() -> tuple[list[dict], list[dict]]:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.send_command(consts.ACTION_TAKE, "0003EADD", "", result_timeout_seconds=0.1, count=3))
        first = await game
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, inventory=consts.INVENTORY_OWN))
        return first, await game

    take, report = asyncio.run(scenario())
    assert (take[0][consts.KEY_COMMAND_ACTION], take[0][consts.KEY_COMMAND_COUNT]) == (consts.ACTION_TAKE, 3)
    assert (report[0][consts.KEY_COMMAND_ACTION], report[0][consts.KEY_COMMAND_DETAIL]) == (consts.ACTION_REPORT_STATE, consts.INVENTORY_OWN)


def test_fake_game_companion_walks_and_retreats_like_the_game():
    from src.companion.fake_game import is_fleeing_after, is_waiting_after, run_command, walk_target_after

    is_ok, message, target_ref_id = run_command(consts.ACTION_MOVE_TO, "000F6845", "")
    assert is_ok is True and "Whiterun Guard" in message
    assert is_waiting_after(consts.ACTION_MOVE_TO, False) is True
    assert walk_target_after(consts.ACTION_MOVE_TO, "000F6845") == "000F6845"
    assert run_command(consts.ACTION_MOVE_TO, "00000099", "")[0] is False

    # Retreating ends the fight, and the next command ends both the retreat and the walk
    is_ok, message, target_ref_id = run_command(consts.ACTION_FLEE, "", "FF000A01")
    assert is_ok is True and target_ref_id == ""
    assert is_fleeing_after(consts.ACTION_FLEE) is True
    assert is_fleeing_after(consts.ACTION_FOLLOW) is False
    assert walk_target_after(consts.ACTION_FOLLOW, "") == ""


def test_the_dead_are_listed_after_the_living():
    from src.companion import game_state
    from src.companion.state_text import describe_state

    nearby = [
        {"name": "Bandit", "ref_id": "FF000A03", "distance_meters": 2, "angle_degrees": 0, "hostile": False, "dead": True},
        {"name": "Wolf", "ref_id": "FF000A04", "distance_meters": 30, "angle_degrees": 90, "hostile": True},
        {"name": "Bandit Chief", "ref_id": "FF000A05", "distance_meters": 1, "angle_degrees": 180, "hostile": False, "dead": True},
        {"name": "Faendal", "ref_id": "0001348C", "distance_meters": 9, "angle_degrees": -90, "hostile": False},
    ]
    state = game_state.read_game_state(game_state_for(nearby))
    assert [(character["name"], character["dead"]) for character in state["nearby"]] == [
        ("Wolf", False), ("Faendal", False), ("Bandit Chief", True), ("Bandit", True)]
    lines = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines()
    assert lines[-2:] == ["FF000A05 Bandit Chief, dead, 1 m behind", "FF000A03 Bandit, dead, 2 m ahead"]
    # A list in the shape of an older game mod (four numbers per character) is left out instead of read wrongly
    raw = game_state_for(nearby[:1])
    raw[game_state.KEY_NEARBY_VALUES] = raw[game_state.KEY_NEARBY_VALUES][:4]
    assert game_state.read_game_state(raw)["nearby"] == []


def test_what_a_body_carries_only_comes_when_asked_for():
    from src.companion.fake_game import CORPSE_REF_ID, build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(state: dict, corpse_wanted: str = "") -> list[str]:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state,
                               "corpse_wanted": corpse_wanted}).splitlines()

    state = read_game_state(build_state(inventory=consts.INVENTORY_CORPSE, corpse_ref_id=CORPSE_REF_ID))
    assert state["corpse"] == {"ref_id": "FF000A03", "name": "Bandit Outlaw", "container": False, "locked": False}
    lines = text_for(state, CORPSE_REF_ID)
    # On a body the gold is something to take, so it is in the list
    assert lines[lines.index("The body of Bandit Outlaw FF000A03 carries:"):] == [
        "The body of Bandit Outlaw FF000A03 carries:",
        "- Weapons: Iron War Axe 0001359D (equipped)",
        "- Armor and clothing: Hide Armor 00013911 (equipped)",
        "- Other: Gold 0000000F x23",
    ]
    assert not any("body" in line for line in text_for(state))
    # The game sends nothing for a ref_id that is no body. A body from an earlier question does not count either
    missing = ("There is no dead character and no container with the ref_id 000F6845 around. "
               "Take the ref_id from the dead or the containers in 'Nearby'.")
    assert text_for(read_game_state(build_state(inventory=consts.INVENTORY_CORPSE, corpse_ref_id="000F6845")), "000F6845")[-1] == missing
    assert text_for(state, "000F6845")[-1] == missing
    state["inventory_corpse"]["items"] = []
    assert text_for(state, CORPSE_REF_ID)[-1] == "The body of Bandit Outlaw FF000A03 carries nothing."


def test_loot_carries_the_item_and_its_outcome_is_told_once():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import build_state, run_command, walk_target_after
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def call(tool: str, arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": tool, "arguments": arguments}))

        assert "needs a target" in call("COMMAND", {"action": "loot"})["message"]
        assert "ID of the item" in call("COMMAND", {"action": "loot", "target": "FF000A03", "detail": "the axe"})["message"]
        assert call("COMMAND", {"action": "loot", "target": "FF000A03", "detail": "all"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        assert "not a ref_id" in call("STATE", {"corpse": "that bandit"})["message"]

    async def commands() -> tuple[list[dict], list[dict]]:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.send_command(consts.ACTION_LOOT, "FF000A03", "0001359D", result_timeout_seconds=0.1, item="0001359D"))
        first = await game
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, inventory=consts.INVENTORY_OWN, corpse="FF000A03"))
        return first, await game

    loot, report = asyncio.run(commands())
    assert (loot[0][consts.KEY_COMMAND_TARGET_HIGH], loot[0][consts.KEY_COMMAND_TARGET_LOW]) == (0xFF00, 0x0A03)
    assert (loot[0][consts.KEY_COMMAND_ITEM_HIGH], loot[0][consts.KEY_COMMAND_ITEM_LOW]) == (0x0001, 0x359D)
    # Asking about a body replaces asking for an inventory
    assert (report[0][consts.KEY_COMMAND_DETAIL], report[0][consts.KEY_COMMAND_TARGET]) == (consts.INVENTORY_CORPSE, "FF000A03")
    assert (report[0][consts.KEY_COMMAND_ITEM_HIGH], report[0][consts.KEY_COMMAND_ITEM_LOW]) == (0, 0)

    async def outcome() -> tuple[dict, dict]:
        queue = CompanionCommandQueue(hold_seconds=0.05, connection_grace_seconds=0.0)
        await queue.poll(read_game_state(build_state(report="You took 1 Iron War Axe from the body of Bandit Outlaw.")), [])
        # A later state without an outcome must not wipe out what the AI has not been told yet
        await queue.poll(read_game_state(build_state()), [])
        return await queue.get_state(), await queue.get_state()

    first, second = asyncio.run(outcome())
    assert first["reports"] == ["You took 1 Iron War Axe from the body of Bandit Outlaw."] and second["reports"] == []
    assert "report" not in first["state"]
    assert "Since your last STATE: You took 1 Iron War Axe from the body of Bandit Outlaw." in describe_state(first).splitlines()
    assert not any("Since your last STATE" in line for line in describe_state(second).splitlines())

    # The fake game answers like the game mod
    assert run_command(consts.ACTION_LOOT, "FF000A03", "")[0] is True
    assert "alive" in run_command(consts.ACTION_LOOT, "000F6845", "")[1]
    assert run_command(consts.ACTION_LOOT, "00000099", "")[0] is False
    assert walk_target_after(consts.ACTION_LOOT, "FF000A03") == "FF000A03"


def test_quest_targets_and_places():
    from src.companion import game_state
    from src.companion.state_text import describe_state

    def lines_for(raw: dict) -> list[str]:
        state = game_state.read_game_state(raw)
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines()

    def line(*parts: object) -> str:
        return "\t".join(str(part) for part in parts)

    raw: dict = {
        game_state.KEY_WORLD_VALUES: [0, 600, 3, 0],
        # The second line is broken and left out, so the third quest of the game is the second one here
        game_state.KEY_QUESTS: [line(1, 1, "Bleak Falls Barrow", "Retrieve the Dragonstone", "Talk to Farengar"), "broken line",
                                line(1, 6, "", "Bring the claw to Lucan")],
        game_state.KEY_QUEST_TARGETS: [
            line(0, 0, 0, 35, 90, "Dragonstone", ""), line(0, 1, 2, 0, 0, "Farengar Secret-Fire", "Dragonsreach"),
            line(2, 0, 1, 12, -90, "Lucan Valerius", "Riverwood Trader"), line(2, 0, 2, 0, 0, "", ""),
            # Left out: a quest and an objective that do not exist, an unknown kind, too few parts, no number
            line(1, 0, 0, 5, 0, "Nobody", ""), line(0, 7, 0, 5, 0, "Nobody", ""), line(0, 0, 9, 5, 0, "Nobody", ""),
            line(0, 0, 0, 5, 0, "Nobody"), line(0, 0, 0, "near", 0, "Nobody", ""),
        ],
    }
    state = game_state.read_game_state(raw)
    assert state["quests"][0]["targets"] == [
        {"objective": 0, "kind": "here", "name": "Dragonstone", "distance_meters": 35, "angle_degrees": 90},
        {"objective": 1, "kind": "elsewhere", "name": "Farengar Secret-Fire", "place": "Dragonsreach"},
    ]
    assert lines_for(raw)[-3:] == [
        "Quests:",
        "- Bleak Falls Barrow (tracked): Retrieve the Dragonstone [target Dragonstone: 35 m right]; "
        "Talk to Farengar [target Farengar Secret-Fire: not in this area, in Dragonsreach]",
        "- Miscellaneous: Bring the claw to Lucan [target Lucan Valerius: behind the door to Riverwood Trader, 12 m left] "
        "[target: not in this area]",
    ]
    # No list of targets (an older plugin, or no tracked quest has one) leaves the quests as they were
    del raw[game_state.KEY_QUEST_TARGETS]
    assert "targets" not in game_state.read_game_state(raw)["quests"][0]
    # A newer plugin also sends the ref ID to walk to: of the target itself, or of the door to it
    raw[game_state.KEY_QUEST_TARGETS] = [
        line(0, 0, 0, 35, 90, "Dragonstone", "", "000D1234"), line(0, 1, 2, 0, 0, "Farengar Secret-Fire", "Dragonsreach", "00000000"),
        line(2, 0, 1, 12, -90, "Lucan Valerius", "Riverwood Trader", "0001A6F0"),
    ]
    targets = game_state.read_game_state(raw)["quests"][0]["targets"]
    assert targets[0]["ref_id"] == "000D1234" and "ref_id" not in targets[1]
    assert lines_for(raw)[-2:] == [
        "- Bleak Falls Barrow (tracked): Retrieve the Dragonstone [target Dragonstone 000D1234: 35 m right]; "
        "Talk to Farengar [target Farengar Secret-Fire: not in this area, in Dragonsreach]",
        "- Miscellaneous: Bring the claw to Lucan [target Lucan Valerius: behind the door to Riverwood Trader 0001A6F0, 12 m left]",
    ]

    # Places: map markers and doors each get a line. Indoors the plugin sends doors only
    places = {game_state.KEY_PLACES: [
        line(0, 120, -60, 13, 1, "Pelagia Farm"), line(0, 290, 10, 39, 0, "Dragonsreach"), line(0, 150, 100, 99, 0, "Odd Marker"),
        line(1, 20, 90, 0, 0, "Warmaiden's"), line(1, 44, 180, 0, 0, "Skyrim"),
        # Left out: an unknown kind, no name, too few parts
        line(7, 5, 0, 0, 0, "Nothing"), line(1, 5, 0, 0, 0, " "), line(1, 5, 0, 0),
    ]}
    assert game_state.read_game_state(places)["places"][:2] == [
        {"kind": "marker", "name": "Pelagia Farm", "distance_meters": 120, "angle_degrees": -60, "type": "farm", "known": True},
        {"kind": "marker", "name": "Dragonsreach", "distance_meters": 290, "angle_degrees": 10, "type": "castle", "known": False},
    ]
    assert lines_for(places)[-2:] == [
        "Places: Pelagia Farm (farm) 120 m ahead-left, Dragonsreach (castle) 290 m ahead, Odd Marker 150 m right",
        "Doors: Warmaiden's 20 m right, Skyrim 44 m behind",
    ]
    assert lines_for({game_state.KEY_PLACES: [line(1, 6, 0, 0, 0, "Dragonsreach, Jarl's Quarters")]})[-1] == "Doors: Dragonsreach, Jarl's Quarters 6 m ahead"
    # A newer plugin sends the ref ID of every place as its second part, for the commands move_to and look_at
    newer_places = {game_state.KEY_PLACES: [
        line(0, "000C0001", 120, -60, 13, 1, "Pelagia Farm"), line(1, "0001A6F0", 20, 90, 0, 0, "Warmaiden's"),
        # Left out: what stands in the place of the ref ID is none
        line(1, "the door", 20, 90, 0, 0, "Warmaiden's"),
    ]}
    assert game_state.read_game_state(newer_places)["places"][0] == {
        "kind": "marker", "name": "Pelagia Farm", "distance_meters": 120, "angle_degrees": -60, "ref_id": "000C0001", "type": "farm", "known": True}
    assert lines_for(newer_places)[-2:] == ["Places: Pelagia Farm 000C0001 (farm) 120 m ahead-left", "Doors: Warmaiden's 0001A6F0 20 m right"]
    # An older game mod sends no places at all, and then nothing is said about them
    assert "places" not in game_state.read_game_state({game_state.KEY_LOCATION: "Riverwood"})
    assert game_state.read_game_state({game_state.KEY_PLACES: []})["places"] == []


def test_things_only_come_when_asked_for_and_the_lists_have_a_limit():
    import asyncio

    from src.companion import game_state
    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    def lines_from_nearby(raw: dict, **result: object) -> list[str]:
        state = game_state.read_game_state(raw)
        lines = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **result}).splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("Nearby"))
        return lines[start:]

    def line(*parts: object) -> str:
        return "\t".join(str(part) for part in parts)

    things = [line(0, "0005A3F1", 3, -90, 1, "wooden bench"), line(1, "0005A400", 4, 30, 0, "Chest"), line(2, "FF00B001", 1, 0, 0, "Sweet Roll"),
              # Left out: an unknown kind, no ref ID, no name, too few parts
              line(5, "0005A3F1", 3, 0, 0, "Odd"), line(0, "bench", 3, 0, 0, "Bench"), line(0, "0005A3F1", 3, 0, 0, ""), line(0, "0005A3F1", 3)]
    state = game_state.read_game_state({game_state.KEY_THINGS: things})
    assert state["things"] == [
        {"kind": "furniture", "ref_id": "0005A3F1", "name": "Wooden bench", "distance_meters": 3, "angle_degrees": -90, "seat": True},
        {"kind": "container", "ref_id": "0005A400", "name": "Chest", "distance_meters": 4, "angle_degrees": 30, "locked": False},
        {"kind": "item", "ref_id": "FF00B001", "name": "Sweet Roll", "distance_meters": 1, "angle_degrees": 0},
    ]
    # Things the AI did not ask for are not shown, even if the kept state carries some
    assert lines_from_nearby({game_state.KEY_THINGS: things}) == ["Nearby: nobody"]
    # The sweet roll is nearest, but furniture and containers come before loose items
    assert lines_from_nearby({game_state.KEY_THINGS: things}, things_wanted=True) == [
        "Nearby: nobody", "Things:", "0005A3F1 Wooden bench, seat, 3 m left", "0005A400 Chest, container, 4 m ahead-right",
        "FF00B001 Sweet Roll, item, 1 m ahead"]
    assert lines_from_nearby({}, things_wanted=True) == ["Nearby: nobody", "Things: nothing within about 10 meters"]
    assert lines_from_nearby({}, things_missed=True)[-1] == "The game did not send the things in time. Ask again."
    assert "things" not in game_state.read_game_state({game_state.KEY_LOCATION: "Riverwood"})

    # Each list has its own limit: ten lines if the AI names no number
    crowd = game_state_for([{"name": f"Guest {number}", "ref_id": f"000B00{number:02d}", "distance_meters": 2 + number, "angle_degrees": 0,
                             "hostile": False} for number in range(14)])
    crowd[game_state.KEY_THINGS] = [line(2, f"000C00{number:02d}", 1 + number, 0, 0, f"Cup {number}") for number in range(14)]
    assert len(lines_from_nearby(crowd)) == 1 + 10
    assert len(lines_from_nearby(crowd, things_wanted=True)) == 2 * (1 + 10)
    short = lines_from_nearby(crowd, things_wanted=True, max_lines=2)
    assert short == ["Nearby:", "000B0000 Guest 0, 2 m ahead", "000B0001 Guest 1, 3 m ahead", "Things:", "000C0000 Cup 0, item, 1 m ahead",
                     "000C0001 Cup 1, item, 2 m ahead"]
    assert len(lines_from_nearby(crowd, things_wanted=True, max_lines=30)) == 2 * (1 + 14)

    # The game learns with the request for the state how long the lists may get and whether things are wanted
    async def requests() -> tuple[dict, dict]:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1))
        plain = (await game)[0]
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, things=True, max_lines=25))
        return plain, (await game)[0]

    plain, wide = asyncio.run(requests())
    assert (plain[consts.KEY_COMMAND_COUNT], plain[consts.KEY_COMMAND_THINGS]) == (10, 0)
    assert (plain[consts.KEY_COMMAND_AROUND], wide[consts.KEY_COMMAND_AROUND]) == (0, 0)
    assert (wide[consts.KEY_COMMAND_COUNT], wide[consts.KEY_COMMAND_THINGS]) == (25, 1)

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        # A number out of range is brought back into it, and something that is no number counts as none
        for wanted, used in ((500, 30), (0, 1), ("many", 10)):
            answer = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True, "max": wanted}}))
            assert answer["max_lines"] == used
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"things", "max"} <= set(tools[1]["inputSchema"]["properties"])

    # The fake game mod speaks the protocol of this server, so there is no warning
    assert not describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0,
                               "state": game_state.read_game_state(build_state())}).startswith("Warning")
    newer = build_state()
    newer[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PROTOCOL] = game_state.GAME_PROTOCOL + 1
    assert describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0,
                           "state": game_state.read_game_state(newer)}).startswith("Warning: the game mod and this server do not fit together")


def test_sitting_and_looting_a_container():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import (CHEST_REF_ID, LOCKED_CHEST_REF_ID, SEAT_REF_ID, build_state, is_sitting_after,
                                         is_waiting_after, run_command)
    from src.companion.game_state import read_game_state
    from src.companion.mcp_route import _find_nearest_seat
    from src.companion.state_text import describe_state

    def text_for(state: dict, corpse_wanted: str = "") -> list[str]:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state,
                               "corpse_wanted": corpse_wanted}).splitlines()

    # What a chest holds comes like what a body carries. Nobody can look into a locked one
    chest = read_game_state(build_state(inventory=consts.INVENTORY_CORPSE, corpse_ref_id=CHEST_REF_ID))
    assert chest["corpse"] == {"ref_id": "0005A400", "name": "Chest", "container": True, "locked": False}
    assert text_for(chest, CHEST_REF_ID)[-3:] == [
        "Chest 0005A400 holds:", "- Potions and food: Potion of Minor Healing 0003EADD", "- Other: Gold 0000000F x40"]
    chest["inventory_corpse"]["items"] = []
    assert text_for(chest, CHEST_REF_ID)[-1] == "Chest 0005A400: the game lists nothing in it."
    locked = read_game_state(build_state(inventory=consts.INVENTORY_CORPSE, corpse_ref_id=LOCKED_CHEST_REF_ID))
    assert "inventory_corpse" not in locked and text_for(locked, LOCKED_CHEST_REF_ID)[-1] == "Strongbox 0005A401 is locked."
    # A game mod from before the containers sends the ref ID only, and that is a body
    older = build_state(inventory=consts.INVENTORY_CORPSE, corpse_ref_id="FF000A03")
    older["corpse"] = older["corpse"][:1]
    assert read_game_state(older)["corpse"]["container"] is False

    # The companion sits: the line 'You' says so. A game mod from before sitting sends twelve numbers and says nothing about it
    seated = read_game_state(build_state(True, is_sitting=True))
    assert seated["companion"]["sitting"] is True
    assert text_for(seated)[2] == "You, Lydia: health 100%, 3 m behind-left, faces south, player left of you, sitting"
    assert text_for(read_game_state(build_state(True, "", False, SEAT_REF_ID)))[2].endswith("walking to Wooden Bench 0005A3F1")
    older = build_state(True)
    older["companion"] = older["companion"][:12]
    assert "sitting" not in read_game_state(older)["companion"]

    # sit without a target takes the seat that is nearest to the companion, and what was heard stays kept for the AI
    async def nearest_seat(state: dict | None) -> tuple[str, list[dict]]:
        queue = CompanionCommandQueue(hold_seconds=0.05, connection_grace_seconds=0.0)
        await queue.poll(state, [])
        return (await _find_nearest_seat(queue))[0], (await queue.get_state())["heard"]

    seat, heard = asyncio.run(nearest_seat(read_game_state(build_state(has_heard_lines=True))))
    assert seat == SEAT_REF_ID and len(heard) == 3
    assert asyncio.run(nearest_seat({"location": "Whiterun", "things": [{"kind": "container", "ref_id": "0005A400"}]}))[0] == ""
    assert asyncio.run(nearest_seat(None))[0] == ""

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        # Without a game there is no state to pick a seat from
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "sit"}}))
        assert answer["status"] == consts.STATUS_FAILED and "no seat" in answer["message"]
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "sit", "target": SEAT_REF_ID}}))
        assert answer["status"] == consts.STATUS_GAME_NOT_CONNECTED

    # The fake game answers like the game mod
    assert run_command(consts.ACTION_SIT, SEAT_REF_ID, "")[0] is True
    assert "nothing to sit on" in run_command(consts.ACTION_SIT, CHEST_REF_ID, "")[1]
    assert run_command(consts.ACTION_LOOT, CHEST_REF_ID, "")[0] is True
    assert run_command(consts.ACTION_LOOT, LOCKED_CHEST_REF_ID, "")[1] == "Strongbox is locked."
    # Sitting is the work of a package of the game mod: the companion is not told to wait for it
    assert is_sitting_after(consts.ACTION_SIT, False) is True and is_waiting_after(consts.ACTION_SIT, False) is False
    assert is_sitting_after(consts.ACTION_WAIT, True) is True and is_sitting_after(consts.ACTION_GIVE, True) is True
    assert is_sitting_after(consts.ACTION_FOLLOW, True) is False and is_sitting_after(consts.ACTION_ATTACK, True) is False


def test_weapon_look_and_the_lists_as_the_companion_sees_them():
    import asyncio

    from src.companion import game_state
    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import DOOR_REF_ID, PLAYER_REF_ID, build_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        state = game_state.read_game_state(raw)
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **result}).splitlines()

    # Weapons out, and the companion keeps its eyes on someone because of look_at
    armed = text_for(build_state(are_weapons_drawn=True, look_target_ref_id="000F6845"))
    assert armed[1] == "Player Dragonborn: health 85%, not fighting, weapon drawn, faces north-east, position 23500,-7900,-3300"
    assert armed[2].endswith("following, weapon drawn, looking at Whiterun Guard 000F6845")
    # The player has no line in 'Nearby', a door has its name from 'Doors'
    assert text_for(build_state(look_target_ref_id=PLAYER_REF_ID))[2].endswith("following, looking at the player")
    assert text_for(build_state(look_target_ref_id=DOOR_REF_ID))[2].endswith("following, looking at Dragonsreach 0001A6F4")
    assert "weapon drawn" not in "".join(text_for(build_state()))

    # A game mod from before this sends fewer numbers and says nothing about weapons and looks
    older = build_state(are_weapons_drawn=True, look_target_ref_id="000F6845")
    older[game_state.KEY_PLAYER_VALUES] = older[game_state.KEY_PLAYER_VALUES][:7]
    older[game_state.KEY_COMPANION_VALUES] = older[game_state.KEY_COMPANION_VALUES][:13]
    older[game_state.KEY_WORLD_VALUES] = older[game_state.KEY_WORLD_VALUES][:5]
    older_state = game_state.read_game_state(older)
    assert "weapon_drawn" not in older_state["player"] and "weapon_drawn" not in older_state["companion"]
    assert "look_target_ref_id" not in older_state["companion"] and older_state["companion"]["sitting"] is False
    assert older_state["around_companion"] is False and older_state["player"]["health_percent"] == 85

    # Seen from the player every line has both views. If the game measured from the companion, there is only that one
    assert game_state.read_game_state(build_state())["around_companion"] is False
    around_state = game_state.read_game_state(build_state(is_around_companion=True))
    assert around_state["around_companion"] is True
    assert "distance_from_companion_meters" not in around_state["nearby"][0]
    # Where the player is does not depend on that
    assert around_state["companion"]["player_angle_degrees"] == game_state.read_game_state(build_state())["companion"]["player_angle_degrees"]
    around = text_for(build_state(is_around_companion=True), things_wanted=True, around_wanted=True)
    start = around.index("The lists below show what is around you, not around the player.")
    assert around[start + 1:start + 3] == ["Nearby:", "FF000A01 Bandit, from you 12 m ahead-left, hostile"]
    assert "0005A3F1 Wooden Bench, seat, from you 3 m left" in around
    assert "Places (from you): Pelagia Farm 000C0001 (farm) 120 m ahead-left, Whiterun Stables 000C0002 (stable) 260 m behind" in around
    assert "Doors (from you): Warmaiden's 0001A6F0 20 m right, Dragonsreach 0001A6F4 140 m ahead-left" in around
    assert ("- Before the Storm (tracked): Talk to the Jarl of Whiterun "
            "[target Jarl Balgruuf: behind the door to Dragonsreach 0001A6F4, from you 140 m ahead-left]") in around
    empty = build_state(is_around_companion=True)
    empty[game_state.KEY_THINGS] = []
    assert "Things: nothing within about 10 meters of you" in text_for(empty, things_wanted=True)
    # The AI asked for its own surroundings, but the game listed those of the player (the companion is far away, or the mod is older)
    refused = text_for(build_state(), around_wanted=True)
    assert any(line.startswith("The game listed what is around the player") for line in refused)
    assert not any(line.startswith("The game listed") for line in text_for(build_state()))

    # The game learns with the request for the state from whom the lists should be seen
    async def request() -> dict:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, around_companion=True))
        return (await game)[0]

    assert asyncio.run(request())[consts.KEY_COMMAND_AROUND] == 1

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        # The game is gone by now, so nothing fresh comes and nothing is said about whose surroundings these are
        answer = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True, "around": "you"}}))
        assert answer["around_wanted"] is False and answer["state"]["around_companion"] is False
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[1]["inputSchema"]["properties"]["around"]["enum"] == ["player", "you"]


def test_look_at_and_gesture():
    from pathlib import Path

    from src.companion.fake_game import (DOOR_REF_ID, is_sitting_after, look_target_after, run_command, walk_target_after)
    from src.companion.gestures import GESTURES, load_gestures, pick_animation

    # The gestures come from the table of Mantella. Animations that are in Mantella.esp are left out
    assert GESTURES["wave"] == ["0003EA32"] and GESTURES["agree"] == ["000FFA0C"]
    assert "deescalate" not in GESTURES
    assert len(GESTURES["applaud"]) == 4 and pick_animation(" Applaud ") in GESTURES["applaud"]
    assert pick_animation("come this way") == "000F6CCF"
    assert pick_animation("moonwalk") == ""
    assert load_gestures(Path("no_such_table.csv")) == {}

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "name of the gesture" in command({"action": "gesture"})["message"]
        unknown = command({"action": "gesture", "detail": "moonwalk"})
        assert unknown["status"] == consts.STATUS_FAILED and "wave" in unknown["message"]
        # What is fine is refused only because no game is there
        assert command({"action": "gesture", "detail": "wave"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        assert command({"action": "gesture", "detail": "stop"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        # look_at without a target frees the eyes again, so it needs none
        assert command({"action": "look_at"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        assert "not a ref_id" in command({"action": "look_at", "target": "the door"})["message"]
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"look_at", "gesture"} <= set(tools[0]["inputSchema"]["properties"]["action"]["enum"])
        assert "come_this_way" in manual_text()

    # The game gets the ID of the animation in two halves, and the name only for its answer
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You do the gesture 'wave'."}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}, consts.KEY_RESULTS: results})

        game_thread = threading.Thread(target=game, daemon=True)
        game_thread.start()
        time.sleep(0.3)
        response = rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "gesture", "detail": "Wave", "target": "000F6845"}})
        assert tool_payload(response) == {"status": consts.STATUS_DONE, "message": "You do the gesture 'wave'."}
        sent = received_commands[0]
        assert (sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == ("gesture", "wave", "")
        assert (sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (0x0003, 0xEA32)

    # The fake game answers like the game mod. A walk can lead to a door now
    assert run_command(consts.ACTION_MOVE_TO, DOOR_REF_ID, "")[1].startswith("You walk to Dragonsreach and wait there.")
    assert run_command(consts.ACTION_LOOK_AT, "", "")[1] == "You look where you like again."
    assert "Whiterun Guard" in run_command(consts.ACTION_LOOK_AT, "000F6845", "")[1]
    assert run_command(consts.ACTION_LOOK_AT, "00000099", "")[0] is False
    assert run_command(consts.ACTION_GESTURE, "", "", "0003EA32", "wave")[0] is True
    assert run_command(consts.ACTION_GESTURE, "", "", "", "stop")[1] == "You stand normally again."
    assert run_command(consts.ACTION_GESTURE, "", "", "00000099", "moonwalk")[0] is False
    # The look holds until look_at comes without a target. A look and a gesture end neither a walk nor a rest on a seat
    assert look_target_after(consts.ACTION_LOOK_AT, "000F6845", "") == "000F6845"
    assert look_target_after(consts.ACTION_FOLLOW, "", "000F6845") == "000F6845"
    assert look_target_after(consts.ACTION_LOOK_AT, "", "000F6845") == ""
    assert walk_target_after(consts.ACTION_GESTURE, "", "000F6845") == "000F6845"
    assert is_sitting_after(consts.ACTION_LOOK_AT, True) is True and is_sitting_after(consts.ACTION_GESTURE, True) is True


def test_say_makes_the_sound_and_tells_the_game_which_line_to_speak(tmp_path):
    import sys
    import wave

    from src.companion.fake_game import build_state, is_sitting_after, run_command
    from src.companion.game_state import read_game_state
    from src.companion.voice import LINE_FILES, CompanionVoice, clean_text, find_voice_folder

    # A stand-in for Piper that talks the same way: it answers "Model loaded" and writes out.wav into its working folder
    stand_in = tmp_path / "stand_in_piper.py"
    stand_in.write_text(
        "import sys, wave\n"
        "for line in sys.stdin:\n"
        "    if line.startswith('load_model'):\n"
        "        print('Model loaded', flush=True)\n"
        "    elif line.startswith('synthesize '):\n"
        "        with wave.open('out.wav', 'wb') as sound:\n"
        "            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(22050)\n"
        "            sound.writeframes(b'\\x00\\x00' * len(line))\n", encoding="utf-8")
    game_folder = tmp_path / "game"
    voice = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(stand_in)])
    voice_folder = game_folder / "Data" / "Sound" / "Voice" / "KI_MCP_Gefaehrte.esp" / "FemaleEvenToned"

    def frames(file_name: str) -> int:
        with wave.open(str(voice_folder / file_name), "rb") as sound:
            return sound.getnframes()

    # Without the folder the game mod's installer makes, there is nowhere to put the sound
    assert voice.describe_setup().startswith("off:")
    is_ok, problem, line = voice.speak("Hello there.", "FemaleEvenToned")
    assert is_ok is False and "build.py --install" in problem
    voice_folder.mkdir(parents=True)
    assert voice.describe_setup().startswith("on:")
    # The two lines of the game mod take turns, because the game may still hold on to the file it played last
    assert voice.speak("Hello there.", "FemaleEvenToned") == (True, "", 1)
    assert voice.speak("I am sworn to carry your burdens, my Thane.", "FemaleEvenToned") == (True, "", 2)
    assert voice.speak("Short.", "FemaleEvenToned") == (True, "", 1)
    assert frames(LINE_FILES[1]) > frames(LINE_FILES[0]) > 0
    assert voice.speak("  * ", "FemaleEvenToned")[:2] == (False, "There is nothing to say.")
    assert clean_text("*She waves.*\nHello,   you.") == "She waves. Hello, you."
    # Mantella's list of characters knows the voice of the characters of the game by their names
    assert find_voice_folder("lydia") == "FemaleEvenToned" and find_voice_folder("Nobody At All") == ""
    assert read_game_state(build_state())["companion"]["voice"] == "FemaleEvenToned"
    # The game mod's own line carries '...' as its text. That is not something the AI has to hear
    own_line = build_state()
    own_line["heard"] = ["2\t000A2C94\tLydia\t...", "1\t000F6845\tWhiterun Guard\tNo lollygaggin'."]
    assert [heard["name"] for heard in read_game_state(own_line)["heard"]] == ["Whiterun Guard"]

    def command(client: TestClient, arguments: dict) -> dict:
        return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        assert "without a voice" in command(client, {"action": "say", "detail": "Hello."})["message"]
    with TestClient(create_app(TOKEN, hold_seconds=0.1, voice=voice)) as client:
        assert "text to speak" in command(client, {"action": "say"})["message"]
        assert "too long" in command(client, {"action": "say", "detail": "word " * 200})["message"]
        assert "Get STATE first" in command(client, {"action": "say", "detail": "Hello."})["message"]
        # An older plugin does not tell the voice, and nobody of that name is in Mantella's list
        stranger = build_state()
        del stranger["companion_voice"]
        stranger["companion_name"] = "Nobody At All"
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: stranger})
        assert "which voice" in command(client, {"action": "say", "detail": "Hello."})["message"]
        # The sound is made, and only then the game turns out not to pick the command up
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        time.sleep(0.2)
        before = (voice_folder / LINE_FILES[1]).stat().st_mtime_ns
        assert command(client, {"action": "say", "detail": "Follow me."})["status"] in (consts.STATUS_NOT_DELIVERED, consts.STATUS_GAME_NOT_CONNECTED)
        assert (voice_folder / LINE_FILES[1]).stat().st_mtime_ns > before
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "say" in tools[0]["inputSchema"]["properties"]["action"]["enum"]

    # The game is told the number of the line, not the text
    with TestClient(create_app(TOKEN, hold_seconds=5, voice=voice)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You say it aloud."}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        said = command(client, {"action": "say", "detail": "Watch out!"})
        # The answer tells how long the sentence takes, so that the AI does not talk into it
        assert said["status"] == consts.STATUS_DONE and said["message"].startswith("You say it aloud. You speak for about ") and "WAIT_FOR_EVENT" in said["message"]
        sent = received_commands[0]
        # The text goes along for the subtitle of the line
        assert (sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_DETAIL]) == ("say", "Watch out!")
        assert sent[consts.KEY_COMMAND_COUNT] in (1, 2)

    assert run_command(consts.ACTION_SAY, "", "") == (True, "You say it aloud.", "")
    assert is_sitting_after(consts.ACTION_SAY, True) is True


def test_following_someone_going_through_a_door_and_a_look_on_the_face():
    from src.companion import game_state
    from src.companion.fake_game import (DOOR_REF_ID, build_state, expression_after, is_sitting_after, is_waiting_after,
                                         leader_after, run_command, walk_target_after)
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        state = game_state.read_game_state(raw)
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **result}).splitlines()

    # Whom the companion follows instead of the player, and the look on its face
    assert text_for(build_state(leader_ref_id="000F6845"))[2].endswith("player left of you, following Whiterun Guard 000F6845")
    assert text_for(build_state(expression="happy"))[2].endswith("player left of you, following, face happy")
    # A game mod from before this sends fifteen numbers about the companion and says nothing about a leader
    older = build_state(leader_ref_id="000F6845")
    older[game_state.KEY_COMPANION_VALUES] = older[game_state.KEY_COMPANION_VALUES][:15]
    assert "leader_ref_id" not in game_state.read_game_state(older)["companion"]

    # The companion went through a door on its own: it is somewhere the game has not loaded
    away = build_state(True)
    away[game_state.KEY_COMPANION_VALUES][5] = 0
    assert text_for(away)[2] == "You, Lydia: health 100%, far away from the player, waiting"
    away[game_state.KEY_COMPANION_PLACE] = "The Bannered Mare"
    assert text_for(away)[2] == "You, Lydia: health 100%, in The Bannered Mare, not where the player is, waiting"
    # Looking around there, it gets the characters from the plugin of the game mod: only those the game keeps in memory
    away[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_AROUND] = 1
    away[game_state.KEY_NEARBY_LINES] = ["000A0002\t9\t30\t1\tsaadia", "000A0001\t4\t-90\t0\tHulda", "broken line", "000A0003\tnear\t0\t0\tMikael"]
    del away[game_state.KEY_THINGS]
    assert game_state.read_game_state(away)["nearby"] == [
        {"name": "Hulda", "ref_id": "000A0001", "distance_meters": 4, "angle_degrees": -90, "hostile": False, "dead": False},
        {"name": "Saadia", "ref_id": "000A0002", "distance_meters": 9, "angle_degrees": 30, "hostile": False, "dead": True},
    ]
    inside = text_for(away, around_wanted=True)
    start = next(index for index, line in enumerate(inside) if line.startswith("The lists below show the place you are in."))
    assert "Do not take an empty list for an empty place." in inside[start]
    assert inside[start + 1:start + 4] == ["Nearby:", "000A0001 Hulda, from you 4 m left", "000A0002 Saadia, dead, from you 9 m ahead-right"]

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "needs a target" in command({"action": "enter"})["message"]
        assert "needs the look" in command({"action": "expression"})["message"]
        unknown = command({"action": "expression", "detail": "smug"})
        assert unknown["status"] == consts.STATUS_FAILED and "happy" in unknown["message"]
        assert "how strong" in command({"action": "expression", "detail": "happy 500"})["message"]
        # What is fine is refused only because no game is there
        for arguments in ({"action": "expression", "detail": "happy 50"}, {"action": "expression", "detail": "none"},
                          {"action": "enter", "target": DOOR_REF_ID}, {"action": "follow", "target": "000F6845"}, {"action": "follow"}):
            assert command(arguments)["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"enter", "expression"} <= set(tools[0]["inputSchema"]["properties"]["action"]["enum"])

    # The game gets the number of the look, and how strong it is where loot has the lower half of an item ID
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You look happy now."}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "expression", "detail": "Happy 60", "target": "000F6845"}}))
        assert answer == {"status": consts.STATUS_DONE, "message": "You look happy now."}
        sent = received_commands[0]
        assert (sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == ("expression", "happy", "")
        assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (10, 0, 60)

    # The fake game answers like the game mod
    assert "You follow Whiterun Guard now" in run_command(consts.ACTION_FOLLOW, "000F6845", "")[1]
    assert run_command(consts.ACTION_FOLLOW, "FF000A03", "")[1] == "Bandit Outlaw is dead."
    assert "not a character" in run_command(consts.ACTION_FOLLOW, DOOR_REF_ID, "")[1]
    assert run_command(consts.ACTION_FOLLOW, "", "")[0] is True
    assert run_command(consts.ACTION_ENTER, DOOR_REF_ID, "")[1].startswith("You walk to the door and go through it.")
    assert "not a door" in run_command(consts.ACTION_ENTER, "000F6845", "")[1]
    assert "not a door" in run_command(consts.ACTION_ENTER, "000C0001", "")[1]
    assert run_command(consts.ACTION_EXPRESSION, "", "", "", "none")[1] == "Your face is your own again."
    # Following someone holds through a fight and through what the companion does on the spot. Being sent somewhere ends it
    assert leader_after(consts.ACTION_FOLLOW, "000F6845", "") == "000F6845"
    assert leader_after(consts.ACTION_ATTACK, "FF000A01", "000F6845") == "000F6845"
    assert leader_after(consts.ACTION_SAY, "", "000F6845") == "000F6845"
    assert leader_after(consts.ACTION_WAIT, "", "000F6845") == "" and leader_after(consts.ACTION_FOLLOW, "", "000F6845") == ""
    assert expression_after(consts.ACTION_EXPRESSION, "happy", "") == "happy" and expression_after(consts.ACTION_FOLLOW, "", "happy") == "happy"
    assert expression_after(consts.ACTION_EXPRESSION, "none", "happy") == ""
    assert walk_target_after(consts.ACTION_ENTER, DOOR_REF_ID) == DOOR_REF_ID and is_waiting_after(consts.ACTION_ENTER, False) is True
    assert is_sitting_after(consts.ACTION_EXPRESSION, True) is True and is_sitting_after(consts.ACTION_ENTER, True) is False


def test_stats_of_the_player_only_come_when_asked_for():
    import asyncio

    from src.companion import game_state
    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        state = game_state.read_game_state(raw)
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **result}).splitlines()

    stats = game_state.read_game_state(build_state(has_stats=True))["player_stats"]
    assert (stats["level"], stats["level_up_waiting"], stats["perk_points"]) == (5, True, 2)
    assert stats["skills"][0] == {"name": "One-Handed", "level": 32} and len(stats["skills"]) == 18
    assert "player_stats" not in game_state.read_game_state(build_state())

    shown = text_for(build_state(has_stats=True), stats_wanted=True)
    start = next(index for index, line in enumerate(shown) if line.startswith("Player stats"))
    assert shown[start] == ("Player stats: level 5, a level-up is waiting in the skills menu, 2 perk points to spend, health 150, "
                            "magicka 80 (80%), stamina 120 (100%), carries 182 of 300")
    # The skills the player is best at come first
    assert shown[start + 1].startswith("Player skills: One-Handed 32, Archery 27, Sneak 26, Destruction 25, Heavy Armor 24, ")
    assert shown[start + 1].endswith("Illusion 15, Enchanting 15")
    assert shown[start + 2] == "Your stats: level 6, health 250, magicka 50 (100%), stamina 180 (100%)"
    # Stats the AI did not ask for are not shown, even if the kept state carries some
    assert not any(line.startswith("Player stats") for line in text_for(build_state(has_stats=True)))
    assert "The game did not send the stats in time. Ask again." in text_for(build_state(), stats_missed=True)
    # No level-up waits below the experience it takes, and one perk point is one point
    calm = build_state(has_stats=True)
    calm[game_state.KEY_STATS_PLAYER] = list(calm[game_state.KEY_STATS_PLAYER])
    calm[game_state.KEY_STATS_PLAYER][8:11] = [1, 120, 400]
    assert "Player stats: level 5, 1 perk point to spend, health 150" in "\n".join(text_for(calm, stats_wanted=True))
    # A list of another length (another game mod) is left out
    calm[game_state.KEY_STATS_PLAYER] = calm[game_state.KEY_STATS_PLAYER][:20]
    assert "The game did not send the stats. That needs a newer game mod." in text_for(calm, stats_wanted=True)

    # The game learns with the request for the state that the stats are wanted
    async def request(**wanted: bool) -> dict:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, **wanted))
        return (await game)[0]

    assert asyncio.run(request(stats=True))[consts.KEY_COMMAND_STATS] == 1
    assert asyncio.run(request())[consts.KEY_COMMAND_STATS] == 0

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        answer = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True, "stats": True}}))
        assert answer["stats_wanted"] is False and answer["stats_missed"] is True
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[1]["inputSchema"]["properties"]["stats"]["type"] == "boolean"


def test_visible_only_lists_what_can_be_seen():
    import asyncio

    from src.companion import game_state
    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        state = game_state.read_game_state(raw)
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **result}).splitlines()

    assert game_state.read_game_state(build_state(is_visible_only=True))["visible_only"] is True
    assert game_state.read_game_state(build_state())["visible_only"] is False
    # The game filtered the characters. Of the things only those in front stay: the bench (left) and the strongbox (behind) go
    seen = text_for(build_state(is_visible_only=True), visible_wanted=True, things_wanted=True)
    assert any(line.startswith("Only what the player can see: characters in front of the player") for line in seen)
    start = seen.index("Things:")
    assert seen[start + 1:start + 4] == ["0005A400 Chest, container, 4 m ahead-right, from you 7 m left",
                                         "0005A410 Iron Dagger, item, 2 m ahead-right, from you 5 m left",
                                         "Places: Pelagia Farm 000C0001 (farm) 120 m ahead-left"]
    shown = "\n".join(seen)
    assert "Wooden Bench" not in shown and "Strongbox" not in shown and "Grindstone" not in shown
    # The same goes for places and doors: the stables (behind) and the door to the right go, what lies in front stays.
    # The plugin of the game mod leaves out what is behind a wall, if it is new enough, and the line says which it was
    assert "Whiterun Stables" not in shown and "Warmaiden's" not in shown and "Dragonsreach" in shown
    assert "doors and places in front (the plugin of the game mod is too old to check walls for them)" in shown
    newer = build_state(is_visible_only=True)
    newer[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PLUGIN_VERSION] = game_state.PLUGIN_VERSION_WITH_SIGHT
    assert "doors and places in front with no wall in between, things within" in "\n".join(text_for(newer, visible_wanted=True))
    assert "Whiterun Stables" in "\n".join(text_for(build_state(), things_wanted=True))
    # Seen from the companion
    assert any(line.startswith("Only what you can see:") for line in
               text_for(build_state(is_around_companion=True, is_visible_only=True), visible_wanted=True, around_wanted=True))
    # The game could not filter (an older game mod, or inside a place it has not loaded): everything stays, and that is said
    unfiltered = text_for(build_state(), visible_wanted=True, things_wanted=True)
    assert any(line.startswith("The game could not check what can be seen") for line in unfiltered)
    assert "Wooden Bench" in "\n".join(unfiltered)
    # Not asked for: no line, no filter, even if the kept state was filtered
    plain = text_for(build_state(is_visible_only=True), things_wanted=True)
    assert not any(line.startswith(("Only what", "The game could not check")) for line in plain) and "Wooden Bench" in "\n".join(plain)

    async def request(**wanted: bool) -> dict:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, **wanted))
        return (await game)[0]

    assert asyncio.run(request(visible=True))[consts.KEY_COMMAND_VISIBLE] == 1
    assert asyncio.run(request())[consts.KEY_COMMAND_VISIBLE] == 0
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()})
        answer = tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"debug": True, "visible": True}}))
        assert answer["visible_wanted"] is False
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[1]["inputSchema"]["properties"]["visible"]["type"] == "boolean"


def test_make_room_carries_the_direction_and_how_far():
    from src.companion.fake_game import (is_sitting_after, is_waiting_after, leader_after, run_command, walk_target_after)

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        unknown = command({"action": "make_room", "detail": "up"})
        assert unknown["status"] == consts.STATUS_FAILED and "back, left, right" in unknown["message"]
        assert "how far in meters" in command({"action": "make_room", "detail": "left 50"})["message"]
        assert "how far in meters" in command({"action": "make_room", "detail": "left far"})["message"]
        # What is fine is refused only because no game is there
        for arguments in ({"action": "make_room"}, {"action": "make_room", "detail": "Right"}, {"action": "make_room", "detail": "back 10"}):
            assert command(arguments)["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "make_room" in tools[0]["inputSchema"]["properties"]["action"]["enum"]

    # The game gets the number of the direction, and how far where loot has the lower half of an item ID
    def sent_for(arguments: dict) -> dict:
        with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
            received_commands: list[dict] = []

            def game():
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You step."}]
                client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

            threading.Thread(target=game, daemon=True).start()
            time.sleep(0.3)
            answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))
            assert answer == {"status": consts.STATUS_DONE, "message": "You step."}
            return received_commands[0]

    sent = sent_for({"action": "make_room", "detail": "Left 5", "target": "000F6845"})
    assert (sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == ("make_room", "left", "")
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (1, 0, 5)
    sent = sent_for({"action": "make_room"})
    assert (sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_LOW]) == ("back", 0, consts.ROOM_DEFAULT_METERS)

    # The fake game answers like the game mod. Stepping aside ends a walk and a seat, but not waiting or following someone
    assert run_command(consts.ACTION_MAKE_ROOM, "", "", "00000005", "right")[1].startswith("You step about 5 meters to the right")
    assert run_command(consts.ACTION_MAKE_ROOM, "", "", "", "back")[1].startswith("You step back about 3 meters")
    assert walk_target_after(consts.ACTION_MAKE_ROOM, "", "000F6845") == "" and is_sitting_after(consts.ACTION_MAKE_ROOM, True) is False
    assert is_waiting_after(consts.ACTION_MAKE_ROOM, True) is True and is_waiting_after(consts.ACTION_MAKE_ROOM, False) is False
    assert leader_after(consts.ACTION_MAKE_ROOM, "", "000F6845") == "000F6845"


def test_settings_come_from_a_file_that_is_made_when_missing(tmp_path):
    from src.companion.settings import load_settings
    from src.companion.voice import DEFAULT_GAME_FOLDER, DEFAULT_PIPER_FOLDER

    file = tmp_path / "companion.ini"
    settings, source = load_settings(str(file))
    assert file.is_file() and "new" in source
    assert (settings.game_folder, settings.piper_folder, settings.lips) == (DEFAULT_GAME_FOLDER, DEFAULT_PIPER_FOLDER, False)
    # The file that was made reads as the same settings, and says so in its notes
    again, source = load_settings(str(file))
    assert again == settings and "new" not in source and "lips = off" in file.read_text(encoding="utf-8")

    file.write_text("[companion]\ngame_folder = \"D:\\Games\\Skyrim 100%\"\nlips = On\nfonix_file = D:\\CK\\FonixData.cdf\n", encoding="utf-8")
    settings, source = load_settings(str(file))
    assert (settings.game_folder, settings.lips, settings.fonix_file) == ("D:\\Games\\Skyrim 100%", True, "D:\\CK\\FonixData.cdf")
    assert (settings.piper_folder, settings.lipgen_folder, settings.facefx_folder) == (DEFAULT_PIPER_FOLDER, "", "")

    # A file nobody can make sense of leaves everything as it would be without it
    file.write_text("game_folder = D:\\Games\n", encoding="utf-8")
    settings, source = load_settings(str(file))
    assert settings.game_folder == DEFAULT_GAME_FOLDER and "could not be read" in source
    file.write_text("[other]\nlips = on\n", encoding="utf-8")
    settings, source = load_settings(str(file))
    assert settings.lips is False and "[companion]" in source


def test_lips_get_a_file_next_to_the_sound_when_switched_on(tmp_path):
    import sys

    from src.companion.lips import PLACEHOLDER_FILE, LipSync
    from src.companion.voice import LINE_FILES, CompanionVoice, lip_file_of

    piper = tmp_path / "stand_in_piper.py"
    piper.write_text(
        "import sys, wave\n"
        "for line in sys.stdin:\n"
        "    if line.startswith('load_model'):\n"
        "        print('Model loaded', flush=True)\n"
        "    elif line.startswith('synthesize '):\n"
        "        with wave.open('out.wav', 'wb') as sound:\n"
        "            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(22050)\n"
        "            sound.writeframes(b'\\x00\\x00' * len(line))\n", encoding="utf-8")
    # A stand-in for the lip program: it writes the text into the lip file, and gives up on the sentence 'Hm.'
    lip_program = tmp_path / "stand_in_lips.py"
    lip_program.write_text(
        "import sys\n"
        "if sys.argv[3] != 'Hm.':\n"
        "    open(sys.argv[2], 'w', encoding='utf-8').write(sys.argv[3])\n", encoding="utf-8")
    game_folder = tmp_path / "game"
    voice_folder = game_folder / "Data" / "Sound" / "Voice" / "KI_MCP_Gefaehrte.esp" / "FemaleEvenToned"
    voice_folder.mkdir(parents=True)
    other_folder = voice_folder.parent / "MaleNord"
    other_folder.mkdir()
    for folder in (voice_folder, other_folder):
        for line_file in LINE_FILES:
            (folder / line_file).write_bytes(b"sound")
    lip_files = [voice_folder / lip_file_of(line_file) for line_file in LINE_FILES]
    assert lip_files[0].name == "KI_MCP_Say_KI_MCP_SayOne_00000805_1.lip"
    assert PLACEHOLDER_FILE.is_file()
    piper_command = [sys.executable, str(piper)]

    # Neither of the two programs is there: the lips count as switched off, and the start of the server says why
    missing = LipSync(str(game_folder))
    assert "neither" in missing.find_problem() and missing.describe_setup().startswith("off:")
    (game_folder / "Data" / "Sound" / "Voice" / "Processing").mkdir()
    (game_folder / "Data" / "Sound" / "Voice" / "Processing" / "FaceFXWrapper.exe").write_bytes(b"program")
    assert "FonixData.cdf" in LipSync(str(game_folder)).find_problem()
    assert LipSync(str(game_folder), fonix_file=str(piper)).find_problem() == ""
    silent = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=piper_command, lips=missing)
    assert silent.describe_lips().startswith("off:") and silent.prepare_lip_files() == ""
    assert silent.speak("Hello there.", "FemaleEvenToned") == (True, "", 1) and not lip_files[0].exists()

    # Switched on: every line of every voice gets a lip file to begin with, because the game only finds what is there when it starts
    lips = LipSync(str(game_folder), lip_command=[sys.executable, str(lip_program)])
    voice = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=piper_command, lips=lips)
    assert voice.describe_lips().startswith("on:")
    change = voice.prepare_lip_files()
    assert change.startswith("4 lip files put next to the sound files") and "started anew" in change
    assert lip_files[0].read_bytes() == PLACEHOLDER_FILE.read_bytes() and (other_folder / lip_files[1].name).is_file()
    assert voice.prepare_lip_files() == ""
    # Each sentence brings its own lips. The text goes to the program without quotation marks
    assert voice.speak('He said "no" to me.', "FemaleEvenToned") == (True, "", 1)
    assert lip_files[0].read_text(encoding="utf-8") == "He said no to me."
    assert voice.speak("I am sworn to carry your burdens.", "FemaleEvenToned") == (True, "", 2)
    assert lip_files[1].read_text(encoding="utf-8") == "I am sworn to carry your burdens."
    # A sentence the program gives up on gets the placeholder instead of the lips of the sentence before
    assert voice.speak("Hm.", "FemaleEvenToned") == (True, "", 1)
    assert lip_files[0].read_bytes() == PLACEHOLDER_FILE.read_bytes()

    # Switched off again: the lip files go, so that the mouth stays shut, and speaking leaves it at that
    off = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=piper_command)
    assert off.describe_lips().startswith("off")
    assert off.prepare_lip_files().startswith("4 lip files of the lines taken away")
    assert off.speak("Hello again.", "FemaleEvenToned")[0] is True
    assert not lip_files[0].exists() and not lip_files[1].exists() and (voice_folder / LINE_FILES[0]).is_file()


def test_face_move_and_pick_up_carry_what_the_game_needs():
    from src.companion.fake_game import (ITEM_REF_ID, CHEST_REF_ID, is_sitting_after, is_waiting_after, leader_after, run_command,
                                         walk_target_after)

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "point of the compass" in command({"action": "face"})["message"]
        assert "point of the compass" in command({"action": "face", "detail": "left"})["message"]
        assert "needs a direction" in command({"action": "move"})["message"]
        assert "needs a direction" in command({"action": "move", "detail": "up 3"})["message"]
        assert "how far in meters" in command({"action": "move", "detail": "north 500"})["message"]
        assert "needs a target" in command({"action": "pick_up"})["message"]
        # What is fine is refused only because no game is there
        for arguments in ({"action": "face", "detail": "NW"}, {"action": "face", "target": "000C0001"}, {"action": "move", "detail": "ahead"},
                          {"action": "move", "detail": "South West 12 m"}, {"action": "pick_up", "target": ITEM_REF_ID}):
            assert command(arguments)["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"face", "move", "pick_up"} <= set(tools[0]["inputSchema"]["properties"]["action"]["enum"])
        assert "space" in tools[1]["inputSchema"]["properties"]

    def sent_for(arguments: dict) -> dict:
        with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
            received_commands: list[dict] = []

            def game():
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}]
                client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

            threading.Thread(target=game, daemon=True).start()
            time.sleep(0.3)
            answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))
            assert answer == {"status": consts.STATUS_DONE, "message": "Done."}
            return received_commands[0]

    # face: the point of the compass in degrees, or the target and no direction
    sent = sent_for({"action": "face", "detail": "South-East"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == (135, "south-east", "")
    sent = sent_for({"action": "face", "detail": "no"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_DETAIL]) == (45, "north-east")
    sent = sent_for({"action": "face", "target": "000C0001", "detail": "north"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == (consts.FACE_NO_DIRECTION, "", "000C0001")
    # move: the direction in degrees, 1 in the upper half of the item ID if it counts from where the companion faces, the meters in the lower
    sent = sent_for({"action": "move", "detail": "north 10", "target": "000F6845"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (0, 0, 10)
    assert (sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_TARGET]) == ("to the north", "")
    sent = sent_for({"action": "move", "detail": "ahead left 6m"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (315, 1, 6)
    assert sent[consts.KEY_COMMAND_DETAIL] == "ahead to the left"
    sent = sent_for({"action": "move", "detail": "back"})
    assert (sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == (180, 1, consts.MOVE_DEFAULT_METERS)
    sent = sent_for({"action": "pick_up", "target": ITEM_REF_ID})
    assert (sent[consts.KEY_COMMAND_TARGET], sent[consts.KEY_COMMAND_TARGET_HIGH], sent[consts.KEY_COMMAND_TARGET_LOW]) == (ITEM_REF_ID, 0x0005, 0xA410)

    # The fake game answers like the game mod
    assert run_command(consts.ACTION_FACE, "", "", "", "south-east")[1] == "You turn to face south-east."
    assert run_command(consts.ACTION_FACE, "000C0001", "")[1] == "You turn to face Pelagia Farm."
    assert run_command(consts.ACTION_MOVE, "", "", "0001000A", "to your left")[1].startswith("You walk about 10 meters to your left and wait there.")
    assert run_command(consts.ACTION_PICK_UP, ITEM_REF_ID, "")[1].startswith("You walk to Iron Dagger to pick it up.")
    assert "Use 'loot'" in run_command(consts.ACTION_PICK_UP, CHEST_REF_ID, "")[1]
    assert "not a thing to pick up" in run_command(consts.ACTION_PICK_UP, "000F6845", "")[1]
    # Turning changes nothing else. Walking somewhere makes the companion wait there, picking something up is a walk like loot
    assert is_sitting_after(consts.ACTION_FACE, True) is True and leader_after(consts.ACTION_FACE, "", "000F6845") == "000F6845"
    assert is_waiting_after(consts.ACTION_MOVE, False) is True and walk_target_after(consts.ACTION_MOVE, "", "000F6845") == ""
    assert walk_target_after(consts.ACTION_PICK_UP, ITEM_REF_ID) == ITEM_REF_ID and is_waiting_after(consts.ACTION_PICK_UP, False) is False


def test_traps_come_with_every_state_and_the_space_only_when_asked_for():
    from src.companion.fake_game import build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), **result}).split("\n")

    lines = text_for(build_state(has_traps=True))
    start = lines.index("Traps:")
    # Each trap with its ref_id, what it is, and where it is from the player and from the companion. They stand in front of the characters
    assert lines[start + 1].startswith("0006B001 tripwire, trap, 5 m ahead, from you ")
    # A kind of trap the server has no word for goes out as the game files it
    assert lines[start + 2].startswith("0006B002 swingingblade, trap, 12 m left")
    assert start < lines.index("Nearby:")
    assert not any(line.startswith("Space around you") for line in lines)
    # Without traps nothing is said about them
    assert "Traps:" not in text_for(build_state())
    # 'visible' leaves out the trap behind the viewer
    seen = text_for(build_state(is_visible_only=True, has_traps=True), visible_wanted=True)
    assert any(line.startswith("0006B001 tripwire") for line in seen) and not any(line.startswith("0006B002") for line in seen)
    # Seen from the companion there is only that view
    around = text_for(build_state(is_around_companion=True, has_traps=True))
    assert "0006B001 tripwire, trap, from you 5 m ahead" in around

    # The space around the companion: walls, rising ground, drops and what is open, in one line
    space_line = [line for line in text_for(build_state(has_space=True), space_wanted=True) if line.startswith("Space around you")]
    assert space_line == ["Space around you (rays at chest height, about 15 m far): wall ahead 3 m, left 5 m, ahead-left 2 m; "
                          "ground rises behind-right 8 m; DROP right 4 m; open ahead-right, behind, behind-left"]
    assert read_game_state(build_state(has_space=True))["space"][2] == {"angle_degrees": 90, "obstacle": "", "drop_meters": 4}
    # Asked for, but the game sent nothing (an older plugin, or the companion is somewhere else)
    assert "could not measure" in [line for line in text_for(build_state(), space_wanted=True) if line.startswith("Space")][0]
    assert "did not send the space" in "\n".join(text_for(build_state(), space_missed=True))
    # Numbers that do not come in threes are not read
    broken = build_state()
    broken["space"] = [1, 3]
    assert "space" not in read_game_state(broken)

    # The server asks the game for the space only when the AI wants it
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(has_traps=True, has_space=True)})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        answer = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"space": True}}))
        assert received_commands[0][consts.KEY_COMMAND_SPACE] == 1
        assert "Space around you (rays at chest height, about 15 m far): wall ahead 3 m" in answer and "Traps:" in answer


def test_find_by_name_and_move_to_keeps_a_distance():
    from src.companion.fake_game import CHEST_REF_ID, build_state, run_command
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **result: object) -> list[str]:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), **result}).split("\n")

    # What the game found, nearest first, each with what it is and where
    lines = text_for(build_state(find="wa"), find_wanted="wa")
    start = lines.index("Found for 'wa':")
    assert lines[start + 1].startswith("0001A6F0 Warmaiden's, door, 20 m right, from you ")
    assert start < lines.index("Nearby:")
    assert any(line.startswith("000C0002 Whiterun Stables, place, 260 m behind") for line in text_for(build_state(find="stables"), find_wanted="stables"))
    # Every word has to be in the name, and a dead character is marked
    assert [line for line in text_for(build_state(find="the BALGRUUF"), find_wanted="the BALGRUUF") if line.startswith("000A2C8E")] == [
        "000A2C8E Balgruuf the Greater, character, 85 m ahead, from you 87 m behind-left"]
    assert any(line.startswith("FF000A03 Bandit Outlaw, character, dead, 9 m") for line in text_for(build_state(find="bandit"), find_wanted="bandit"))
    nothing = text_for(build_state(find="Belethor"), find_wanted="Belethor")
    assert any(line.startswith("Found for 'Belethor': nothing with that name in the loaded area.") for line in nothing)
    # An older plugin does not answer the search, and a search nobody asked for is not shown
    assert any("could not look for 'wa'" in line and "interface version 10" in line for line in text_for(build_state(), find_wanted="wa"))
    assert not any(line.startswith("Found for") for line in text_for(build_state(find="wa")))
    assert "did not send what it found" in "\n".join(text_for(build_state(), find_missed=True))

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "how many meters in front" in command({"action": "move_to", "target": "000F6845", "detail": "close"})["message"]
        assert "how many meters in front" in command({"action": "move_to", "target": "000F6845", "detail": "50"})["message"]
        assert command({"action": "move_to", "target": "000F6845", "detail": "3 m"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "find" in tools[1]["inputSchema"]["properties"]

    # The server passes the text on to the game, and for move_to how far to stay away
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            # The first request picks up the question for the state, the next one answers it and picks up a command,
            # and each one after that brings the result of the command before
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 1}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            body: dict = {consts.KEY_STATE: build_state(find="maiden")}
            for _ in range(2):
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                body = {consts.KEY_RESULTS: [{consts.KEY_COMMAND_ID: received_commands[-1][consts.KEY_COMMAND_ID],
                                              consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}]}
            client.post(consts.ROUTE_GAME, json=body)

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        answer = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"find": "  Maiden \n"}}))
        assert received_commands[0][consts.KEY_COMMAND_FIND] == "Maiden"
        assert "Found for 'Maiden':\n0001A6F0 Warmaiden's, door, 20 m right" in answer
        assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "move_to", "target": "000F6845", "detail": "3m"}}))["status"] == consts.STATUS_DONE
        assert (received_commands[1][consts.KEY_COMMAND_COUNT], received_commands[1][consts.KEY_COMMAND_DETAIL]) == (3, "3")
        assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "move_to", "target": "000F6845"}}))["status"] == consts.STATUS_DONE
        assert received_commands[2][consts.KEY_COMMAND_COUNT] == consts.MOVE_TO_USUAL_DISTANCE

    # The fake game answers like the game mod: a little away from a character, right up to a thing, or as far as told
    assert run_command(consts.ACTION_MOVE_TO, "000F6845", "")[1].startswith("You walk to Whiterun Guard, stop about 2 meters in front, and wait there.")
    assert run_command(consts.ACTION_MOVE_TO, CHEST_REF_ID, "")[1] == "You walk to Chest and wait there. 'follow' brings you back to the player."
    assert run_command(consts.ACTION_MOVE_TO, CHEST_REF_ID, "", "", "4")[1].startswith("You walk to Chest, stop about 4 meters in front")
    assert run_command(consts.ACTION_MOVE_TO, "000F6845", "", "", "0")[1] == "You walk to Whiterun Guard and wait there. 'follow' brings you back to the player."


def test_xtts_speaks_instead_of_piper_and_piper_steps_in(tmp_path):
    import io
    import sys
    import wave
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from src.companion.settings import VOICE_PIPER, VOICE_XTTS, load_settings
    from src.companion.voice import LINE_FILES, CompanionVoice
    from src.companion.xtts import DEFAULT_XTTS_LANGUAGE, DEFAULT_XTTS_URL, DEFAULT_XTTS_VOICE, XttsVoice

    xtts_rate = 24000
    piper_rate = 22050
    asked: list[dict] = []

    # A stand-in for the XTTS server: it answers the two routes the way the player's xtts_server.py does
    class StandInXtts(BaseHTTPRequestHandler):
        def answer(self, body: bytes, kind: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.answer(b'{"status": "ok"}', "application/json")

        def do_POST(self) -> None:
            wanted = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            asked.append(wanted)
            if wanted["input"] == "Kaputt.":
                self.answer(b"no sound at all", "audio/wav")
                return
            sound = io.BytesIO()
            with wave.open(sound, "wb") as opened:
                opened.setnchannels(1)
                opened.setsampwidth(2)
                opened.setframerate(xtts_rate)
                opened.writeframes(b"\x00\x00" * 100)
            self.answer(sound.getvalue(), "audio/wav")

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StandInXtts)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    piper = tmp_path / "stand_in_piper.py"
    piper.write_text(
        "import sys, wave\n"
        "for line in sys.stdin:\n"
        "    if line.startswith('load_model'):\n"
        "        print('Model loaded', flush=True)\n"
        "    elif line.startswith('synthesize '):\n"
        "        with wave.open('out.wav', 'wb') as sound:\n"
        f"            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate({piper_rate})\n"
        "            sound.writeframes(b'\\x00\\x00' * len(line))\n", encoding="utf-8")
    game_folder = tmp_path / "game"
    voice_folder = game_folder / "Data" / "Sound" / "Voice" / "KI_MCP_Gefaehrte.esp" / "FemaleEvenToned"
    voice_folder.mkdir(parents=True)

    def rate_of(line: int) -> int:
        with wave.open(str(voice_folder / LINE_FILES[line - 1]), "rb") as sound:
            return sound.getframerate()

    try:
        voice = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(piper)],
                               xtts=XttsVoice(url + "/", "Uta Obando", "DE"))
        assert voice.spoken_language == "German"
        assert voice.describe_setup().startswith("on: XTTS at " + url + ", voice 'Uta Obando', language German")
        # The sound comes from the XTTS server, which is asked the way OpenAI's speech route is
        assert voice.speak("Hallo, mein *Thane*.", "FemaleEvenToned") == (True, "", 1)
        assert rate_of(1) == xtts_rate
        assert asked == [{"input": "Hallo, mein Thane.", "voice": "Uta Obando", "language": "de", "speed": 1.0}]
        # What the XTTS server sends is no sound: Piper makes the sentence, so that the companion does not fall silent
        assert voice.speak("Kaputt.", "FemaleEvenToned") == (True, "", 2)
        assert rate_of(2) == piper_rate
        assert voice.speak("Wieder da.", "FemaleEvenToned") == (True, "", 1)
        assert rate_of(1) == xtts_rate

        # The AI is told to write in the language of the voice, and in English when Piper speaks
        with TestClient(create_app(TOKEN, hold_seconds=0.1, voice=voice)) as client:
            description = rpc(client, "tools/list").json()["result"]["tools"][0]["description"]
            assert "Write it in German" in description and "Write it in English" not in description
        with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
            assert "Write it in English" in rpc(client, "tools/list").json()["result"]["tools"][0]["description"]
    finally:
        server.shutdown()
        server.server_close()

    # The XTTS server is not running: the start of the server says so, and Piper speaks
    assert "does not answer" in voice.describe_setup() and "Piper speaks" in voice.describe_setup()
    assert voice.speak("Niemand da.", "FemaleEvenToned") == (True, "", 2)
    assert rate_of(2) == piper_rate
    # Without Piper to step in, the AI is told about both
    alone = CompanionVoice(str(game_folder), str(tmp_path / "no_piper"), xtts=XttsVoice(url))
    is_ok, problem, line = alone.speak("Niemand da.", "FemaleEvenToned")
    assert is_ok is False and problem.startswith("XTTS could not speak") and "Piper is not installed" in problem
    plain = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(piper)])
    assert plain.spoken_language == "English" and plain.describe_setup().startswith("on: Piper")

    # The settings: Piper unless xtts is asked for, and what is left empty is what the notes say
    file = tmp_path / "companion.ini"
    settings, _ = load_settings(str(file))
    assert (settings.voice, settings.xtts_url, settings.xtts_voice, settings.xtts_language) == (
        VOICE_PIPER, DEFAULT_XTTS_URL, DEFAULT_XTTS_VOICE, DEFAULT_XTTS_LANGUAGE)
    assert "voice =\n" in file.read_text(encoding="utf-8") and load_settings(str(file))[0] == settings
    file.write_text("[companion]\nvoice = XTTS\nxtts_voice = lydia\nxtts_language = en\n", encoding="utf-8")
    settings, _ = load_settings(str(file))
    assert (settings.voice, settings.xtts_url, settings.xtts_voice, settings.xtts_language) == (VOICE_XTTS, DEFAULT_XTTS_URL, "lydia", "en")
    file.write_text("[companion]\nvoice = xtss\n", encoding="utf-8")
    assert load_settings(str(file))[0].voice == VOICE_PIPER


def test_the_xtts_server_is_started_when_asked_for(tmp_path):
    import socket
    import sys

    from src.companion.settings import load_settings
    from src.companion.xtts import XttsVoice

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    xtts = XttsVoice(f"http://127.0.0.1:{port}")
    # The file that is named is not there
    assert "not found" in xtts.start_server(str(tmp_path / "start-xtts-server.bat"))
    # What is started stops again at once
    start_file = str(tmp_path / "start.py")
    assert "stopped again" in xtts.start_server(start_file, [sys.executable, "-c", "pass"], timeout_seconds=20)
    # A stand-in that answers the health route after a moment, like the real one after loading its model
    stand_in = tmp_path / "stand_in_xtts.py"
    stand_in.write_text(
        "import sys, time\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, *args):\n"
        "        pass\n"
        "time.sleep(1.5)\n"
        "server = HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler)\n"
        "server.timeout = 0.5\n"
        "end = time.time() + 20\n"
        "while time.time() < end:\n"
        "    server.handle_request()\n", encoding="utf-8")
    assert "ready after" in xtts.start_server(start_file, [sys.executable, str(stand_in), str(port)], timeout_seconds=20)
    assert xtts.start_server(start_file, [sys.executable, "-c", "pass"]) == "is running already"

    file = tmp_path / "companion.ini"
    assert load_settings(str(file))[0].xtts_start == "" and "xtts_start =\n" in file.read_text(encoding="utf-8")
    file.write_text("[companion]\nvoice = xtts\nxtts_start = \"D:\\xtts\\start-xtts-server.bat\"\n", encoding="utf-8")
    assert load_settings(str(file))[0].xtts_start == "D:\\xtts\\start-xtts-server.bat"


def test_sheathe_doors_loud_voice_and_what_the_player_does(tmp_path):
    import array
    import sys
    import wave

    from src.companion import game_state
    from src.companion.fake_game import build_state, run_command
    from src.companion.loudness import change_volume, scale_sample
    from src.companion.settings import load_settings
    from src.companion.state_text import describe_state
    from src.companion.voice import LINE_FILES, CompanionVoice

    def state_text(raw: dict) -> str:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": game_state.read_game_state(raw)})

    # The state tells whether the player sits or sleeps, and whether the companion keeps its weapon put away
    plain = game_state.read_game_state(build_state())
    assert (plain["player"]["sitting"], plain["player"]["sleeping"], plain["companion"]["weapon_kept_away"]) == (False, False, False)
    seated = build_state(is_player_sitting=True, is_weapon_kept_away=True)
    read = game_state.read_game_state(seated)
    assert read["player"]["sitting"] is True and read["companion"]["weapon_kept_away"] is True
    text = state_text(seated)
    player_line = [line for line in text.splitlines() if line.startswith("Player")][0]
    assert "sitting" in player_line and "weapon kept away (sheathe)" in text
    asleep = build_state()
    asleep[game_state.KEY_PLAYER_VALUES][game_state.PLAYER_PLACE_OF_SLEEPING] = 3
    assert "lying asleep" in state_text(asleep)
    # A drawn weapon counts, whatever sheathe set before (a fight)
    assert "weapon kept away" not in state_text(build_state(are_weapons_drawn=True, is_weapon_kept_away=True))
    # An older game mod sends fewer numbers and says nothing about all that
    older = build_state()
    older[game_state.KEY_PLAYER_VALUES] = older[game_state.KEY_PLAYER_VALUES][:game_state.PLAYER_VALUE_COUNT_WITHOUT_SITTING]
    older[game_state.KEY_COMPANION_VALUES] = older[game_state.KEY_COMPANION_VALUES][:game_state.COMPANION_VALUE_COUNT_WITHOUT_KEPT_WEAPON]
    read = game_state.read_game_state(older)
    assert "sitting" not in read["player"] and "weapon_drawn" in read["player"] and "weapon_kept_away" not in read["companion"]

    # The fake game answers the new commands like the game mod
    assert run_command(consts.ACTION_SHEATHE, "", "")[1].startswith("You put your weapon away.")
    assert run_command(consts.ACTION_DRAW, "", "")[1].startswith("You draw your weapon.")
    assert run_command(consts.ACTION_OPEN, "", "")[:2] == (True, "You open the door.")
    assert run_command(consts.ACTION_CLOSE, "", "")[:2] == (True, "You close the door.")
    assert "'enter' takes you through it" in run_command(consts.ACTION_OPEN, "0001A6F4", "")[1]

    # Louder: the quiet parts in full, the loud parts pressed together so that nothing is cut off
    assert scale_sample(0.1, 2.5) == 0.25 and 0.9 < scale_sample(0.9, 2.5) < 1.0 and scale_sample(-0.9, 2.5) < -0.9

    def write_sound(file, values: list[int]) -> None:
        with wave.open(str(file), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(24000)
            sound.writeframes(array.array("h", values).tobytes())

    def read_sound(file) -> list[int]:
        with wave.open(str(file), "rb") as sound:
            samples = array.array("h")
            samples.frombytes(sound.readframes(sound.getnframes()))
        return list(samples)

    sound_file = tmp_path / "sound.wav"
    write_sound(sound_file, [1000, -1000, 30000, -30000])
    assert change_volume(str(sound_file), 1.0) is False and read_sound(sound_file) == [1000, -1000, 30000, -30000]
    assert change_volume(str(sound_file), 0.5) is True and read_sound(sound_file) == [500, -500, 15000, -15000]
    write_sound(sound_file, [1000, -1000, 30000, -30000])
    assert change_volume(str(sound_file), 2.5) is True
    louder = read_sound(sound_file)
    assert louder[:2] == [2500, -2500] and 30000 < louder[2] <= 32767 and -32768 <= louder[3] < -30000

    # say: the setting voice_volume counts for every sentence, 'loud' comes on top and ends the sentence with "!"
    piper = tmp_path / "stand_in_piper.py"
    piper.write_text(
        "import sys, wave, array\n"
        "for line in sys.stdin:\n"
        "    if line.startswith('load_model'):\n"
        "        print('Model loaded', flush=True)\n"
        "    elif line.startswith('synthesize '):\n"
        "        with open('said.txt', 'a', encoding='utf-8') as said:\n"
        "            said.write(line)\n"
        "        with wave.open('out.wav', 'wb') as sound:\n"
        "            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(22050)\n"
        "            sound.writeframes(array.array('h', [1000] * 50).tobytes())\n", encoding="utf-8")
    game_folder = tmp_path / "game"
    voice_folder = game_folder / "Data" / "Sound" / "Voice" / "KI_MCP_Gefaehrte.esp" / "FemaleEvenToned"
    voice_folder.mkdir(parents=True)
    voice = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(piper)],
                           volume_percent=200, talk_gestures=True)
    assert voice.speak("Follow me.", "FemaleEvenToned") == (True, "", 1)
    assert read_sound(voice_folder / LINE_FILES[0])[0] == 2000
    assert voice.speak("Watch out, a trap.", "FemaleEvenToned", True) == (True, "", 2)
    # 200 % times what 'loud' adds (half as much again) is three times as loud
    assert read_sound(voice_folder / LINE_FILES[1])[0] == 3000
    quiet = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(piper)])
    assert quiet.speak("Halt!", "FemaleEvenToned", True)[0] is True and quiet.has_talk_gestures is False
    # 'quiet' makes the sound quieter (1000 at 100 % times QUIET_FACTOR), and the sentence stays as written
    from src.companion.loudness import QUIET_FACTOR
    is_said, _, quiet_line = quiet.speak("Draugr ahead.", "FemaleEvenToned", False, True)
    assert is_said and read_sound(voice_folder / LINE_FILES[quiet_line - 1])[0] == int(1000 * QUIET_FACTOR)
    assert run_command(consts.ACTION_SAY, "", "", is_quiet=True) == (True, "You say it quietly.", "")

    # The game gets the animation event for the hands with say, and the AI can ask for a raised voice
    with TestClient(create_app(TOKEN, hold_seconds=5, voice=voice)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You say it aloud."}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "say", "detail": "Archers on the tower.", "loud": True}}))
        assert answer["status"] == consts.STATUS_DONE and answer["message"].startswith("You say it aloud.")
        sent = received_commands[0]
        assert sent[consts.KEY_COMMAND_TALK] in consts.TALK_ANIMATIONS and sent[consts.KEY_COMMAND_DETAIL] == "Archers on the tower."
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"sheathe", "draw", "open", "close"} <= set(tools[0]["inputSchema"]["properties"]["action"]["enum"])
        assert tools[0]["inputSchema"]["properties"]["loud"]["type"] == "boolean"
        assert tools[0]["inputSchema"]["properties"]["quiet"]["type"] == "boolean" and "'quiet': true" in tools[0]["description"]
        # In a low voice: the game is told so, and the hands stay still unless a manner is named. Not together with 'loud'
        both = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "say", "detail": "Hm.", "loud": True, "quiet": True}}))
        assert both == {"status": consts.STATUS_FAILED, "message": "say takes 'loud' or 'quiet', not both."}
        received_commands.clear()
        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "say", "detail": "Draugr ahead.", "quiet": True}})
        assert received_commands[0][consts.KEY_COMMAND_QUIET] == 1 and received_commands[0][consts.KEY_COMMAND_TALK] == ""
        assert consts.KEY_COMMAND_LOUD not in received_commands[0]
        # Called out: the game is told so, because enemies hear that further away
        assert consts.KEY_COMMAND_QUIET not in sent and sent[consts.KEY_COMMAND_LOUD] == 1
        assert "always speak with 'quiet'" in manual_text()
        assert "about 200 meters" in manual_text() and "lying asleep" in manual_text()
    # What Piper was given: the sentence that is called out ends with an exclamation mark, the others stay as written
    said_files = list(tmp_path.glob("**/said.txt")) + list(__import__("pathlib").Path(__import__("tempfile").gettempdir()).glob("companion_voice_*/said.txt"))
    said = "".join(file.read_text(encoding="utf-8") for file in said_files)
    assert "synthesize Follow me.\n" in said and "synthesize Watch out, a trap!\n" in said and "synthesize Archers on the tower!\n" in said

    # The settings: the volume in percent within its limits, the hands move unless they are switched off
    file = tmp_path / "companion.ini"
    settings, _ = load_settings(str(file))
    assert (settings.voice_volume, settings.talk_gestures) == (100, True) and load_settings(str(file))[0] == settings
    file.write_text("[companion]\nvoice_volume = 250 %\ntalk_gestures = off\n", encoding="utf-8")
    settings, _ = load_settings(str(file))
    assert (settings.voice_volume, settings.talk_gestures) == (250, False)
    file.write_text("[companion]\nvoice_volume = 9000\n", encoding="utf-8")
    assert load_settings(str(file))[0].voice_volume == 400 and load_settings(str(file))[0].talk_gestures is True

    # The setting mute_game_lines goes to the game with every command, which takes it from report_state
    assert load_settings(str(file))[0].mute_game_lines is False
    file.write_text("[companion]\nmute_game_lines = on\n", encoding="utf-8")
    assert load_settings(str(file))[0].mute_game_lines is True
    for wanted in (True, False):
        with TestClient(create_app(TOKEN, hold_seconds=5, mutes_game_lines=wanted)) as client:
            received: list[dict] = []

            def poll():
                received.extend(client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()}).json()[consts.KEY_COMMANDS])

            threading.Thread(target=poll, daemon=True).start()
            time.sleep(0.3)
            rpc(client, "tools/call", {"name": "STATE", "arguments": {}})
            assert received[0][consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE and received[0][consts.KEY_COMMAND_MUTE] == int(wanted)


def test_say_with_whom_the_companion_speaks_to_and_how(monkeypatch):
    from src.companion import mcp_route
    from src.companion.fake_game import run_command

    # The hands: one animation for a short sentence, another one every few seconds of a long one, never the same twice in a row
    assert len(mcp_route._pick_talk_animations("", 3.0)) == 1
    assert len(mcp_route._pick_talk_animations("", consts.TALK_GESTURE_SECONDS + consts.TALK_GESTURE_LEAST_SECONDS)) == 2
    picked = mcp_route._pick_talk_animations("calm", 60.0)
    assert len(picked) > 10 and set(picked) <= set(consts.TALK_EVENTS)
    assert all(first != second for first, second in zip(picked, picked[1:]))
    assert mcp_route._pick_talk_animations("calm", 1.0)[0] != picked[-1]
    # A manner without hands only has its face. A manner with one animation repeats it
    assert mcp_route._pick_talk_animations("sad", 60.0) == [] and consts.MANNERS["sad"][1] in consts.EXPRESSIONS
    assert mcp_route._pick_talk_animations("angry", 7.0) == ["IdleDialogueAngryStart", "IdleDialogueAngryStart"]
    # An animation goes to the game mod as the name of an animation event or as the ID of an animation
    assert mcp_route._split_animation("IdleDialogueMovingTalkA") == ("IdleDialogueMovingTalkA", "")
    assert mcp_route._split_animation("000FFA0C") == ("", "000FFA0C")
    for animations, face in consts.MANNERS.values():
        assert face == "" or face in consts.EXPRESSIONS

    # Beside the sentence: the further animations one after the other, then the end, all with the number of the sentence
    sent: list[tuple] = []

    class Queue:
        async def send_command(self, action, talk="", item="", say_number=0):
            sent.append((action, talk, item, say_number))
            return {"status": consts.STATUS_DONE, "message": "ok"}

    monkeypatch.setattr(consts, "TALK_GESTURE_SECONDS", 0.01)
    monkeypatch.setattr(consts, "SAY_END_EXTRA_SECONDS", 0.0)
    asyncio.run(mcp_route._finish_speech(Queue(), 7, 0.05, ["IdleDialogueMovingTalkB", "000B8F1F"]))
    assert sent == [(consts.ACTION_SAY_TALK, "IdleDialogueMovingTalkB", "", 7), (consts.ACTION_SAY_TALK, "", "000B8F1F", 7),
                    (consts.ACTION_SAY_END, "", "", 7)]

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "say", "detail": "Hello.", "manner": "singing"}}))
        assert answer["status"] == consts.STATUS_FAILED and "calm" in answer["message"]
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[0]["inputSchema"]["properties"]["manner"]["enum"] == list(consts.MANNERS)
        assert "whom you speak to" in tools[0]["description"] and consts.ACTION_SAY_END not in tools[0]["inputSchema"]["properties"]["action"]["enum"]
    # The game mod answers the two commands of the server, and speaks even if the one spoken to is not around
    assert run_command(consts.ACTION_SAY_END, "", "") == (True, "ok", "")
    assert "not around here" in run_command(consts.ACTION_SAY, "00ABCDEF", "")[1]


def test_run_goes_to_the_game_and_the_state_tells_who_runs():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    def state_text(raw: dict) -> str:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": game_state.read_game_state(raw)})

    # The state: one more number about the companion, and the line 'You' says so
    assert game_state.read_game_state(build_state())["companion"]["running"] is False
    assert game_state.read_game_state(build_state(is_running=True))["companion"]["running"] is True
    assert "running" in state_text(build_state(is_running=True)) and "running" not in state_text(build_state())
    # An older game mod sends one number less and says nothing about running
    older = build_state()
    older[game_state.KEY_COMPANION_VALUES] = older[game_state.KEY_COMPANION_VALUES][:game_state.COMPANION_VALUE_COUNT_WITHOUT_RUNNING]
    read = game_state.read_game_state(older)
    assert "running" not in read["companion"] and "weapon_kept_away" in read["companion"]

    # The command: 'run' goes along for the actions that walk, and for no other
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            # Like the game mod: the request that brings the results of one command is the one that waits for the next
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state()}).json()
            for _ in range(2):
                commands = reply[consts.KEY_COMMANDS]
                received_commands.extend(commands)
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "ok"} for sent in commands]
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results}).json()

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "follow", "run": True}}))["status"] == consts.STATUS_DONE
        assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "wait", "run": True}}))["status"] == consts.STATUS_DONE
        assert [(sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_RUN]) for sent in received_commands] == [("follow", 1), ("wait", 0)]
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[0]["inputSchema"]["properties"]["run"]["type"] == "boolean"
        assert "run instead of walk" in manual_text() and "only your lips, head and face move" in manual_text()
        assert "none" in tools[0]["inputSchema"]["properties"]["manner"]["enum"]


def test_the_settings_say_which_game_is_played(tmp_path):
    from src.companion.settings import DEFAULT_GAME_FOLDER_SE, load_settings
    from src.companion.voice import DEFAULT_GAME_FOLDER

    # A new file, and one from before this setting: the game is Skyrim VR
    file = tmp_path / "companion.ini"
    settings, _ = load_settings(str(file))
    assert (settings.vr, settings.game_folder) == (True, DEFAULT_GAME_FOLDER) and load_settings(str(file))[0] == settings
    file.write_text("[companion]\ngame_folder = X:\\VR\n", encoding="utf-8")
    assert load_settings(str(file))[0].game_folder == "X:\\VR"
    # vr = off: the folder of Skyrim Special Edition counts, the one named or the usual one
    file.write_text("[companion]\nvr = off\ngame_folder = X:\\VR\ngame_folder_se = Y:\\SE\n", encoding="utf-8")
    settings, _ = load_settings(str(file))
    assert (settings.vr, settings.game_folder) == (False, "Y:\\SE")
    file.write_text("[companion]\nvr = off\ngame_folder = X:\\VR\n", encoding="utf-8")
    assert load_settings(str(file))[0].game_folder == DEFAULT_GAME_FOLDER_SE


def test_say_tells_the_game_how_long_the_sentence_is():
    # The game mod needs the length when the companion first walks over or turns to another character
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "walk over first" in manual_text()
    from src.companion.command_queue import CompanionCommandQueue
    import inspect
    assert "say_ms" in inspect.signature(CompanionCommandQueue.send_command).parameters


def test_piper_speaks_with_a_voice_model_of_ones_own(tmp_path):
    from src.companion.settings import load_settings
    from src.companion.voice import CompanionVoice

    piper_folder = tmp_path / "piper"
    (piper_folder / "models").mkdir(parents=True)
    (piper_folder / "models" / "de_DE-eva_k-x_low.onnx").write_bytes(b"model")
    (piper_folder / "models" / "de_DE-eva_k-x_low.onnx.json").write_text('{"language": {"code": "de_DE"}}', encoding="utf-8")
    game_folder = tmp_path / "game"
    (game_folder / "Data" / "Sound" / "Voice" / "KI_MCP_Gefaehrte.esp").mkdir(parents=True)

    # The model named in the settings tells the AI which language to write in
    own = CompanionVoice(str(game_folder), str(piper_folder), piper_command=["piper"], piper_voice="de_DE-eva_k-x_low")
    assert own.spoken_language == "German"
    assert "de_DE-eva_k-x_low.onnx" in own.describe_setup() and "German" in own.describe_setup()
    # The file with its folder counts too, and without the setting the voice of the character speaks English
    whole = CompanionVoice(str(game_folder), str(tmp_path), piper_command=["piper"],
                           piper_voice=str(piper_folder / "models" / "de_DE-eva_k-x_low.onnx"))
    assert whole.spoken_language == "German"
    assert CompanionVoice(str(game_folder), str(piper_folder), piper_command=["piper"]).spoken_language == "English"
    # A model that is not there switches the voice off at the start, with the reason
    missing = CompanionVoice(str(game_folder), str(piper_folder), piper_voice="nobody")
    (piper_folder / "piper.exe").write_bytes(b"")
    assert missing.describe_setup().startswith("off: the voice model") and missing.spoken_language == "English"

    file = tmp_path / "companion.ini"
    file.write_text("[companion]\npiper_voice = de_DE-eva_k-x_low\n", encoding="utf-8")
    settings, _ = load_settings(str(file))
    assert settings.piper_voice == "de_DE-eva_k-x_low"


def test_space_looks_into_one_direction_as_far_and_as_wide_as_asked():
    from src.companion.fake_game import SPACE_FACING_DEGREES, build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def space_line(raw: dict, **result: object) -> str:
        text = describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), "space_wanted": True, **result})
        return [line for line in text.split("\n") if line.startswith("Space")][0]

    # A look to the west, 150 m far and 30 degrees wide: three rays, named by how far they are turned away from west
    west = build_state(has_space=True, space_info=[270, 30, 150, SPACE_FACING_DEGREES])
    assert space_line(west, space_direction_wanted="west", space_meters_wanted=150) == (
        "Space towards west (rays at chest height, about 150 m far, fanned out over 30 degrees): wall west 40 m; "
        "DROP 15 degrees right of west 12 m; open 15 degrees left of west "
        "Outdoors the game only knows the ground and the walls of about 120 m around the player: "
        "further out 'open' just means that nothing was found.")
    rays = read_game_state(west)["space"]
    assert [ray["compass_degrees"] for ray in rays] == [255, 270, 285] and [ray["offset_degrees"] for ray in rays] == [-15, 0, 15]
    # Seen from the companion, which faces east, west is behind
    assert rays[1]["angle_degrees"] == -180
    # A direction seen from the companion also tells the point of the compass
    ahead = build_state(has_space=True, space_info=[90, 30, 15, SPACE_FACING_DEGREES])
    assert space_line(ahead, space_direction_wanted="ahead").startswith(
        "Space towards ahead, that is east (rays at chest height, about 15 m far, fanned out over 30 degrees): wall ahead 40 m")
    # All around with a plugin that can look: as before, and which way the companion faces
    around = build_state(has_space=True, space_info=[90, 360, 15, SPACE_FACING_DEGREES])
    assert space_line(around).startswith("Space around you (rays at chest height, about 15 m far, you face east): wall ahead 3 m, left 5 m")
    # A plugin that cannot look into a direction gives the usual look, and the AI is told
    assert "older than interface version 13" in space_line(build_state(has_space=True), space_direction_wanted="west")

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            for _ in range(3):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: west}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        answer = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"space_direction": "West", "space_meters": 150, "space_angle": 30}}))
        command = received_commands[0]
        # The direction alone asks for the space. West is 270 degrees from north
        assert (command[consts.KEY_COMMAND_SPACE], command[consts.KEY_COMMAND_SPACE_DEGREES], command[consts.KEY_COMMAND_SPACE_FACING],
                command[consts.KEY_COMMAND_SPACE_METERS], command[consts.KEY_COMMAND_SPACE_ANGLE]) == (1, 270, 0, 150, 30)
        assert "Space towards west (rays at chest height, about 150 m far" in answer
        # A direction seen from the companion without an angle: 60 degrees wide. More than 200 m is not asked for
        tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"space_direction": "left", "space_meters": 900}}))
        command = received_commands[1]
        assert (command[consts.KEY_COMMAND_SPACE_DEGREES], command[consts.KEY_COMMAND_SPACE_FACING],
                command[consts.KEY_COMMAND_SPACE_METERS], command[consts.KEY_COMMAND_SPACE_ANGLE]) == (270, 1, 200, 60)
        # A word that is no direction is turned down before the game is asked
        refused = rpc(client, "tools/call", {"name": "STATE", "arguments": {"space_direction": "up"}}).json()["result"]
        assert refused["isError"] is True and "no direction 'up'" in refused["content"][0]["text"]
        # Plain space stays a look all around
        tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"space": True}}))
        command = received_commands[2]
        assert (command[consts.KEY_COMMAND_SPACE], command[consts.KEY_COMMAND_SPACE_DEGREES], command[consts.KEY_COMMAND_SPACE_ANGLE]) == (1, -1, 360)


def test_spells_are_listed_with_the_own_inventory_and_cast_by_id_or_name():
    from src.companion.fake_game import SPELL_REF_ID, build_state
    from src.companion.game_state import read_game_state
    from src.companion.ref_ids import split_form_id, parse_ref_id
    from src.companion.state_text import describe_state

    def text_for(raw: dict, wanted: str) -> str:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), "inventory_wanted": wanted})

    # The spells come with the own inventory, each with its ID
    assert f"Spells you know (cast_spell): Healing Hands {SPELL_REF_ID}" in text_for(build_state(inventory="own"), "own")
    assert "Spells you know" not in text_for(build_state(inventory="player"), "player")
    # A companion that knows none is told how to learn one. A game mod that does not know spells says nothing
    none = build_state(inventory="own")
    none["spells"] = [0]
    del none["spells_names"]
    assert "Spells you know: none. Read a spell tome" in text_for(none, "own")
    del none["spells"]
    assert "Spells you know" not in text_for(none, "own")

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            results: list[dict] = []
            for _ in range(3):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(), consts.KEY_RESULTS: results}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: command[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You cast it."}
                           for command in reply[consts.KEY_COMMANDS]]

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # By its ID: the game gets the ID in two halves, like the item of loot. On the player
        rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell", "detail": SPELL_REF_ID.lower(), "target": "00000014"}})
        command = received_commands[0]
        assert (command[consts.KEY_COMMAND_ITEM_HIGH], command[consts.KEY_COMMAND_ITEM_LOW]) == split_form_id(parse_ref_id(SPELL_REF_ID))
        assert command[consts.KEY_COMMAND_TARGET] == "00000014"
        # By its name: no ID goes along, the game looks the name up. A short name that could be hex digits stays a name
        rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell", "detail": "Fade"}})
        command = received_commands[1]
        assert (command[consts.KEY_COMMAND_ITEM_HIGH], command[consts.KEY_COMMAND_ITEM_LOW], command[consts.KEY_COMMAND_DETAIL]) == (0, 0, "Fade")


def test_riding_shows_on_what_one_sits_and_the_horse_and_its_commands_reach_the_game():
    from src.companion.fake_game import HORSE_REF_ID, build_state, horse_after, is_ride_locked_after, is_riding_after, run_command
    from src.companion.game_state import KEY_COMPANION_MOUNT, UNKNOWN_MOUNT_NAME, read_game_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict) -> str:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw)})

    # On foot and without a horse nothing changes
    plain = text_for(build_state())
    assert "riding" not in plain and "Your horse" not in plain
    # Sitting names the piece of furniture, for the player and for the companion. Without a name it stays 'sitting'
    seated = text_for(build_state(is_sitting=True, is_player_sitting=True, seat_name="Wooden Bench"))
    assert seated.count("sitting on Wooden Bench") == 2
    assert ", sitting," in text_for(build_state(is_player_sitting=True)) and "sitting on" not in text_for(build_state(is_player_sitting=True))
    # A horse that stands around: where it is, seen from the companion
    assert f"Your horse: Horse {HORSE_REF_ID}, 6 m left of you" in text_for(build_state(horse_ref_id=HORSE_REF_ID))
    # In the saddle: both lines name the horses, the rider still follows, and the horse line says so
    riding = text_for(build_state(horse_ref_id=HORSE_REF_ID, is_riding=True, is_player_riding=True, is_ride_locked=True))
    assert "riding Shadowmere" in riding and "riding Horse, following" in riding
    assert f"Your horse: Horse {HORSE_REF_ID}, you ride it, ride_lock on" in riding
    # Riding somewhere: the target the companion walks to on foot is what it rides to in the saddle
    assert "riding Horse, riding to Whiterun Guard 000F6845" in text_for(build_state(horse_ref_id=HORSE_REF_ID, is_riding=True, walk_target_ref_id="000F6845"))
    assert "walking to Whiterun Guard 000F6845" in text_for(build_state(walk_target_ref_id="000F6845"))
    # A plugin too old to tell which horse it is: 'riding' without a name
    old = build_state(horse_ref_id=HORSE_REF_ID, is_riding=True)
    old[KEY_COMPANION_MOUNT] = UNKNOWN_MOUNT_NAME
    assert "riding, following" in text_for(old)

    # The simulator keeps track like the game mod does
    assert run_command("mount", "", "")[0] is False
    assert run_command("mount", "", "", horse_ref_id=HORSE_REF_ID)[0] is True
    assert horse_after("claim_horse", HORSE_REF_ID, "") == HORSE_REF_ID and horse_after("follow", "", HORSE_REF_ID) == HORSE_REF_ID
    assert is_riding_after("mount", False) and is_riding_after("wait", True) and not is_riding_after("dismount", True)
    assert is_ride_locked_after("ride_lock", "on", False) and not is_ride_locked_after("ride_lock", "off", True)

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received_commands: list[dict] = []

        def game():
            results: list[dict] = []
            for _ in range(5):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(), consts.KEY_RESULTS: results}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}
                           for sent in reply[consts.KEY_COMMANDS]]

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # What the server turns down itself never reaches the game
        assert "needs a target" in command({"action": "claim_horse"})["message"]
        assert "needs 'on' or 'off'" in command({"action": "ride_lock"})["message"]
        assert "needs 'on' or 'off'" in command({"action": "ride_lock", "detail": "maybe"})["message"]
        assert command({"action": "claim_horse", "target": HORSE_REF_ID.lower()})["status"] == consts.STATUS_DONE
        assert command({"action": "mount"})["status"] == consts.STATUS_DONE
        assert command({"action": "ride_lock", "detail": "ON", "target": HORSE_REF_ID})["status"] == consts.STATUS_DONE
        assert command({"action": "dismount", "target": HORSE_REF_ID})["status"] == consts.STATUS_DONE
        sent = [(one[consts.KEY_COMMAND_ACTION], one[consts.KEY_COMMAND_TARGET], one[consts.KEY_COMMAND_DETAIL]) for one in received_commands]
        assert sent == [("claim_horse", HORSE_REF_ID, ""), ("mount", "", ""), ("ride_lock", "", "on"), ("dismount", "", "")]


def test_a_horse_is_called_and_the_players_horse_shows_in_state():
    from src.companion.fake_game import HORSE_REF_ID, PLAYER_HORSE_REF_ID, build_state, called_horse_after, run_command
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict) -> str:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw)})

    # Without a horse the player has ridden there is no line
    assert "Player's horse" not in text_for(build_state())
    # The horse the player rode last: where it stands seen from the player, right below the line of the player
    lines = text_for(build_state(player_horse_ref_id=PLAYER_HORSE_REF_ID)).split("\n")
    place = [number for number, line in enumerate(lines) if line.startswith("Player's horse")][0]
    assert lines[place] == f"Player's horse: Shadowmere {PLAYER_HORSE_REF_ID}, 14 m behind of the player"
    assert lines[place - 1].startswith("Player ")
    # In the saddle, and far away
    assert f"Player's horse: Shadowmere {PLAYER_HORSE_REF_ID}, the player rides it" in text_for(build_state(player_horse_ref_id=PLAYER_HORSE_REF_ID, is_player_riding=True))
    away = build_state(player_horse_ref_id=PLAYER_HORSE_REF_ID)
    away["player_horse"][1] = -1
    assert "not nearby (call_horse brings it to the player)" in text_for(away)
    # A called horse says so, whichever of the two it is
    both = dict(player_horse_ref_id=PLAYER_HORSE_REF_ID, horse_ref_id=HORSE_REF_ID)
    assert "14 m behind of the player, running to the player" in text_for(build_state(called_horse_ref_id=PLAYER_HORSE_REF_ID, **both))
    called_own = text_for(build_state(called_horse_ref_id=HORSE_REF_ID, **both))
    assert "6 m left of you, running to the player" in called_own and "of the player, running" not in called_own

    # The simulator: without a target the companion's own horse, without one of its own the player's. No other horse comes
    assert called_horse_after("call_horse", "", HORSE_REF_ID, "") == HORSE_REF_ID
    assert called_horse_after("call_horse", "", "", "") == PLAYER_HORSE_REF_ID
    assert called_horse_after("mount", "", HORSE_REF_ID, HORSE_REF_ID) == "" and called_horse_after("say", "", HORSE_REF_ID, HORSE_REF_ID) == HORSE_REF_ID
    assert run_command("call_horse", "", "")[1].startswith("Shadowmere runs to the player")
    assert run_command("call_horse", "000A0001", "", horse_ref_id=HORSE_REF_ID)[0] is False

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            results: list[dict] = []
            for _ in range(3):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(), consts.KEY_RESULTS: results}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}
                           for sent in reply[consts.KEY_COMMANDS]]

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # With and without a target: both reach the game
        for arguments in ({"action": "call_horse"}, {"action": "call_horse", "target": PLAYER_HORSE_REF_ID.lower()}):
            assert tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))["status"] == consts.STATUS_DONE
        assert [(one[consts.KEY_COMMAND_ACTION], one[consts.KEY_COMMAND_TARGET]) for one in received_commands] == [("call_horse", ""), ("call_horse", PLAYER_HORSE_REF_ID)]


def start_busy_game(client: TestClient, game: dict) -> None:
    """A game for the tests of the chain: it asks again and again, tells whether its companion is busy (game["busy"])
    and how often something failed (game["fails"]), keeps the commands it gets in game["commands"], and is busy
    from a move_to on until the test says otherwise. game["stop"] ends it"""
    from src.companion.fake_game import build_state

    def run():
        results: list[dict] = []
        is_state_wanted = False
        while not game.get("stop"):
            body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: game["busy"], consts.KEY_FAILS: game["fails"]}
            if is_state_wanted:
                body[consts.KEY_STATE] = build_state()
            try:
                reply = client.post(consts.ROUTE_GAME, json=body).json()
            except Exception:
                return
            results = []
            is_state_wanted = False
            for command in reply.get(consts.KEY_COMMANDS, []):
                if command[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                    is_state_wanted = True
                    continue
                game["commands"].append((command[consts.KEY_COMMAND_ACTION], command[consts.KEY_COMMAND_TARGET], command[consts.KEY_COMMAND_DETAIL]))
                if command[consts.KEY_COMMAND_ACTION] == consts.ACTION_MOVE_TO:
                    game["busy"] = 1
                # game["reject"]: the actions this game refuses
                if command[consts.KEY_COMMAND_ACTION] in game.get("reject", ()):
                    results.append({consts.KEY_COMMAND_ID: command[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: False, consts.KEY_RESULT_MESSAGE: "It is dead already."})
                    continue
                results.append({consts.KEY_COMMAND_ID: command[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Started."})

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)


def wait_for(check, seconds: float = 6.0) -> bool:
    """Waits until check() is true, at most this long"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def test_commands_wait_for_each_other_in_a_chain():
    walk = {"action": "move_to", "target": "000A0001"}

    # Without a game mod that tells whether the companion is busy nothing can wait. What is wrong shows before anything starts
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "tells when your character is done" in command({**walk, "then": [{"action": "gesture", "detail": "wave"}]})["message"]
        assert "Step 2 of 'then' has the unknown action 'fly'" in command({**walk, "then": [{"action": "wait"}, {"action": "fly"}]})["message"]
        assert "needs a target for 'loot'" in command({**walk, "then": [{"action": "loot"}]})["message"]
        assert "seconds of the pause" in command({**walk, "then": [{"action": "pause", "detail": "long"}]})["message"]
        assert "at most 5 commands" in command({**walk, "then": [{"action": "wait"}] * 6})["message"]
        assert "pause only waits" not in command({"action": "pause", "detail": "5"})["message"]
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"then", "after_previous"} <= set(tools[0]["inputSchema"]["properties"])

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        def state() -> str:
            return tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {}}))

        game = {"busy": 0, "fails": 0, "commands": [], "stop": False}
        start_busy_game(client, game)
        try:
            # The walk starts at once, the rest waits until the companion has arrived
            answer = command({**walk, "then": [{"action": "gesture", "detail": "Wave"}, {"action": "pause", "detail": "1"}, {"action": "follow"}]})
            assert answer["status"] == consts.STATUS_DONE
            assert answer["message"].endswith("Queue: now move_to 000A0001; then gesture Wave; then pause 1; then follow.")
            time.sleep(1.5)
            assert game["commands"] == [("move_to", "000A0001", "")]
            assert "Queue: now move_to 000A0001; then gesture Wave; then pause 1; then follow" in state()
            # What runs at once leaves the chain alone
            assert command({"action": "look_at", "target": "00000014"})["status"] == consts.STATUS_DONE
            game["busy"] = 0
            assert wait_for(lambda: len(game["commands"]) == 4)
            assert game["commands"][1:] == [("look_at", "00000014", ""), ("gesture", "", "wave"), ("follow", "", "")]
            assert wait_for(lambda: "Queue: all done (gesture Wave, then pause 1, then follow)." in state())
            assert "Queue:" not in state()

            # A step that fails is skipped, the ones behind it go on
            game["commands"].clear()
            command({**walk, "then": [{"action": "gesture", "detail": "wave"}]})
            game["fails"] = 1
            game["busy"] = 0
            assert wait_for(lambda: "Queue: 'move_to 000A0001' did not work out, so it was skipped. The rest goes on: gesture wave." in state())
            assert wait_for(lambda: game["commands"] == [("move_to", "000A0001", ""), ("gesture", "", "wave")])
            assert wait_for(lambda: "Queue: finished. Done: gesture wave." in state())
            # A gesture has started and cannot go wrong any more: a failure the game tells of meanwhile (the walk
            # before it, told late) is not blamed on the gesture, and the chain goes on
            game["commands"].clear()
            assert wait_for(lambda: "Queue:" not in state())
            command({"action": "gesture", "detail": "dance", "then": [{"action": "pause", "detail": "3"}, {"action": "follow"}]})
            game["fails"] = 2
            assert wait_for(lambda: game["commands"] == [("gesture", "", "dance"), ("follow", "", "")], 10.0)
            assert wait_for(lambda: "Queue: all done (pause 3, then follow)." in state())
            # The same if the game refuses the command itself or a step in the middle (attack on someone who is dead already)
            game["commands"].clear()
            game["reject"] = [consts.ACTION_ATTACK]
            answer = command({"action": "attack", "target": "FF000A03", "then": [{"action": "attack", "target": "FF000A03"}, {"action": "follow"}]})
            assert answer["status"] == consts.STATUS_FAILED and "It was skipped, what you put behind it goes on. Queue:" in answer["message"]
            assert wait_for(lambda: game["commands"] == [("attack", "FF000A03", ""), ("attack", "FF000A03", ""), ("follow", "", "")])
            assert wait_for(lambda: "Queue: 'attack FF000A03' failed (It is dead already.), so it was skipped. The rest goes on: follow." in state())
            game["reject"] = []
            assert wait_for(lambda: "Queue:" not in state())

            # A command that moves the companion throws away what waits
            game["commands"].clear()
            command({**walk, "then": [{"action": "gesture", "detail": "wave"}]})
            answer = command({"action": "follow"})
            assert "This threw away what waited in the queue: gesture wave." in answer["message"]
            game["busy"] = 0
            time.sleep(1.5)
            assert game["commands"] == [("move_to", "000A0001", ""), ("follow", "", "")]

            # after_previous: behind a walk that was started without a chain
            game["commands"].clear()
            command(walk)
            answer = command({"action": "gesture", "detail": "wave", "after_previous": True})
            assert answer["status"] == consts.STATUS_QUEUED and "then gesture wave" in answer["message"]
            time.sleep(1.5)
            assert game["commands"] == [("move_to", "000A0001", "")]
            game["busy"] = 0
            assert wait_for(lambda: game["commands"] == [("move_to", "000A0001", ""), ("gesture", "", "wave")])
        finally:
            game["stop"] = True


def write_master_file(folder, idles: list[tuple]) -> None:
    """Writes a small master file like Skyrim.esm with these animations: (form ID, name, event, parent, sitting condition)"""
    import os
    import struct

    def field(name: str, data: bytes) -> bytes:
        return name.encode("ascii") + struct.pack("<H", len(data)) + data

    def record(kind: bytes, form_id: int, body: bytes) -> bytes:
        return kind + struct.pack("<III", len(body), 0, form_id) + bytes(8) + body

    records = b""
    for form_id, name, event, parent, needs_sitting in idles:
        body = field("EDID", name.encode("ascii") + b"\0")
        if needs_sitting:
            # A condition: GetSitting (function 159) equals 3
            body += field("CTDA", bytes(4) + struct.pack("<f", 3.0) + struct.pack("<H", 159) + bytes(22))
        if event != "":
            body += field("ENAM", event.encode("ascii") + b"\0")
        body += field("ANAM", struct.pack("<II", parent, 0))
        records += record(b"IDLE", form_id, body)
    group = b"GRUP" + struct.pack("<I", 24 + len(records)) + b"IDLE" + bytes(12) + records
    other_group = b"GRUP" + struct.pack("<I", 24 + 5) + b"KYWD" + bytes(12) + b"12345"
    os.makedirs(os.path.join(folder, "Data"))
    with open(os.path.join(folder, "Data", "Skyrim.esm"), "wb") as opened:
        opened.write(record(b"TES4", 0, b"header") + other_group + group)


def test_gestures_are_found_in_the_game_file_and_play_sitting_or_by_event(tmp_path):
    from src.companion.gestures import GESTURE_NAMES, pick_gesture
    from src.companion.idle_search import IdleLibrary, split_name
    from src.companion.ref_ids import parse_ref_id, split_form_id

    # The companion's own table: events for the gestures of Mantella's table, animations for sitting, and being drunk
    drink = pick_gesture(" Drink ")
    assert (drink["id"], drink["event"], drink["sit_id"], drink["sit_event"]) == ("000FD68B", "IdleDrink", "00065D07", "ChairDrinkingStart")
    assert pick_gesture("eat")["sit_stop_event"] == "ChairEatingStop"
    assert pick_gesture("pass out")["id"] == "" and pick_gesture("pass_out")["sit_id"] == "00106274"
    drunk = pick_gesture("drunk")
    assert (drunk["event"], drunk["stop_event"], drunk["state"]) == ("DrunkStart", "DrunkStop", "1")
    assert pick_gesture("cross_arms")["event"] == "OffsetArmsCrossedStart" and pick_gesture("moonwalk") is None
    assert {"drunk", "sway", "drink_at_table", "pass_out", "wave"} <= set(GESTURE_NAMES)

    # The animations of the game: groups have no event, conditions of the entry above count, parts of names are compared
    assert split_name("ChairEatingStart") == ["chair", "eating", "start"] and split_name("idle_A_sway_fastTrans") == ["idle", "a", "sway", "fast", "trans"]
    write_master_file(tmp_path, [
        (0x000640FB, "DrinkingRoot", "", 0, False),
        (0x00065D07, "ChairDrinkingStart", "ChairDrinkingStart", 0x000640FB, True),
        (0x000FD68B, "IdleDrink", "IdleDrink", 0, False),
        (0x00103656, "IdleMQ201Drink", "IdleMQ201Drink", 0, False),
        (0x00105D46, "IdlePrisonerBeat", "IdlePrisonerBeat", 0, False),
        (0x00102EC1, "IdleEatSoup", "IdleEatSoup", 0, False),
        (0x0010A2A3, "DeerDeathDrink", "DeathAnimation", 0, False),
    ])
    library = IdleLibrary(str(tmp_path))
    found, total = library.find("drink")
    assert [entry.name for entry in found] == ["IdleDrink", "IdleMQ201Drink", "ChairDrinkingStart"] and total == 3
    assert found[2].needs_sitting and found[2].has_conditions and not found[0].has_conditions
    assert [entry.name for entry in library.find("eat")[0]] == ["IdleEatSoup"]
    assert library.get("idlemq201drink").ref_id == "00103656" and library.get("000fd68b").name == "IdleDrink" and library.get("DrinkingRoot") is None
    missing = IdleLibrary(str(tmp_path / "nowhere"))
    assert missing.find("drink") == ([], 0) and "could not read the animations" in missing.problem

    with TestClient(create_app(TOKEN, hold_seconds=5, game_folder=str(tmp_path))) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        # find_gesture answers without the game, and only with the gestures that have a short name: the other
        # animations of the game mostly did nothing when they were played
        text = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"find_gesture": "Drink"}}))
        assert text == "Gestures for 'Drink': drink, drink_at_table, drinkpotion. Play one with the command gesture and its name in 'detail'."
        assert tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"find_gesture": "moonwalk"}})).startswith("No gesture has 'moonwalk' in its name. The gestures: ")
        for unknown in ("moonwalk", "idlemq201drink", "00103656"):
            refused = command({"action": "gesture", "detail": unknown})
            assert refused["status"] == consts.STATUS_FAILED and refused["message"].startswith(f"There is no gesture '{unknown}'. The gestures: ")

        game = {"busy": 0, "fails": 0, "commands": [], "stop": False}
        received: list[dict] = []

        def game_loop():
            results: list[dict] = []
            for _ in range(5):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results}).json()
                received.extend(reply.get(consts.KEY_COMMANDS, []))
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}
                           for sent in reply.get(consts.KEY_COMMANDS, [])]

        threading.Thread(target=game_loop, daemon=True).start()
        time.sleep(0.3)
        # A gesture with a short name carries its event and what it plays while sitting
        assert command({"action": "gesture", "detail": "drink"})["status"] == consts.STATUS_DONE
        sent = received[0]
        assert (sent[consts.KEY_COMMAND_ITEM_HIGH], sent[consts.KEY_COMMAND_ITEM_LOW]) == split_form_id(parse_ref_id("000FD68B"))
        assert (sent[consts.KEY_COMMAND_SIT_ITEM_HIGH], sent[consts.KEY_COMMAND_SIT_ITEM_LOW]) == split_form_id(parse_ref_id("00065D07"))
        assert (sent[consts.KEY_COMMAND_TALK], sent[consts.KEY_COMMAND_SIT_EVENT], sent[consts.KEY_COMMAND_GESTURE_STATE]) == ("IdleDrink", "ChairDrinkingStart", 0)
        # The companion marks a weapon of its own as a favorite: the game gets 1 for on and 0 for off
        assert command({"action": "favorite", "target": "0001397E"})["status"] == consts.STATUS_DONE
        assert (received[1][consts.KEY_COMMAND_ACTION], received[1][consts.KEY_COMMAND_COUNT]) == ("favorite", 1)
        assert command({"action": "favorite", "target": "0001397E", "detail": "Off"})["status"] == consts.STATUS_DONE
        assert received[2][consts.KEY_COMMAND_COUNT] == 0
        assert "'detail' is 'on'" in command({"action": "favorite", "target": "0001397E", "detail": "maybe"})["message"]
        assert "needs a target" in command({"action": "favorite"})["message"]


def test_a_horse_is_renamed_and_called_to_its_owner():
    from src.companion.fake_game import HORSE_REF_ID, PLAYER_HORSE_REF_ID, build_state, horse_names_after, run_command
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    # The simulator: the new name shows wherever the horse is named
    names = horse_names_after("rename_horse", "", "Klumpatsch", HORSE_REF_ID, {})
    names = horse_names_after("rename_horse", PLAYER_HORSE_REF_ID, "Poldi", HORSE_REF_ID, names)
    assert names == {HORSE_REF_ID: "Klumpatsch", PLAYER_HORSE_REF_ID: "Poldi"} and horse_names_after("follow", "", "x", HORSE_REF_ID, names) == names
    text = describe_state({"connected": True, "fresh": True, "state": read_game_state(
        build_state(horse_ref_id=HORSE_REF_ID, player_horse_ref_id=PLAYER_HORSE_REF_ID, horse_names=names))})
    assert f"Your horse: Klumpatsch {HORSE_REF_ID}" in text and f"Player's horse: Poldi {PLAYER_HORSE_REF_ID}" in text
    assert run_command("rename_horse", "", "", detail="Poldi")[0] is False
    # release_horse gives the horse back: the companion has none afterwards
    from src.companion.fake_game import horse_after
    assert run_command(consts.ACTION_RELEASE_HORSE, "", "")[0] is False
    assert run_command(consts.ACTION_RELEASE_HORSE, "", "", horse_ref_id=HORSE_REF_ID)[0] is True
    assert horse_after(consts.ACTION_RELEASE_HORSE, "", HORSE_REF_ID) == "" and horse_after(consts.ACTION_RELEASE_HORSE, PLAYER_HORSE_REF_ID, HORSE_REF_ID) == HORSE_REF_ID
    assert consts.ACTION_RELEASE_HORSE in consts.PLAYER_ACTIONS and consts.ACTION_RELEASE_HORSE not in consts.ACTIONS_REQUIRING_TARGET
    # Every horse runs to its owner, unless the detail says otherwise
    assert run_command("call_horse", "", "", horse_ref_id=HORSE_REF_ID)[1] == "Horse runs to you and stays there."
    assert run_command("call_horse", PLAYER_HORSE_REF_ID, "", horse_ref_id=HORSE_REF_ID)[1] == "Shadowmere runs to the player and stays there."
    assert run_command("call_horse", "", "", detail="player", horse_ref_id=HORSE_REF_ID)[1] == "Horse runs to the player and stays there."

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received: list[dict] = []

        def game_loop():
            results: list[dict] = []
            for _ in range(4):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results}).json()
                received.extend(reply.get(consts.KEY_COMMANDS, []))
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}
                           for sent in reply.get(consts.KEY_COMMANDS, [])]

        threading.Thread(target=game_loop, daemon=True).start()
        time.sleep(0.3)
        assert "needs the new name" in command({"action": "rename_horse"})["message"]
        assert "at most 40 characters" in command({"action": "rename_horse", "detail": "x" * 41})["message"]
        assert "whom the horse runs to" in command({"action": "call_horse", "detail": "home"})["message"]
        assert command({"action": "rename_horse", "detail": "  Klumpatsch   der Zweite "})["status"] == consts.STATUS_DONE
        assert command({"action": "rename_horse", "target": PLAYER_HORSE_REF_ID.lower(), "detail": "Poldi"})["status"] == consts.STATUS_DONE
        assert command({"action": "call_horse", "detail": "Player"})["status"] == consts.STATUS_DONE
        sent = [(one[consts.KEY_COMMAND_ACTION], one[consts.KEY_COMMAND_TARGET], one[consts.KEY_COMMAND_DETAIL]) for one in received]
        assert sent == [("rename_horse", "", "Klumpatsch der Zweite"), ("rename_horse", PLAYER_HORSE_REF_ID, "Poldi"), ("call_horse", "", "player")]


def test_favorites_of_the_player_are_marked_in_the_inventory():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    raw = build_state(inventory="player")
    key = game_state.KEY_INVENTORY_PLAYER
    # The fourth number of an item: 1 = equipped, 2 = a favorite of the player, 3 = both. The simulator's sword is equipped
    assert raw[key][4] == 1
    raw[key][4] = 3
    raw[key][8] = 2
    text = describe_state({"connected": True, "fresh": True, "state": game_state.read_game_state(raw), "inventory_wanted": "player"})
    assert "Iron Sword 00012E49 (equipped, favorite)" in text
    assert "Potion of Minor Healing 0003EADD x5 (favorite)" in text
    assert "Lockpick 0000000A x12," not in text and "Lockpick 0000000A x12 (" not in text
    # Without the flag nothing changes
    plain = describe_state({"connected": True, "fresh": True, "state": game_state.read_game_state(build_state(inventory="player")), "inventory_wanted": "player"})
    assert "Iron Sword 00012E49 (equipped)" in plain and "favorite" not in plain


def test_empty_containers_and_bodies_show_at_once():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    # A newer plugin of the game mod counts what an open container holds: 2 = empty, 8 = three different items.
    # 1 stays 'locked', 0 means that it does not tell. For the dead the count plus 1 comes where 'hostile' is for the living
    raw = build_state(has_things=True)
    things = [line.split("\t") for line in raw[game_state.KEY_THINGS]]
    chest = next(parts for parts in things if parts[5] == "Chest")
    chest[4] = "8"
    raw[game_state.KEY_THINGS] = ["\t".join(parts) for parts in things] + ["1\t0005A4FF\t3\t0\t2\tBarrel"]
    values = raw[game_state.KEY_NEARBY_VALUES]
    dead_place = next(place for place in range(0, len(values), game_state.VALUES_PER_NEARBY_CHARACTER) if values[place + 4] == 1)
    state = game_state.read_game_state(raw)
    by_name = {thing["name"]: thing for thing in state["things"]}
    assert by_name["Chest"]["item_count"] == 3 and by_name["Barrel"]["item_count"] == 0
    assert by_name["Strongbox"]["locked"] is True and "item_count" not in by_name["Strongbox"]
    assert all("item_count" not in character for character in state["nearby"])
    text = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, "things_wanted": True})
    assert "Chest, container, 3 different items, " in text and "Barrel, container, empty, " in text and "Strongbox, container, locked, " in text
    assert "Bandit Outlaw, dead, 5 m" in text
    for sent, shown in ((1, "dead, empty, "), (2, "dead, 1 item, "), (5, "dead, 4 different items, ")):
        values[dead_place + 3] = sent
        counted = game_state.read_game_state(raw)
        body = next(character for character in counted["nearby"] if character["dead"])
        assert body["item_count"] == sent - 1 and body["hostile"] is False
        assert f"Bandit Outlaw, {shown}" in describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": counted})


def test_inventory_filter_gives_a_short_list():
    import asyncio

    from src.companion import game_state
    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state

    # The fourth number of an item: 1 = equipped, 2 = favorite, and in steps of 16 what kind of weapon or armor it is
    # (8 = bow, 2 = sword, 20 = shield). A game mod that does not tell the kind leaves it at the flags
    raw = build_state(inventory=consts.INVENTORY_BOTH)
    own = raw[game_state.KEY_INVENTORY_OWN]
    own[4], own[8], own[12] = 1 + 2 + 16 * 2, 1 + 16 * 20, 2 + 16 * 8
    player = raw[game_state.KEY_INVENTORY_PLAYER]
    player[4], player[8] = 1, 2
    state = game_state.read_game_state(raw)
    assert [item["kind"] for item in state[game_state.KEY_INVENTORY_OWN]["items"][:4]] == ["sword", "shield", "bow", ""]

    def text_for(inventory: str, item_filter: str) -> str:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state,
                               "inventory_wanted": inventory, "inventory_filter": item_filter})

    favorites = text_for(consts.INVENTORY_OWN, consts.FILTER_FAVORITES)
    assert "Your favorites: Steel Sword 00013989 (sword, equipped), Hunting Bow 00013985 (bow)" in favorites
    assert "Your inventory" not in favorites and "Iron Arrow" not in favorites
    assert "You wear and hold: Steel Sword 00013989 (sword), Iron Shield 00012EB6 (shield)" in text_for(consts.INVENTORY_OWN, consts.FILTER_EQUIPPED)
    both = text_for(consts.INVENTORY_BOTH, consts.FILTER_FAVORITES)
    assert "The player's favorites: Potion of Minor Healing 0003EADD (potion or food) x5" in both and "Your favorites: Steel Sword" in both
    assert "The player wears and holds: Iron Sword 00012E49 (weapon)" in text_for(consts.INVENTORY_PLAYER, consts.FILTER_EQUIPPED)
    assert "Your inventory:" in text_for(consts.INVENTORY_OWN, "")
    plain = game_state.read_game_state(build_state(inventory=consts.INVENTORY_OWN))
    none = describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": plain,
                           "inventory_wanted": consts.INVENTORY_OWN, "inventory_filter": consts.FILTER_FAVORITES})
    assert "Your favorites: none. Mark a weapon" in none and "your arrows" in none

    # The game is told the filter, so that it only collects that part
    async def request(**wanted: str) -> dict:
        queue = CompanionCommandQueue(hold_seconds=5)
        game = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        asyncio.create_task(queue.get_state(refresh_timeout_seconds=0.1, **wanted))
        return (await game)[0]

    sent = asyncio.run(request(inventory=consts.INVENTORY_OWN, inventory_filter=consts.FILTER_EQUIPPED))
    assert (sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_INVENTORY_FILTER]) == (consts.INVENTORY_OWN, consts.FILTER_EQUIPPED)
    assert consts.KEY_COMMAND_INVENTORY_FILTER not in asyncio.run(request(inventory=consts.INVENTORY_OWN))
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[1]["inputSchema"]["properties"]["filter"]["enum"] == ["favorites", "equipped", "all"]
        assert "favorite" in tools[0]["inputSchema"]["properties"]["action"]["enum"]


def test_events_books_and_sneaking():
    from src.companion import game_state
    from src.companion.fake_game import BOOK_REF_ID, SNEAK_NUMBERS, build_state, run_command, sneak_mode_after
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(raw: dict, **wanted) -> str:
        return describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), **wanted})

    # Events: the plugin writes how long ago, the time of day in the game, urgent or not, and what happened. Broken lines are left out
    assert game_state.read_events(["12\t874\t1\tBandit started fighting the player.", "broken", "x\t1\t0\tNo age", "3\t875\t0\t "]) == [
        {"seconds_ago": 12, "game_time": "14:34", "urgent": True, "text": "Bandit started fighting the player."}]
    assert game_state.read_events(None) == [] and "events" not in read_game_state(build_state())
    assert len(read_game_state(build_state(has_events=True))["events"]) == 3

    # Sneaking: the player, the companion, and the command sneak that takes the companion out of doing as the player does
    plain = text_for(build_state())
    assert "sneaking" not in plain and "sneak o" not in plain
    sneaking = text_for(build_state(is_sneaking=True, is_player_sneaking=True, sneak_mode=SNEAK_NUMBERS["on"]))
    assert "weapon drawn" not in sneaking and sneaking.count("sneaking") == 2 and "sneaking, sneak on" in sneaking
    assert "following, sneak off" in text_for(build_state(sneak_mode=SNEAK_NUMBERS["off"]))
    # A game mod from before says nothing about sneaking, and is still read
    old = build_state(is_sneaking=True, is_player_sneaking=True)
    old[game_state.KEY_PLAYER_VALUES] = old[game_state.KEY_PLAYER_VALUES][:game_state.PLAYER_VALUE_COUNT_WITHOUT_SNEAKING]
    old[game_state.KEY_COMPANION_VALUES] = old[game_state.KEY_COMPANION_VALUES][:game_state.COMPANION_VALUE_COUNT_WITHOUT_SNEAKING]
    old_state = read_game_state(old)
    assert "sneaking" not in old_state["player"] and "sneaking" not in old_state["companion"] and old_state["companion"]["name"] == "Lydia"
    assert sneak_mode_after("sneak", "on", 0) == 1 and sneak_mode_after("sneak", "auto", 2) == 0 and sneak_mode_after("follow", "", 2) == 2
    assert "whatever the player does" in run_command("sneak", "", "", detail="on")[1]

    # A book: one page of it, with where the next one is. The pieces of a paragraph are put together again
    page_one = text_for(build_state(read=BOOK_REF_ID), read_wanted=BOOK_REF_ID).splitlines()
    assert page_one[-4:] == [f"Reading 'Thief's Letter' (note, ID {BOOK_REF_ID}), page 1 of 2:", "Dear [Thief],",
                             "meet me at the mill when the moon is up.", f"More: STATE with read '{BOOK_REF_ID}' and page 2."]
    page_two = text_for(build_state(read=BOOK_REF_ID, read_page=2), read_wanted=BOOK_REF_ID).splitlines()
    assert page_two[-2:] == [f"Reading 'Thief's Letter' (note, ID {BOOK_REF_ID}), page 2 of 2:", "Bring the claw."]
    assert "is not a book or a note" in text_for(build_state(read="0005A410"), read_wanted="0005A410")
    assert "interface version 17" in text_for(build_state(), read_wanted=BOOK_REF_ID)
    assert "did not send the page in time" in text_for(build_state(), read_missed=True)
    without_text = build_state(read=BOOK_REF_ID)
    without_text[game_state.KEY_BOOK] = [f"0\t0\t2\t{BOOK_REF_ID}\tSpell Tome: Flames"]
    assert f"'Spell Tome: Flames' (spell tome, ID {BOOK_REF_ID}) has no text to read." in text_for(without_text, read_wanted=BOOK_REF_ID)

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        def state_lines(arguments: dict) -> list[str]:
            return tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": arguments})).splitlines()

        received_commands: list[dict] = []

        def game():
            results: list[dict] = []
            urgent: list[str] = ["4\t875\t1\tBandit Outlaw started fighting you."]
            state = build_state(has_events=True)
            for _ in range(8):
                body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: 0, consts.KEY_FAILS: 0}
                if state is not None:
                    body[consts.KEY_STATE] = state
                if len(urgent) > 0 and len(results) > 0:
                    # The urgent events go along with the results of the commands, as the game mod sends them
                    body[consts.KEY_EVENTS] = urgent
                    urgent = []
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                state = None
                results = []
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    received_commands.append(sent)
                    if sent[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                        state = build_state(read=str(sent.get(consts.KEY_COMMAND_READ) or ""), read_page=int(sent.get(consts.KEY_COMMAND_READ_PAGE) or 1))
                    else:
                        results.append({consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # What the server turns down itself never reaches the game. The urgent event of the first state hangs onto
        # the first answer to a command, whatever became of the command, and only onto that one
        refused = command({"action": "sneak"})["message"]
        assert "sneak needs one of these" in refused and " Meanwhile: Bandit started fighting the player. (" in refused
        refused_again = command({"action": "sneak", "detail": "maybe"})["message"]
        assert "sneak needs one of these" in refused_again and "Meanwhile" not in refused_again
        assert "is not the ID of a book" in tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"read": "the letter"}}))["message"]
        # The urgent event that came with the result hangs onto the answer of the command, with how long ago it was
        sneak = command({"action": "sneak", "detail": "ON", "target": "000A2C94"})
        assert sneak["status"] == consts.STATUS_DONE
        assert sneak["message"].startswith("Done. Meanwhile: Bandit Outlaw started fighting you. (") and sneak["message"].endswith(" s ago)")
        # The other events of the first state were kept until the AI asked. The urgent ones that were told already are not told again
        first = state_lines({"read": BOOK_REF_ID.lower(), "page": 2})
        events = first[first.index("Events:") + 1:first.index("Events:") + 3]
        assert [line.partition(": ")[2] for line in events] == [
            "The player entered Whiterun.", "The player took 2 hits from Bandit and lost about 15 health (85% left)."]
        assert events[0].endswith("(14:30): The player entered Whiterun.") and " s ago (14:35): The player took" in events[1]
        assert not any("started fighting" in line for line in first)
        assert first[-2:] == [f"Reading 'Thief's Letter' (note, ID {BOOK_REF_ID}), page 2 of 2:", "Bring the claw."]
        # Each event is given only once, and a state without 'read' carries no page
        second = state_lines({})
        assert "Events:" not in second and not any(line.startswith("Reading") for line in second)
        sent = [(one[consts.KEY_COMMAND_ACTION], one[consts.KEY_COMMAND_TARGET], one[consts.KEY_COMMAND_DETAIL]) for one in received_commands
                if one[consts.KEY_COMMAND_ACTION] != consts.ACTION_REPORT_STATE]
        assert sent == [("sneak", "", "on")]
        reads = [(one.get(consts.KEY_COMMAND_READ), one.get(consts.KEY_COMMAND_READ_HIGH), one.get(consts.KEY_COMMAND_READ_LOW), one.get(consts.KEY_COMMAND_READ_PAGE))
                 for one in received_commands if one[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE]
        assert reads == [(BOOK_REF_ID, 0x0001, 0xACDA, 2), (None, None, None, None)]


def test_inventory_lines_of_the_plugin_and_seconds_for_a_held_spell():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    # A newer plugin lists an inventory as texts: how many kinds, then ID, how many, form type, flags and name per item.
    # The flags: 1 = worn or held, 2 = favorite, and 16 times the kind (8 = bow, 20 = shield)
    raw = build_state()
    raw[game_state.KEY_INVENTORY_PLAYER + game_state.LINES_SUFFIX] = [
        "3", "00013985\t1\t41\t" + str(1 + 2 + 16 * 8) + "\tHunting Bow", "00012EB6\t1\t26\t" + str(2 + 16 * 20) + "\tIron Shield",
        "0000000F\t250\t32\t0\tGold", "broken", "0003EADD\tfive\t46\t0\tPotion"]
    inventory = read_game_state(raw)[game_state.KEY_INVENTORY_PLAYER]
    assert inventory["total"] == 3
    # Gold is told as a number elsewhere, and a line that cannot be read is left out
    assert inventory["items"] == [
        {"id": "00013985", "name": "Hunting Bow", "count": 1, "group": "Weapons", "equipped": True, "favorite": True, "kind": "bow"},
        {"id": "00012EB6", "name": "Iron Shield", "count": 1, "group": "Armor and clothing", "equipped": False, "favorite": True, "kind": "shield"}]
    text = describe_state({"connected": True, "fresh": True, "state": read_game_state(raw), "inventory_wanted": "player",
                           "inventory_filter": "favorites"})
    assert "Hunting Bow" in text and "Iron Shield" in text
    # A first text that is no number is no inventory
    raw[game_state.KEY_INVENTORY_PLAYER + game_state.LINES_SUFFIX] = ["?"]
    assert game_state.KEY_INVENTORY_PLAYER not in read_game_state(raw)

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            results: list[dict] = []
            for _ in range(3):
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: build_state(), consts.KEY_RESULTS: results}).json()
                received_commands.extend(reply[consts.KEY_COMMANDS])
                results = [{consts.KEY_COMMAND_ID: command[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You cast it."}
                           for command in reply[consts.KEY_COMMANDS]]

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # The number behind the spell is for how long a held spell is held. It does not go to the game as a part of the name
        too_long = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell", "detail": "Flames 60"}}))
        assert too_long["status"] == consts.STATUS_FAILED and "1 to 15" in too_long["message"]
        rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell", "detail": "Flames 6"}})
        assert (received_commands[0][consts.KEY_COMMAND_DETAIL], received_commands[0][consts.KEY_COMMAND_SECONDS]) == ("Flames", 6)
        rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cast_spell", "detail": "Healing Hands"}})
        assert received_commands[1][consts.KEY_COMMAND_DETAIL] == "Healing Hands" and consts.KEY_COMMAND_SECONDS not in received_commands[1]


def test_give_and_take_with_several_items_and_give_junk():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.ref_ids import parse_ref_id, split_form_id

    def halves(item_id: str) -> list[int]:
        return list(split_form_id(parse_ref_id(item_id)))

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received_commands: list[dict] = []
        old_state = build_state()
        old_state[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PROTOCOL] = consts.HAND_OVER_PROTOCOL - 1
        states: list[dict] = [old_state]

        def game():
            results: list[dict] = []
            for _ in range(8):
                body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: 0, consts.KEY_FAILS: 0}
                if len(states) > 0:
                    body[consts.KEY_STATE] = states.pop(0)
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                results = []
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    if sent[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                        # Like the game mod: the inventory only comes when it was asked for
                        states.append(build_state(inventory=str(sent.get(consts.KEY_COMMAND_DETAIL) or "")))
                    else:
                        received_commands.append(sent)
                        results.append({consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True,
                                        consts.KEY_RESULT_MESSAGE: "You give the player: 1 Hunting Bow."})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # A game mod from before this would only hand over the first item, so the server does not send it the list
        too_old = command({"action": "give", "target": "00013985,0001397D"})
        assert too_old["status"] == consts.STATUS_FAILED and "newer game mod" in too_old["message"]
        # Any state of a game mod that is new enough lifts that (here the one the game sends when it is asked for it)
        tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {}}))
        # What the server turns down itself never reaches the game
        assert "is not the ID of an item" in command({"action": "give", "target": "00013985, the bow"})["message"]
        assert "2 items and 3 numbers" in command({"action": "take", "target": "00013985,0001397D", "detail": "1,2,3"})["message"]
        assert "is not how many" in command({"action": "give", "target": "00013985;0001397D", "detail": "1,0"})["message"]
        assert "Only give knows 'junk'" in command({"action": "take", "detail": "junk"})["message"]
        assert not received_commands
        # Several items: the IDs in two halves each with how many (0 = all), in the order they were named, each one once
        given = command({"action": "give", "target": "00013985, 0001397d 00013985", "detail": "1,ALL,5"})
        assert given["status"] == consts.STATUS_DONE and given["message"] == "You give the player: 1 Hunting Bow."
        assert received_commands[0][consts.KEY_COMMAND_ITEMS] == halves("00013985") + [1] + halves("0001397D") + [0]
        assert (received_commands[0][consts.KEY_COMMAND_TARGET], received_commands[0][consts.KEY_COMMAND_DETAIL]) == ("00013985", "00013985,0001397D")
        # One number counts for every item, nothing means one of each
        command({"action": "take", "target": "00012E49,0003EADD", "detail": "3"})
        assert received_commands[1][consts.KEY_COMMAND_ITEMS] == halves("00012E49") + [3] + halves("0003EADD") + [3]
        command({"action": "take", "target": "00012E49,0003EADD"})
        assert received_commands[2][consts.KEY_COMMAND_ITEMS] == halves("00012E49") + [1] + halves("0003EADD") + [1]
        # One item goes the way it always went
        command({"action": "give", "target": "00013985", "detail": "all"})
        assert consts.KEY_COMMAND_ITEMS not in received_commands[3] and received_commands[3][consts.KEY_COMMAND_COUNT] == consts.COUNT_ALL
        # junk: the server asks for the inventory and names what is neither worn, held, a favorite, ammunition, a potion,
        # a key nor gold. Of the simulator's companion that is the bow it does not hold
        junk = command({"action": "give", "detail": "JUNK"})
        assert junk["status"] == consts.STATUS_DONE
        assert received_commands[4][consts.KEY_COMMAND_ITEMS] == halves("00013985") + [0]

    # The simulator answers like the game mod: what changed hands and what did not
    from src.companion.fake_game import hand_over
    is_ok, message = hand_over(consts.ACTION_GIVE, halves("00013985") + [0] + halves("0000BEEF") + [2])
    assert is_ok and message.startswith("You give the player: ") and message.endswith(" Not handed over: number 2 of your list (no such item).")
    assert hand_over(consts.ACTION_TAKE, halves("0000BEEF") + [1]) == (False, "Nothing changed hands. Not handed over: number 1 of your list (no such item).")


def test_a_paused_game_is_told_apart_from_no_game():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue, describe_silence
    from src.companion.state_text import describe_state

    silence_start = "The game has not asked for commands for "
    assert describe_silence(12.7) == silence_start + "12 seconds: it is paused (the player is probably in a menu or at a loading screen) or was closed."
    assert describe_silence(0.2).startswith(silence_start + "1 seconds") and describe_silence(200).startswith(silence_start + "3 minutes")
    # The text of STATE: a game that never came is told apart from one that went silent
    paused_lines = describe_state({"connected": False, "silent_seconds": 42.0, "fresh": False, "state_age_seconds": 50.0,
                                   "state": {"location": "Whiterun", "nearby": []}}).splitlines()
    assert paused_lines[:2] == [describe_silence(42.0), "Last known state, 50.0 seconds old:"]
    assert describe_state({"connected": False, "silent_seconds": None, "state": None}) == "The game is not connected. Is Skyrim running with a loaded save?"

    async def scenario() -> dict:
        answers: dict = {}
        # Without a grace period the game counts as silent right after its request
        queue = CompanionCommandQueue(hold_seconds=0.05, connection_grace_seconds=0.0, pause_wait_seconds=3.0)
        # No game ever asked: the command fails at once
        answers["never"] = await queue.send_command(consts.ACTION_FOLLOW)
        answers["never_state"] = await queue.get_state()
        await queue.poll({"location": "Whiterun"}, [])
        # The game was there and went silent (a menu): the command waits for it, and the game comes back in time
        async def come_back() -> list[dict]:
            await asyncio.sleep(0.5)
            commands = await queue.poll(None, [])
            await queue.poll(None, [{consts.KEY_COMMAND_ID: commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True,
                                     consts.KEY_RESULT_MESSAGE: "You follow the player."}])
            return commands
        game = asyncio.create_task(come_back())
        answers["waited"] = await queue.send_command(consts.ACTION_FOLLOW)
        answers["picked_up"] = await game
        # It stays silent: STATE tells so at once and shows what it knows, and the command gives up after its wait
        await asyncio.sleep(0.1)
        started = time.monotonic()
        answers["paused_state"] = await queue.get_state()
        answers["state_seconds"] = time.monotonic() - started
        answers["given_up"] = await queue.send_command(consts.ACTION_FOLLOW)
        # A command that was given up is not carried out later
        answers["later"] = await queue.poll(None, [])
        return answers

    answers = asyncio.run(scenario())
    assert answers["never"] == {"status": consts.STATUS_GAME_NOT_CONNECTED, "message": "The game is not polling. Is Skyrim running with a loaded save?"}
    assert answers["never_state"]["connected"] is False and answers["never_state"]["silent_seconds"] is None
    assert answers["waited"] == {"status": consts.STATUS_DONE, "message": "You follow the player."} and len(answers["picked_up"]) == 1
    assert answers["paused_state"]["connected"] is False and 0.0 < answers["paused_state"]["silent_seconds"] < 2.0
    assert answers["paused_state"]["state"]["location"] == "Whiterun" and answers["state_seconds"] < 1.0
    assert answers["given_up"]["status"] == consts.STATUS_NOT_DELIVERED
    assert answers["given_up"]["message"].startswith(silence_start + "3 seconds") and "NOT carried out" in answers["given_up"]["message"]
    assert answers["later"] == []


def test_lead_auto_heal_and_trading_with_a_merchant():
    from src.companion import game_state
    from src.companion.fake_game import MERCHANT_REF_ID, build_state
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    def text_for(state: dict, **wanted) -> str:
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state, **wanted})

    # Healing by itself is only told when it is not as usual, leading always
    usual = read_game_state(build_state())
    assert usual["companion"]["auto_heal_percent"] == consts.AUTO_HEAL_USUAL_PERCENT and "leading" not in usual["companion"]
    assert "auto-heal" not in text_for(usual) and "leading" not in text_for(usual)
    assert "auto-heal off" in text_for(read_game_state(build_state(auto_heal_percent=0)))
    assert "auto-heal below 60%" in text_for(read_game_state(build_state(auto_heal_percent=60)))
    leading_text = text_for(read_game_state(build_state(lead_state=1)))
    assert "leading the player" in leading_text and "catch up" not in leading_text
    assert "leading the player (waiting for the player to catch up)" in text_for(read_game_state(build_state(lead_state=2)))
    # A game mod from before says nothing about either
    old_state = build_state()
    old_state[game_state.KEY_COMPANION_VALUES] = old_state[game_state.KEY_COMPANION_VALUES][:game_state.COMPANION_VALUE_COUNT_WITHOUT_HEALING]
    assert "auto_heal_percent" not in read_game_state(old_state)["companion"]

    # What a merchant offers: wares with prices, the gold of both, and what the companion could sell
    merchant_state = read_game_state(build_state(inventory=consts.INVENTORY_MERCHANT, corpse_ref_id=MERCHANT_REF_ID))
    merchant = merchant_state["merchant"]
    assert (merchant["status"], merchant["gold"], merchant["own_gold"]) == (game_state.MERCHANT_TRADES, 750, 30)
    assert [(ware["id"], ware["count"], ware["price"]) for ware in merchant["wares"]] == [("00013989", 2, 85), ("0003EADD", 3, 56)]
    merchant_lines = text_for(merchant_state, merchant_wanted=MERCHANT_REF_ID).splitlines()
    start = merchant_lines.index(f"Whiterun Guard {MERCHANT_REF_ID} trades (from 8 to 20 o'clock). Gold: the merchant has 750, you have 30.")
    assert merchant_lines[start + 1:start + 6] == [
        "Wares, each with its ID and the price of one piece, for the command buy:",
        "- Weapons: Steel Sword 00013989 x2 85 gold", "- Potions and food: Potion of Minor Healing 0003EADD x3 56 gold",
        "What you could sell here (nothing you wear or hold), each with the price one piece brings, for the command sell:",
        "- Weapons: Hunting Bow 00013985 17 gold"]
    # Without the argument the wares are not shown, and somebody who is no merchant is told as such
    assert "Wares" not in text_for(merchant_state)
    no_merchant = read_game_state(build_state(inventory=consts.INVENTORY_MERCHANT, corpse_ref_id="FF000A01"))
    assert f"FF000A01 is no merchant." in text_for(no_merchant, merchant_wanted="FF000A01")
    assert "told nothing about a merchant" in text_for(usual, merchant_wanted=MERCHANT_REF_ID)

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received_commands: list[dict] = []
        states: list[dict] = [build_state()]

        def game():
            results: list[dict] = []
            for _ in range(9):
                body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: 0, consts.KEY_FAILS: 0}
                if len(states) > 0:
                    body[consts.KEY_STATE] = states.pop(0)
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                results = []
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    if sent[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                        states.append(build_state(inventory=str(sent.get(consts.KEY_COMMAND_DETAIL) or ""),
                                                  corpse_ref_id=str(sent.get(consts.KEY_COMMAND_TARGET) or "")))
                    else:
                        received_commands.append(sent)
                        results.append({consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # What the server turns down itself never reaches the game
        assert "needs a target" in command({"action": "lead"})["message"]
        assert "needs a target" in command({"action": "buy", "detail": "00013989"})["message"]
        assert "buy needs the items" in command({"action": "buy", "target": MERCHANT_REF_ID})["message"]
        assert "neither IDs of items nor how many" in command({"action": "sell", "target": MERCHANT_REF_ID, "detail": "two swords"})["message"]
        assert "how many" in command({"action": "buy", "target": MERCHANT_REF_ID, "detail": "00013989 0"})["message"]
        assert "auto_heal needs" in command({"action": "auto_heal", "detail": "sometimes"})["message"]
        assert "auto_heal needs" in command({"action": "auto_heal", "detail": "95"})["message"]
        assert "how many meters" in command({"action": "lead", "target": "000F6845", "detail": "far"})["message"]
        assert not received_commands
        # lead carries the distance to keep like move_to, and 'run'
        assert command({"action": "lead", "target": "000f6845", "detail": "3 m", "run": True})["status"] == consts.STATUS_DONE
        lead = received_commands[0]
        assert (lead[consts.KEY_COMMAND_ACTION], lead[consts.KEY_COMMAND_TARGET], lead[consts.KEY_COMMAND_COUNT], lead[consts.KEY_COMMAND_RUN]) == ("lead", "000F6845", 3, 1)
        # auto_heal: off, on, or the percent (which means on)
        command({"action": "auto_heal", "detail": "OFF"})
        command({"action": "auto_heal", "detail": "60%"})
        assert [(sent[consts.KEY_COMMAND_DETAIL], sent[consts.KEY_COMMAND_COUNT]) for sent in received_commands[1:3]] == [("off", 0), ("on", 60)]
        # buy and sell: the merchant as the target, the item in two halves, and how many (0 = all)
        command({"action": "buy", "target": MERCHANT_REF_ID, "detail": "0003eadd 2"})
        command({"action": "sell", "target": MERCHANT_REF_ID, "detail": "00013985, all"})
        command({"action": "buy", "target": MERCHANT_REF_ID, "detail": "00013989"})
        bought, sold, bought_one = received_commands[3:6]
        assert (bought[consts.KEY_COMMAND_TARGET], bought[consts.KEY_COMMAND_ITEM_HIGH], bought[consts.KEY_COMMAND_ITEM_LOW], bought[consts.KEY_COMMAND_COUNT]) == (MERCHANT_REF_ID, 0x0003, 0xEADD, 2)
        assert (sold[consts.KEY_COMMAND_ACTION], sold[consts.KEY_COMMAND_ITEM_LOW], sold[consts.KEY_COMMAND_COUNT]) == ("sell", 0x3985, consts.COUNT_ALL)
        assert bought_one[consts.KEY_COMMAND_COUNT] == 1
        # STATE with 'merchant' asks the game about that character and shows what it offers
        assert "is not a ref_id" in tool_payload(rpc(client, "tools/call", {"name": "STATE", "arguments": {"merchant": "the smith"}}))["message"]
        wares_text = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"merchant": MERCHANT_REF_ID}}))
        assert "Steel Sword 00013989 x2 85 gold" in wares_text and "Hunting Bow 00013985 17 gold" in wares_text


def test_the_log_tells_silence_and_what_became_of_commands():
    import asyncio
    import logging

    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.server_log import log, shorten

    assert shorten("a  b\n c") == "a b c"
    assert len(shorten("x" * 400)) == 300 and shorten("x" * 400).endswith("...")

    lines: list[str] = []

    class Keeper(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    async def scenario() -> None:
        # Without a grace period the game counts as silent right after its request
        queue = CompanionCommandQueue(hold_seconds=0.05, connection_grace_seconds=0.0, pause_wait_seconds=0.5)
        queue.SILENCE_LOG_SECONDS = 0.2
        await queue.send_command(consts.ACTION_FOLLOW)
        await queue.poll({"location": "Whiterun"}, [], busy=1, events=[{"text": "A fight began.", "urgent": True, "seconds_ago": 0}])
        await asyncio.sleep(0.3)

        async def come_back() -> None:
            commands = await queue.poll(None, [])
            await asyncio.sleep(0.3)
            await queue.poll(None, [{consts.KEY_COMMAND_ID: commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True,
                                     consts.KEY_RESULT_MESSAGE: "You follow the player."}], busy=0)
        game = asyncio.create_task(come_back())
        await queue.send_command(consts.ACTION_FOLLOW, target="000F6845")
        await game
        await asyncio.sleep(0.1)
        await queue.get_state()
        await queue.send_command(consts.ACTION_WAIT)

    keeper = Keeper()
    log.addHandler(keeper)
    try:
        asyncio.run(scenario())
    finally:
        log.removeHandler(keeper)

    def line_with(*words: str) -> str:
        found = [line for line in lines if all(word in line for word in words)]
        assert len(found) > 0, (words, lines)
        return found[0]

    line_with("COMMAND", "follow", "not sent, no game has asked")
    line_with("GAME first request")
    line_with("GAME sent its state (number 1)")
    line_with("GAME sent 1 events: A fight began.")
    line_with("GAME says the companion is busy.")
    # The game stood still after a request that ran out, then it worked on a command
    assert "nothing, its request had run out" in line_with("GAME back after", "of silence")
    line_with("COMMAND", "follow 000F6845", "the game has been silent for", "waiting for it")
    line_with("COMMAND", "follow 000F6845", "picked up by the game after")
    assert line_with("GAME back after", "Before that it was handed:", "follow 000F6845")
    line_with("GAME result of command", "ok: You follow the player.")
    line_with("GAME says the companion is not busy any more.")
    line_with("COMMAND", "follow 000F6845", "result after")
    line_with("STATE got no fresh state", "has been silent for")
    line_with("COMMAND", "wait", "NOT picked up after", "The game has been silent for")
    # A request of the game that brings nothing and gets nothing leaves no line
    assert not any("handed: nothing" in line and "0.0 s" in line for line in lines)

    # The calls of the AI: what was asked, how long it took and how the answer begins
    lines.clear()
    log.addHandler(keeper)
    try:
        with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
            rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "dance"}})
    finally:
        log.removeHandler(keeper)
    assert 'AI -> COMMAND {"action": "dance"}' in lines[0]
    assert "AI <- COMMAND after" in lines[-1] and "(ERROR)" in lines[-1] and "Unknown action" in lines[-1]


def test_a_game_at_work_counts_as_there_and_a_seat_is_not_picked_from_an_old_state():
    import asyncio

    from src.companion.command_queue import CompanionCommandQueue
    from src.companion.fake_game import SEAT_REF_ID, build_state
    from src.companion.game_state import read_game_state
    from src.companion.mcp_route import _find_nearest_seat
    from src.companion.state_text import describe_state

    async def scenario() -> dict:
        answers: dict = {}
        # Without a grace period the game counts as silent right after its request
        queue = CompanionCommandQueue(hold_seconds=1.0, connection_grace_seconds=0.0, pause_wait_seconds=0.5)

        async def slow_game(seconds: float, with_state: bool) -> None:
            # Asks, picks up what comes, then is at work for a while before it asks again
            await queue.poll(read_game_state(build_state()), [])
            await asyncio.sleep(seconds)
            await queue.poll(read_game_state(build_state(inventory=consts.INVENTORY_OWN)) if with_state else None, [])

        # The game takes the request for its state and needs longer than the grace period: the state is still fresh
        game = asyncio.create_task(slow_game(0.6, True))
        await asyncio.sleep(0.1)
        answers["slow"] = await queue.get_state(refresh_timeout_seconds=3.0)
        await game
        # It needs longer than the server waits: it counts as there and at work, not as paused or gone
        game = asyncio.create_task(slow_game(1.2, False))
        await asyncio.sleep(0.1)
        answers["too_slow"] = await queue.get_state(refresh_timeout_seconds=0.4)
        # No seat is picked from the state the server still has, and a command is told that the game is at work
        answers["seat"] = await _find_nearest_seat(queue)
        answers["command"] = await queue.send_command(consts.ACTION_FOLLOW)
        await game
        return answers

    answers = asyncio.run(scenario())
    assert answers["slow"]["fresh"] is True and answers["slow"]["connected"] is True
    assert answers["too_slow"]["fresh"] is False and answers["too_slow"]["connected"] is True
    assert describe_state(answers["too_slow"]).splitlines()[0].startswith("The game did not answer in time. What follows is")
    assert answers["seat"][0] == "" and "did not send the things around you in time" in answers["seat"][1]
    assert answers["command"]["status"] == consts.STATUS_NOT_DELIVERED and "has been busy for" in answers["command"]["message"]
    assert "paused" not in answers["command"]["message"]
    assert CompanionCommandQueue.STATE_TIMEOUT_SECONDS == 12.0 and SEAT_REF_ID != ""


def test_buy_and_sell_take_several_items_and_sell_junk():
    from src.companion import game_state
    from src.companion.fake_game import MERCHANT_REF_ID, build_state
    from src.companion.ref_ids import parse_ref_id, split_form_id

    def halves(item_id: str) -> list[int]:
        return list(split_form_id(parse_ref_id(item_id)))

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received_commands: list[dict] = []
        old_state = build_state()
        old_state[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PROTOCOL] = consts.TRADE_LIST_PROTOCOL - 1
        states: list[dict] = [old_state]

        def game():
            results: list[dict] = []
            for _ in range(12):
                body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: 0, consts.KEY_FAILS: 0}
                if len(states) > 0:
                    body[consts.KEY_STATE] = states.pop(0)
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                results = []
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    if sent[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                        states.append(build_state(inventory=str(sent.get(consts.KEY_COMMAND_DETAIL) or ""),
                                                  corpse_ref_id=str(sent.get(consts.KEY_COMMAND_TARGET) or "")))
                    else:
                        received_commands.append(sent)
                        results.append({consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # What the server turns down itself never reaches the game
        assert "is not made of IDs" in command({"action": "sell", "target": "the bow, 00013985"})["message"]
        # The items alone, as for give and take, are not enough before a merchant is known
        no_merchant = command({"action": "sell", "target": "00013985,0001397D"})
        assert "sell needs the merchant" in no_merchant["message"] and "'detail' the IDs of the items" in no_merchant["message"]
        assert "Only sell knows 'junk'" in command({"action": "buy", "target": MERCHANT_REF_ID, "detail": "junk"})["message"]
        # A game mod from before would only trade the first item, so the server does not send it the list
        too_old = command({"action": "sell", "target": "00013985,0001397D", "detail": MERCHANT_REF_ID})
        assert too_old["status"] == consts.STATUS_FAILED and "newer game mod" in too_old["message"]
        assert not received_commands
        # Looking at a merchant makes it the one that is meant when no merchant is named. Its state is from a newer game mod
        assert "Steel Sword" in tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"merchant": MERCHANT_REF_ID}}))
        # The usual form: the merchant as the target, the items in 'detail', each with how many behind it if not one
        assert command({"action": "buy", "target": MERCHANT_REF_ID, "detail": "00013989 2, 0003eadd"})["status"] == consts.STATUS_DONE
        bought = received_commands[0]
        assert bought[consts.KEY_COMMAND_TARGET] == MERCHANT_REF_ID
        assert bought[consts.KEY_COMMAND_ITEMS] == halves("00013989") + [2] + halves("0003EADD") + [1]
        # The items in 'target' as for give and take: the merchant is the one looked at last, 'detail' is how many
        command({"action": "sell", "target": "00013985,0001397D", "detail": "1,all"})
        sold = received_commands[1]
        assert (sold[consts.KEY_COMMAND_ACTION], sold[consts.KEY_COMMAND_TARGET]) == ("sell", MERCHANT_REF_ID)
        assert sold[consts.KEY_COMMAND_ITEMS] == halves("00013985") + [1] + halves("0001397D") + [0]
        # One item in 'target' and the merchant in 'detail' is read the right way round, and goes the way one item always went
        command({"action": "sell", "target": "00013985", "detail": MERCHANT_REF_ID})
        swapped = received_commands[2]
        assert consts.KEY_COMMAND_ITEMS not in swapped and swapped[consts.KEY_COMMAND_TARGET] == MERCHANT_REF_ID
        assert (swapped[consts.KEY_COMMAND_ITEM_LOW], swapped[consts.KEY_COMMAND_COUNT]) == (0x3985, 1)
        # junk: what the merchant buys and the companion neither wears, holds nor has as a favorite. In the simulator
        # that is the bow, all of it
        junk = command({"action": "sell", "target": MERCHANT_REF_ID, "detail": "JUNK"})
        assert junk["status"] == consts.STATUS_DONE
        assert (received_commands[3][consts.KEY_COMMAND_ITEM_LOW], received_commands[3][consts.KEY_COMMAND_COUNT]) == (0x3985, consts.COUNT_ALL)


def test_whom_the_companion_speaks_to_gets_a_look_that_fits_how_it_speaks():
    # Every look for the listener belongs to a manner of speaking and is a look the game has
    assert set(consts.LISTENER_FACES) <= set(consts.MANNERS)
    assert set(consts.LISTENER_FACES.values()) <= set(consts.EXPRESSIONS)
    assert consts.EXPRESSIONS[consts.LISTENER_FACES["angry"]] == 12 and consts.EXPRESSIONS[consts.LISTENER_FACES["joking"]] == 10
    # Without a voice the server cannot speak, but it does not break over a target and a manner either
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "say", "detail": "Hello.", "target": "000198AC", "manner": "angry"}}))
        assert answer["status"] == consts.STATUS_FAILED and "voice" in answer["message"]


def test_pick_up_takes_several_things_and_everything_around():
    from src.companion import game_state
    from src.companion.fake_game import ITEM_REF_ID, build_state, collect_around, pick_up_several
    from src.companion.ref_ids import parse_ref_id, split_form_id

    other_ref_id = "0005A411"

    def halves(ref_id: str) -> list[int]:
        return list(split_form_id(parse_ref_id(ref_id)))

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        received_commands: list[dict] = []
        old_state = build_state()
        old_state[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PROTOCOL] = consts.PICK_UP_LIST_PROTOCOL - 1
        states: list[dict] = [old_state]

        def game():
            results: list[dict] = []
            for _ in range(10):
                body = {consts.KEY_RESULTS: results, consts.KEY_BUSY: 0, consts.KEY_FAILS: 0}
                if len(states) > 0:
                    body[consts.KEY_STATE] = states.pop(0)
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                results = []
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    if sent[consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE:
                        states.append(build_state())
                    else:
                        received_commands.append(sent)
                        results.append({consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True,
                                        consts.KEY_RESULT_MESSAGE: "You set off."})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # A game mod from before this would only walk to the first thing, so the server does not send it the list
        too_old = command({"action": "pick_up", "target": f"{ITEM_REF_ID},{other_ref_id}"})
        assert too_old["status"] == consts.STATUS_FAILED and "newer game mod" in too_old["message"]
        assert "newer game mod" in command({"action": "pick_up", "detail": "all"})["message"]
        # Any state of a game mod that is new enough lifts that
        tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {}}))
        # What the server turns down itself never reaches the game
        assert "'plate' is not a ref_id" in command({"action": "pick_up", "target": f"{ITEM_REF_ID}, plate"})["message"]
        assert "at most 10" in command({"action": "pick_up", "detail": "all 50"})["message"]
        assert "only one kind" in command({"action": "pick_up", "detail": "all 2 shiny"})["message"]
        assert "You named 2" in command({"action": "pick_up", "target": f"{ITEM_REF_ID},{other_ref_id}", "detail": "all"})["message"]
        assert "needs a target" in command({"action": "pick_up"})["message"]
        assert not received_commands
        # Several things: their ref IDs in two halves each, in the order they were named, each one once
        several = command({"action": "pick_up", "target": f"{ITEM_REF_ID}, {other_ref_id.lower()} {ITEM_REF_ID}"})
        assert several == {"status": consts.STATUS_DONE, "message": "You set off."}
        assert received_commands[0][consts.KEY_COMMAND_ITEMS] == halves(ITEM_REF_ID) + halves(other_ref_id)
        assert (received_commands[0][consts.KEY_COMMAND_TARGET], received_commands[0][consts.KEY_COMMAND_DETAIL]) == (ITEM_REF_ID, f"{ITEM_REF_ID},{other_ref_id}")
        assert consts.KEY_COMMAND_PICK_ALL not in received_commands[0]
        # Everything around: the meters (2 if none are named) and the kind as the game mod knows it. No target = around the companion
        command({"action": "pick_up", "detail": "ALL"})
        sent = received_commands[1]
        assert (sent[consts.KEY_COMMAND_PICK_ALL], sent[consts.KEY_COMMAND_PICK_FILTER], sent[consts.KEY_COMMAND_TARGET]) == (consts.PICK_UP_USUAL_METERS, "", "")
        assert consts.KEY_COMMAND_ITEMS not in sent
        command({"action": "pick_up", "detail": "all 4m Potions", "target": ITEM_REF_ID})
        sent = received_commands[2]
        assert (sent[consts.KEY_COMMAND_PICK_ALL], sent[consts.KEY_COMMAND_PICK_FILTER], sent[consts.KEY_COMMAND_TARGET]) == (4, "potions", ITEM_REF_ID)
        assert sent[consts.KEY_COMMAND_DETAIL] == "all 4 potions"
        command({"action": "pick_up", "detail": "all 3 no clutter"})
        assert (received_commands[3][consts.KEY_COMMAND_PICK_ALL], received_commands[3][consts.KEY_COMMAND_PICK_FILTER]) == (3, consts.PICK_UP_NO_CLUTTER)
        # One thing goes the way it always went
        command({"action": "pick_up", "target": ITEM_REF_ID})
        assert consts.KEY_COMMAND_ITEMS not in received_commands[4] and consts.KEY_COMMAND_PICK_ALL not in received_commands[4]

    # With something to follow ('then') the command is checked before anything starts: 'all' needs no target there either
    from src.companion.mcp_route import _check_step
    assert _check_step({"action": "pick_up", "detail": "all 6 potions"}) == ""
    assert "needs a target" in _check_step({"action": "pick_up"}) and "needs a target" in _check_step({"action": "pick_up", "detail": "6"})
    # Every word the AI may write for a kind leads to a kind the game mod knows
    assert set(consts.PICK_UP_KIND_WORDS.values()) == set(consts.PICK_UP_KINDS) | {consts.PICK_UP_NO_CLUTTER}
    # The simulator answers like the game mod
    is_ok, message = pick_up_several(halves(ITEM_REF_ID) + halves("0000BEEF"))
    assert is_ok and message.startswith("You walk to Iron Dagger to pick it up.") and message.endswith(" 1 of your list you leave out.")
    assert pick_up_several(halves("0000BEEF"))[0] is False
    assert collect_around(2, "weapons")[0] is True and collect_around(2, consts.PICK_UP_NO_CLUTTER)[0] is True
    assert collect_around(2, "potions") == (False, "You took nothing: there is nothing loose of the kind 'potions' within 2 meters around you that is free to take.")
    assert collect_around(1, "")[0] is False


def test_the_log_names_the_menus_that_were_open():
    import logging

    from src.companion.command_queue import describe_menu
    from src.companion.game_state import read_menus
    from src.companion.server_log import log

    # As the plugin of the game mod writes them: the name, a tab and the milliseconds. What is not readable is left out
    assert read_menus(["BarterMenu\t34200", "Console\t800", "broken", "NoTime\tsoon", 7]) == [("BarterMenu", 34.2), ("Console", 0.8)]
    assert read_menus(None) == [] and read_menus("BarterMenu\t1") == []
    assert describe_menu("BarterMenu", 34.2) == "BarterMenu 34 s" and describe_menu("Console", 0.8) == "Console 0.8 s"

    lines: list[str] = []

    class Keeper(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    keeper = Keeper()
    log.addHandler(keeper)
    try:
        with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_MENUS: ["BarterMenu\t34200", "Console\t800"]}).json()
            assert reply[consts.KEY_REPLY_TYPE] == consts.REPLY_TYPE_IDLE
            # A request without menus, or from an older game mod, leaves no such line
            client.post(consts.ROUTE_GAME, json={})
    finally:
        log.removeHandler(keeper)
    assert [line for line in lines if "menus were open" in line] == ["GAME says these menus were open: BarterMenu 34 s, Console 0.8 s"]

    # Where the plugin of the game mod and its script tell different things, the game mod says so, and that is logged
    lines.clear()
    log.addHandler(keeper)
    try:
        with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
            client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun", "plugin_mismatch": "companion runs: plugin 1, script 0"}})
            client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}})
    finally:
        log.removeHandler(keeper)
    assert [line for line in lines if "disagree" in line] == ["GAME says its plugin and its script disagree: companion runs: plugin 1, script 0"]


def test_a_state_built_in_one_go_unpacks_to_the_state_it_always_was():
    from src.companion import game_state
    from src.companion.fake_game import HORSE_REF_ID, PLAYER_HORSE_REF_ID, build_state, pack_quick_state
    from src.companion.game_state import KEY_QUICK_NUMBERS, KEY_QUICK_TEXTS, expand_quick_state, read_game_state

    variants = [
        build_state(),
        build_state(has_heard_lines=True, has_traps=True, has_events=True, report="You picked up Iron Dagger.", is_waiting=True),
        build_state(horse_ref_id=HORSE_REF_ID, is_riding=True, is_ride_locked=True, is_player_riding=True, player_horse_ref_id=PLAYER_HORSE_REF_ID,
                    called_horse_ref_id=PLAYER_HORSE_REF_ID, horse_names={HORSE_REF_ID: "Klumpatsch"}),
        build_state(inventory=consts.INVENTORY_BOTH, has_stats=True, find="Warmaiden", is_sitting=True, expression="happy", is_around_companion=True,
                    target_ref_id="000F6845", lead_state=1, is_sneaking=True),
    ]
    for raw in variants:
        packed = pack_quick_state(raw)
        # What goes along with every state is in the two lists, and only there
        assert KEY_QUICK_NUMBERS in packed and KEY_QUICK_TEXTS in packed
        for key in (game_state.KEY_LOCATION, game_state.KEY_PLAYER_VALUES, game_state.KEY_WORLD_VALUES, game_state.KEY_COMPANION_VALUES,
                    game_state.KEY_QUESTS, game_state.KEY_NEARBY_VALUES, game_state.KEY_NEARBY_NAMES, game_state.KEY_HANDS, game_state.KEY_TIMES):
            assert key in raw and key not in packed
        unpacked = read_game_state(packed)
        assert unpacked.pop("quick") is True
        assert unpacked == read_game_state(raw)
    # A state that is not packed stays as it is and carries no mark
    assert expand_quick_state({"location": "Riften"}) == {"location": "Riften"}
    assert "quick" not in read_game_state(build_state())

    # A list of numbers that is cut off: what came whole before the cut counts, the rest is left out
    packed = pack_quick_state(build_state())
    cut = dict(packed)
    cut[KEY_QUICK_NUMBERS] = packed[KEY_QUICK_NUMBERS][:24]
    cut_state = read_game_state(cut)
    assert cut_state["location"] == "Whiterun, Plains District" and cut_state["player"]["health_percent"] == read_game_state(packed)["player"]["health_percent"]
    assert "companion" not in cut_state
    # Whatever arrives, nothing breaks, and the texts still count
    for broken in ([], [99], [5, 0, 0, 0, 0, 2, 500, 1], [5, 0, 0, 0, 0, 2, -3], ["x"], None, "numbers"):
        state = read_game_state({KEY_QUICK_NUMBERS: broken, KEY_QUICK_TEXTS: ["location\tWhiterun", "no tab", 7, "\tno key"]})
        assert state["location"] == "Whiterun" and state["quick"] is True
    assert read_game_state({KEY_QUICK_NUMBERS: [1], KEY_QUICK_TEXTS: "location\tWhiterun"}) is not None
    # What the game mod sends as a single entry next to the lists counts as it is
    assert read_game_state({KEY_QUICK_TEXTS: ["location\tWhiterun"], "location": "Riften"})["location"] == "Riften"
    # A text keeps the tabs behind its first one: the lines of the quests are split by tabs themselves
    assert expand_quick_state({KEY_QUICK_TEXTS: ["quests\ta\tb", "quests\tc"]})["quests"] == ["a\tb", "c"]


def test_state_asks_with_one_list_and_compare_tells_where_the_two_ways_differ():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state
    from src.companion.mcp_route import _list_differences

    assert _list_differences({"a": 1, "quick": True, "build_ms": 3, "b": {"c": [1, 2]}, "e": [1]}, {"a": 1, "build_ms": 9, "b": {"c": [1, 3]}, "d": "x", "e": [1, 2]}) == [
        "b.c[1]: in one go 2 / the old way 3", 'd: only the old way has it: "x"', "e: in one go [1] / the old way [1, 2]"]

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        def state(arguments: dict):
            return rpc(client, "tools/call", {"name": "STATE", "arguments": arguments})

        received_commands: list[dict] = []
        # What the game mod tells the old way, and whether its plugin can build a state in one go at all
        slow_location = ["Whiterun, Plains District"]
        can_build_quick = [True]

        def game():
            body: dict = {consts.KEY_STATE: pack_quick_state(build_state())}
            for _ in range(12):
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                body = {}
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    received_commands.append(sent)
                    raw = build_state()
                    if sent[consts.KEY_COMMAND_STATE_OPTIONS][consts.STATE_OPTION_SLOW] or not can_build_quick[0]:
                        raw[game_state.KEY_LOCATION] = slow_location[0]
                        body = {consts.KEY_STATE: raw}
                    else:
                        body = {consts.KEY_STATE: pack_quick_state(raw)}

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        # What the state should carry goes to the game as one list of numbers, and as single entries for older game mods
        assert "Whiterun" in tool_text(state({"things": True, "max": 7, "find": "Warmaiden"}))
        options = received_commands[0][consts.KEY_COMMAND_STATE_OPTIONS]
        assert len(options) == consts.STATE_OPTION_COUNT
        assert options[:6] == [7, 1, 0, 0, 0, 0] and options[consts.STATE_OPTION_SLOW] == 0
        # No inventory, a text to find, no filter, no book
        assert options[17:21] == [0, 1, 0, 0]
        # No picture of what lies in front of the companion
        assert options[consts.STATE_OPTION_VIEW_COLUMNS:] == [0, 0]
        assert received_commands[0][consts.KEY_COMMAND_THINGS] == 1 and received_commands[0][consts.KEY_COMMAND_FIND] == "Warmaiden"
        # compare: once in one go, once the old way
        same = state({"compare": True})
        assert tool_text(same).startswith("STATE compare: in one go it took the script") and tool_text(same).endswith("Both ways tell the same.")
        assert same.json()["result"]["isError"] is False
        assert [sent[consts.KEY_COMMAND_STATE_OPTIONS][consts.STATE_OPTION_SLOW] for sent in received_commands[1:3]] == [0, 1]
        slow_location[0] = "Riften"
        differing = tool_text(state({"compare": True}))
        assert "1 differences" in differing and '- location: in one go "Whiterun, Plains District" / the old way "Riften"' in differing
        # A game mod whose plugin cannot build the state in one go: there is nothing to compare
        can_build_quick[0] = False
        old_way = state({"compare": True})
        assert "built its state the old way" in tool_text(old_way) and old_way.json()["result"]["isError"] is True


def test_visible_from_the_companion_tells_who_is_close_and_who_is_heard():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state
    from src.companion.game_state import read_game_state

    def state_with(senses, plugin_version: int = game_state.PLUGIN_VERSION_WITH_SENSES) -> dict:
        raw = build_state(is_visible_only=True, is_around_companion=True)
        raw[game_state.KEY_WORLD_VALUES][game_state.WORLD_PLACE_OF_PLUGIN_VERSION] = plugin_version
        raw[game_state.KEY_TIMES] = [412, 7]
        if senses is not None:
            raw[game_state.KEY_NEARBY_SENSES] = senses
        return raw

    count = len(build_state()[game_state.KEY_NEARBY_VALUES]) // game_state.VALUES_PER_NEARBY_CHARACTER
    assert count >= 3
    senses = [1, 2, 3] + [1] * (count - 3)
    packed = pack_quick_state(state_with(senses))
    # The senses travel as a block of their own
    assert game_state.KEY_NEARBY_SENSES not in packed
    state = read_game_state(packed)
    by_ref_id = {character["ref_id"]: character.get("sense") for character in state["nearby"]}
    values = build_state()[game_state.KEY_NEARBY_VALUES]
    for place, sense in enumerate(senses):
        ref_id = f"{values[place * game_state.VALUES_PER_NEARBY_CHARACTER] & 0xFFFFFFFF:08X}"
        assert by_ref_id[ref_id] == game_state.SENSE_NAMES[sense]
    # The plugin tells its own time in tenths of a millisecond from this version on, an older one in whole ones
    assert (state["build_ms"], state["scan_ms"]) == (412, 0.7)
    assert read_game_state(pack_quick_state(state_with(senses, game_state.PLUGIN_VERSION_WITH_SENSES - 1)))["scan_ms"] == 7
    assert read_game_state(state_with(senses))["scan_ms"] == 7
    # A list that does not fit the characters, or none: nobody carries a sense
    for other in ([1], senses + [1], ["x"] * count, None):
        assert all("sense" not in character for character in read_game_state(pack_quick_state(state_with(other)))["nearby"])
    # A number that names no sense is left out for that character alone
    assert [character.get("sense") for character in read_game_state(state_with([9] + [2] * (count - 1)))["nearby"]].count(None) == 1

    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            body: dict = {consts.KEY_STATE: pack_quick_state(build_state())}
            for _ in range(4):
                reply = client.post(consts.ROUTE_GAME, json=body).json()
                body = {}
                for sent in reply.get(consts.KEY_COMMANDS, []):
                    received_commands.append(sent)
                    body = {consts.KEY_STATE: pack_quick_state(state_with(senses))}

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        text = tool_text(rpc(client, "tools/call", {"name": "STATE", "arguments": {"around": "you", "visible": True}}))
        # Around the companion, and only what it notices
        options = received_commands[0][consts.KEY_COMMAND_STATE_OPTIONS]
        assert (options[2], options[4]) == (1, 1)
        assert "'close' = within about 2 meters of you" in text
        lines = text.split("\n")
        assert len([line for line in lines if line.endswith(", close")]) == 1
        assert len([line for line in lines if line.endswith(", heard (not seen)")]) == 1


def test_passive_holds_the_companion_back_and_the_state_tells_it():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state, run_command
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "passive needs 'on', 'full' or 'off'" in command({"action": "passive"})["message"]
        assert "passive needs 'on', 'full' or 'off'" in command({"action": "passive", "detail": "maybe"})["message"]
        # What is fine is refused only because no game is there
        assert command({"action": "passive", "detail": "ON"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        assert command({"action": "passive", "detail": "Full"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "passive" in tools[0]["inputSchema"]["properties"]["action"]["enum"]
    # Holding back leaves waiting commands alone, like sneak does
    assert consts.ACTION_PASSIVE in consts.ACTIONS_KEEPING_QUEUE
    assert run_command(consts.ACTION_PASSIVE, "", "", "", consts.PASSIVE_ON)[1].startswith("You hold back now")
    assert run_command(consts.ACTION_PASSIVE, "", "", "", consts.PASSIVE_OFF)[1] == "You attack enemies of your own accord again."

    # The state tells it with an entry of its own, built the old way or in one go
    raw = build_state()
    assert "passive" not in read_game_state(raw)["companion"]
    raw[game_state.KEY_COMPANION_PASSIVE] = 1
    for sent in (raw, pack_quick_state(raw)):
        state = read_game_state(sent)
        assert state["companion"]["passive"] is True
        you_line = [line for line in describe_state({"connected": True, "fresh": True, "state": state}).split("\n") if line.startswith("You, ")][0]
        assert ", passive on" in you_line
    # passive with 'full': the companion does not fight at all, and the state tells that apart
    assert consts.PASSIVE_FULL_PROTOCOL <= game_state.GAME_PROTOCOL
    assert run_command(consts.ACTION_PASSIVE, "", "", "", consts.PASSIVE_FULL)[1].startswith("You do not fight at all now")
    raw[game_state.KEY_COMPANION_PASSIVE] = 2
    for sent in (raw, pack_quick_state(raw)):
        state = read_game_state(sent)
        assert state["companion"]["passive"] is True and state["companion"]["no_fight"] is True
        assert ", passive full (you do not fight at all)" in describe_state({"connected": True, "fresh": True, "state": state})
    from src.companion.mcp_route import describe_help
    assert "full = you do not fight at all" in describe_help("passive")[0]


def test_attack_with_hold_keeps_the_companion_at_its_spot_and_the_state_tells_it():
    from src.companion import game_state
    from src.companion.fake_game import NEARBY, build_state, pack_quick_state, run_command
    from src.companion.game_state import read_game_state
    from src.companion.state_text import describe_state

    enemy = NEARBY[0]["ref_id"]
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        # Anything else than 'hold' in 'detail' is told, so that a wish the game cannot grant does not get lost
        refused = command({"action": "attack", "target": enemy, "detail": "with the bow"})
        assert refused["status"] == consts.STATUS_FAILED and "For attack 'detail' is 'hold'" in refused["message"]
        # What is fine is refused only because no game is there
        assert command({"action": "attack", "target": enemy, "detail": "HOLD"})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        assert command({"action": "attack", "target": enemy})["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "'hold'" in manual_text() and "holding position" in manual_text()
    assert consts.ATTACK_HOLD_PROTOCOL <= game_state.GAME_PROTOCOL
    assert run_command(consts.ACTION_ATTACK, enemy, "")[1] == f"You attack {NEARBY[0]['name']} now."
    assert run_command(consts.ACTION_ATTACK, enemy, "", "", consts.ATTACK_HOLD)[1].startswith("You stay where you stand and attack ")

    # The state tells it with an entry of its own, built the old way or in one go
    raw = build_state()
    assert "holding_position" not in read_game_state(raw)["companion"]
    raw[game_state.KEY_COMPANION_HOLDING] = 1
    for sent in (raw, pack_quick_state(raw)):
        state = read_game_state(sent)
        assert state["companion"]["holding_position"] is True
        you_line = [line for line in describe_state({"connected": True, "fresh": True, "state": state}).split("\n") if line.startswith("You, ")][0]
        assert ", holding position" in you_line


def test_cover_needs_no_target_and_the_state_tells_it():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state, run_command
    from src.companion.game_state import read_game_state
    from src.companion.mcp_route import _check_step
    from src.companion.state_text import describe_state

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        # What is fine is refused only because no game is there
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "cover"}}))
        assert answer["status"] == consts.STATUS_GAME_NOT_CONNECTED
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "cover" in tools[0]["inputSchema"]["properties"]["action"]["enum"]
        assert "cover = take cover" in manual_text() and "'in cover'" in manual_text()
    assert _check_step({"action": "cover"}) == ""
    # It sends the companion somewhere, so commands that wait are dropped like for move_to
    assert consts.ACTION_COVER not in consts.ACTIONS_KEEPING_QUEUE and consts.ACTION_COVER not in consts.ACTIONS_REQUIRING_TARGET
    assert consts.COVER_PROTOCOL <= game_state.GAME_PROTOCOL
    assert run_command(consts.ACTION_COVER, "", "")[1].startswith("You run to cover")

    # The state tells it instead of 'holding position', built the old way or in one go
    raw = build_state()
    assert "in_cover" not in read_game_state(raw)["companion"]
    raw[game_state.KEY_COMPANION_HOLDING] = 1
    raw[game_state.KEY_COMPANION_COVERING] = 1
    for sent in (raw, pack_quick_state(raw)):
        state = read_game_state(sent)
        assert state["companion"]["in_cover"] is True
        you_line = [line for line in describe_state({"connected": True, "fresh": True, "state": state}).split("\n") if line.startswith("You, ")][0]
        assert ", in cover" in you_line and "holding position" not in you_line


def test_help_hands_out_the_manual_and_the_descriptions_stay_short():
    from src.companion import mcp_route
    from src.companion.mcp_route import COMMAND_MANUAL, STATE_MANUAL, describe_help, help_topics

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def ask(arguments: dict) -> dict:
            return rpc(client, "tools/call", {"name": "HELP", "arguments": arguments}).json()["result"]

        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["COMMAND", "STATE", "HELP", "WAIT_FOR_EVENT"]
        # An AI is handed all of this with every request, so it must not grow back to what it was (about 37,000 characters)
        assert sum(len(json.dumps(tool)) for tool in tools) < 16000
        # Without a topic: the list of topics. It needs no game
        listed = ask({})
        assert not listed.get("isError") and "Topics about COMMAND: follow, wait, attack" in listed["content"][0]["text"]
        attack = ask({"topic": "attack"})["content"][0]["text"]
        assert attack.startswith("attack = attack the target") and "'hold'" in attack and "cover = " not in attack
        # Several topics in the order of the manual, each piece once, whatever the spelling
        both = ask({"topic": "Move To, follow"})["content"][0]["text"]
        assert both.index("follow = ") < both.index("move_to = ") and both.count("run = true") == 1
        unknown = ask({"topic": "teleport"})
        assert unknown["isError"] is True and "no topic 'teleport'" in unknown["content"][0]["text"]
        half = ask({"topic": "teleport, wait"})
        assert not half.get("isError") and half["content"][0]["text"].endswith("wait = stay here.")
        assert ask({"topic": "a,b,c,d,e,f,g"})["isError"] is True
    # Every command is in the list of the description and has its piece of the manual, and so has every argument of STATE
    topics = help_topics(COMMAND_MANUAL) + help_topics(STATE_MANUAL)
    for action in consts.PLAYER_ACTIONS:
        assert action in topics and action in mcp_route.COMMAND_DESCRIPTION, action
    for argument in mcp_route.TOOLS[1]["inputSchema"]["properties"]:
        assert argument in topics or argument in (mcp_route.ARGUMENT_DEBUG, mcp_route.ARGUMENT_COMPARE), argument
    # The note about the language of the voice is swapped in the manual as it is in the description
    assert "Write it in German" in describe_help("say", "Write it in German. ")[0]
    assert "Write it in English" in describe_help("say")[0]


def test_view_turns_the_numbers_of_the_plugin_into_a_picture_of_text():
    from src.companion import game_state
    from src.companion.fake_game import build_state
    from src.companion.game_state import read_view
    from src.companion.mcp_route import describe_help
    from src.companion.state_text import describe_view

    # 4 columns, 3 rows: sky on top, a wall with the player in front of it and a chest, then ground with a drop on the right
    nothing = [0, 0, 0, 0, 0, 0, 0]
    legend = [1, 0x0000, 0x0014, game_state.VIEW_CREATURE_PLAYER, 1, 0x0004, 0xA2B1, game_state.VIEW_CREATURE_HOSTILE, 2, 0xFF00, 0x0EB0, 2]
    cells = (nothing + nothing + nothing + nothing
             + [2, 12, 0, 1, 6, 2, 9] + [2, 12, 0, 2, 9, 0, 0] + [2, 5, 0, 3, 5, 0, 0] + [3, 2, 0, 0, 0, 0, 0]
             + [1, 2, 0, 0, 0, 0, 0] + [1, 2, 0, 0, 0, 0, 0] + [1, 2, 0, 0, 0, 0, 0] + [0, 0, 1, 0, 0, 0, 0])
    numbers = [1, 4, 3, 120, 80, 3, 4, 7] + legend + cells
    view = read_view(numbers, ["Koi", "Frost Troll", "Chest"])
    assert view is not None and [entry["label"] for entry in view["legend"]] == ["C1", "C2", "O1"]
    assert view["legend"][2]["ref_id"] == "FF000EB0" and view["legend"][2]["kind"] == "container"
    lines = describe_view(view)
    assert lines[0].startswith("View from you (120 degrees wide, 4x3)")
    # Nothing stays empty, ground is a bare number, several in one cell stand in brackets with the nearest first,
    # and a thing right at the wall it stands at hides that wall
    assert lines[1:4] == [",,,", "(C1:6,C2:9,W:12),(C2:9,W:12),O1:5,R:2", "2,2,2,"]
    assert lines[4:8] == ["Legend:", "C1 Koi 00000014, the player", "C2 Frost Troll 0004A2B1, hostile", "O1 Chest FF000EB0, container"]
    assert lines[8] == "In words: The ground drops away to your right right at your feet. Straight ahead a wall about 5 m away."
    # A character comes first in the numbers of a cell so that things cannot push it out, but the nearest is shown first.
    # What stands in no cell is not in the legend, and the numbers of the labels have no gaps
    crowded = [1, 2, 1, 120, 80, 3, 4, 7] + [2, 0, 1, 4, 1, 0, 2, 0, 2, 0, 3, 4] + [0, 0, 0, 2, 4, 3, 3] + nothing
    crowded_view = read_view(crowded, ["Cheese", "Orgnar", "Tankard"])
    assert [entry["label"] + " " + entry["name"] for entry in crowded_view["legend"]] == ["C1 Orgnar", "O1 Tankard"]
    assert describe_view(crowded_view)[1] == "(O1:3,C1:4),"
    # Water: whichever way the plugin found it, it is an A with the meters to its surface, and the sentence names it
    wet = list(numbers)
    first_cell = 8 + len(legend)
    wet[first_cell + 8 * 7: first_cell + 8 * 7 + 3] = [5, 3, 0]
    wet[first_cell + 9 * 7: first_cell + 9 * 7 + 3] = [6, 4, 0]
    wet_view = read_view(wet, ["Koi", "Frost Troll", "Chest"])
    assert wet_view["cells"][2][0]["water_by"] == "height" and wet_view["cells"][2][1]["water_by"] == "both"
    wet_lines = describe_view(wet_view)
    assert wet_lines[3] == "A:3,A:4,2," and "A = water" in wet_lines[0]
    assert wet_lines[8].endswith("Water to your left about 3 m away, ahead about 4 m away. Straight ahead a wall about 5 m away.")
    # What cannot be read is no picture, and the text says so
    assert read_view([2, 4, 3], []) is None and read_view(numbers[:-1], []) is None and read_view(None, None) is None
    assert describe_view(None)[0].startswith("View: the game sent none.")

    assert "view" not in game_state.read_game_state(build_state())
    # In the state the picture only shows when it was asked for, built the old way or in one go
    from src.companion.fake_game import pack_quick_state
    from src.companion.state_text import describe_state
    raw = build_state()
    raw[game_state.KEY_VIEW] = numbers
    raw[game_state.KEY_VIEW_NAMES] = ["Koi", "Frost Troll", "Chest"]
    for sent in (raw, pack_quick_state(raw)):
        shown = game_state.read_game_state(sent)
        assert shown["view"]["columns"] == 4
        assert "View from you" not in describe_state({"connected": True, "fresh": True, "state": shown})
        asked = describe_state({"connected": True, "fresh": True, "state": shown, "view_wanted": True})
        assert "View from you (120 degrees wide, 4x3)" in asked and "O1 Chest FF000EB0, container" in asked
    assert "did not send the view in time" in describe_state({"connected": True, "fresh": False, "state": game_state.read_game_state(build_state()), "view_missed": True, "age_seconds": 3})
    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def state(arguments: dict) -> dict:
            return rpc(client, "tools/call", {"name": "STATE", "arguments": arguments}).json()["result"]

        # A size that makes no sense is told without asking the game
        assert state({"view_size": "200x3"})["isError"] is True and state({"view_size": "wide"})["isError"] is True
    assert "With 'view' you get a coarse picture" in describe_help("view")[0] and describe_help("view_size")[1] is False
    assert consts.VIEW_PROTOCOL <= game_state.GAME_PROTOCOL


def test_sight_tells_how_far_the_companion_sees_and_what_shortens_it():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state
    from src.companion.game_state import read_game_state, read_sight
    from src.companion.mcp_route import describe_help
    from src.companion.state_text import describe_sight, describe_state

    # Outdoors: fog at two in the night, a quarter of the 50 meters by day
    foggy = read_sight([13, 4, 25, 0, 0, 0, 37, 120])
    assert foggy == {"meters": 13, "indoors": False, "time_percent": 25, "torch": False, "minutes": 120, "weather": "fog"}
    assert describe_sight(foggy) == "Sight from you: about 13 m (fog, night)."
    assert describe_sight(read_sight([200, 0, 100, 0, 0, 0, 80, 720])) == "Sight from you: about 200 m."
    assert describe_sight(read_sight([150, 1, 75, 0, 0, 0, 60, 1170])) == "Sight from you: about 150 m (dusk)."
    assert describe_sight(read_sight([63, 2, 63, 0, 0, 0, 40, 330])) == "Sight from you: about 63 m (rain, dawn)."
    # Indoors the light counts, and its level goes along so that the bounds can be set after a look in the game
    assert describe_sight(read_sight([15, -1, 100, 1, 3, 0, 0, 720])) == "Sight from you: about 15 m (a dark place)."
    assert describe_sight(read_sight([25, -1, 100, 1, 3, 1, 0, 720])) == "Sight from you: about 25 m (a dark place, by torchlight)."
    assert describe_sight(read_sight([50, -1, 100, 1, 1, 0, 0, 720])) == "Sight from you: about 50 m."
    assert read_sight([0, 0, 100, 0, 0, 0, 0, 0]) is None and read_sight([5, 0]) is None and read_sight(None) is None
    # The plugin tells fog and snowstorm apart from clouds and snow
    assert game_state.WEATHER_NAMES[4] == "fog" and game_state.WEATHER_NAMES[5] == "dense fog" and game_state.WEATHER_NAMES[6] == "snowstorm"

    # In the state the line only comes with what is about seeing, built the old way or in one go
    raw = build_state()
    assert "sight" not in read_game_state(raw)
    raw[game_state.KEY_SIGHT] = [13, 4, 25, 0, 0, 0, 37, 120]
    for sent in (raw, pack_quick_state(raw)):
        shown = read_game_state(sent)
        assert shown["sight"]["meters"] == 13
        assert "Sight from you" not in describe_state({"connected": True, "fresh": True, "state": shown})
        assert "Sight from you: about 13 m (fog, night)." in describe_state({"connected": True, "fresh": True, "state": shown, "view_wanted": True})
    assert "Sight: how far your character sees" in describe_help("sight")[0] and "Sight: how far" in describe_help("visible")[0]
    assert consts.VIEW_PROTOCOL <= game_state.GAME_PROTOCOL


def test_heights_tell_who_stands_above_or_below_the_viewer():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state
    from src.companion.game_state import read_game_state, read_heights
    from src.companion.mcp_route import describe_help
    from src.companion.state_text import _describe_position, describe_state

    assert read_heights([0x0001B743, 8, 0x0001B744, -4]) == {"0001B743": 8, "0001B744": -4}
    assert read_heights([0x0001B743]) == {} and read_heights(None) == {}
    # Measured from the companion there is only that view, measured from the player the height belongs to the player
    guard = {"distance_meters": 15, "angle_degrees": 0, "height_meters": 8}
    assert _describe_position(guard, True) == "from you 15 m ahead, 8 m above"
    assert _describe_position({"distance_meters": 6, "angle_degrees": 90, "height_meters": -4}, True) == "from you 6 m right, 4 m below"
    assert _describe_position({"distance_meters": 6, "angle_degrees": 90}, True) == "from you 6 m right"
    assert _describe_position(guard, False).startswith("15 m ahead, 8 m above")

    # In the state: the character the game told a height for carries it, the others do not, built the old way or in one go
    raw = build_state()
    nearby = read_game_state(raw)["nearby"]
    assert len(nearby) >= 2 and all("height_meters" not in character for character in nearby)
    raw[game_state.KEY_HEIGHTS] = [int(nearby[0]["ref_id"], 16), 8]
    for sent in (raw, pack_quick_state(raw)):
        shown = read_game_state(sent)
        by_id = {character["ref_id"]: character for character in shown["nearby"]}
        assert by_id[nearby[0]["ref_id"]]["height_meters"] == 8
        assert "height_meters" not in by_id[nearby[1]["ref_id"]]
        text = describe_state({"connected": True, "fresh": True, "state": shown})
        assert text.count(", 8 m above") == 1 and "m below" not in text
    assert "8 m above" in describe_help("directions")[0]


def test_shoot_is_a_command_with_a_living_target():
    from src.companion import game_state
    from src.companion.fake_game import run_command
    from src.companion.mcp_route import COMMAND_DESCRIPTION, describe_help

    assert consts.ACTION_SHOOT in consts.PLAYER_ACTIONS and consts.ACTION_SHOOT in consts.ACTIONS_REQUIRING_TARGET
    # One arrow changes nothing about where the companion walks, so what waits in a chain stays
    assert consts.ACTION_SHOOT in consts.ACTIONS_KEEPING_QUEUE and consts.SHOOT_PROTOCOL <= game_state.GAME_PROTOCOL
    assert "shoot = one arrow at the target" in COMMAND_DESCRIPTION
    assert "shoot = shoot one arrow at the target" in describe_help("shoot")[0] and "also from the saddle" not in describe_help("nonsense")[0]
    # The fake game answers like the game mod
    is_ok, text, _ = run_command(consts.ACTION_SHOOT, "FF000A01", "")
    assert is_ok is True and text.startswith("You draw your bow and shoot one arrow at Bandit")
    assert "is already dead" in run_command(consts.ACTION_SHOOT, "FF000A03", "")[1]
    assert run_command(consts.ACTION_SHOOT, "00000001", "")[0] is False

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "shoot" in tools[0]["inputSchema"]["properties"]["action"]["enum"]
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "shoot"}}))
        assert answer["status"] == consts.STATUS_FAILED and "needs a target" in answer["message"]
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "shoot", "target": "FF000A01"}}))
        assert answer["status"] == consts.STATUS_GAME_NOT_CONNECTED


def test_sleep_lays_the_companion_down_in_a_bed():
    from src.companion import game_state
    from src.companion.fake_game import BED_REF_ID, SEAT_REF_ID, run_command
    from src.companion.game_state import _read_things
    from src.companion.mcp_route import describe_help
    from src.companion.state_text import _describe_thing

    assert consts.ACTION_SLEEP in consts.PLAYER_ACTIONS and consts.ACTION_SLEEP in consts.ACTIONS_REQUIRING_TARGET
    assert consts.ACTION_SLEEP not in consts.ACTIONS_KEEPING_QUEUE and consts.SLEEP_PROTOCOL <= game_state.GAME_PROTOCOL
    # The plugin tells which furniture is a bed
    bed, bench, table = _read_things(["0\t00093BDC\t3\t10\t2\tBed", "0\t0005A3F1\t3\t-90\t1\tWooden Bench", "0\t0005A3F2\t6\t120\t0\tTable"])
    assert bed["bed"] is True and bed["seat"] is False and bench["seat"] is True and "bed" not in bench
    assert table["seat"] is False and "bed" not in table
    assert _describe_thing(bed).startswith("00093BDC Bed, bed, ") and ", seat, " in _describe_thing(bench) and ", furniture, " in _describe_thing(table)
    # The fake game answers like the game mod
    assert run_command(consts.ACTION_SLEEP, BED_REF_ID, "")[0] is True
    assert "is no bed" in run_command(consts.ACTION_SLEEP, SEAT_REF_ID, "")[1]
    assert "sleep = walk to the bed" in describe_help("sleep")[0]

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "sleep"}}))
        assert answer["status"] == consts.STATUS_FAILED and "needs a target" in answer["message"]
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "sleep", "target": BED_REF_ID}}))
        assert answer["status"] == consts.STATUS_GAME_NOT_CONNECTED


def test_hands_of_a_companion_that_fights_with_a_bow_are_shown_for_testing():
    from src.companion import game_state
    from src.companion.fake_game import build_state, pack_quick_state
    from src.companion.game_state import read_game_state
    from src.companion.mcp_route import describe_help
    from src.companion.state_text import describe_state

    raw = build_state()
    assert "Hands (test)" not in describe_state({"connected": True, "fresh": True, "state": read_game_state(raw)})
    raw[game_state.KEY_COMPANION_HANDS] = [7, 7, 0, 0, 1]
    for sent in (raw, pack_quick_state(raw)):
        shown = read_game_state(sent)
        assert shown["companion"]["hands"] == {"equipped_right": 7, "equipped_left": 7, "shown_right": 0, "shown_left": 0, "drawn": True}
        text = describe_state({"connected": True, "fresh": True, "state": shown})
        assert "Hands (test): the game has 7 right and 7 left" in text and "the animation shows 0 right and 0 left, weapon drawn." in text
    # A list of another length is left out
    raw[game_state.KEY_COMPANION_HANDS] = [7, 7]
    assert "hands" not in read_game_state(raw)["companion"]
    assert "in hand anew" in describe_help("equip")[0] and "arrows or bolts" in describe_help("favorite")[0]


def test_drop_lets_items_of_the_companion_fall_to_the_ground():
    from src.companion import game_state
    from src.companion.fake_game import hand_over, run_command
    from src.companion.mcp_route import describe_help
    from src.companion.ref_ids import parse_ref_id, split_form_id

    def halves(item_id: str) -> list[int]:
        return list(split_form_id(parse_ref_id(item_id)))

    assert consts.ACTION_DROP in consts.PLAYER_ACTIONS and consts.ACTION_DROP in consts.ACTIONS_REQUIRING_TARGET
    assert consts.ACTION_DROP in consts.ACTIONS_WITH_COUNT and consts.ACTION_DROP in consts.ACTIONS_KEEPING_QUEUE
    assert consts.DROP_PROTOCOL <= game_state.GAME_PROTOCOL
    # The fake game answers like the game mod: one item, and several with what fell and what did not
    assert run_command(consts.ACTION_DROP, "0001397D", "")[0] is True
    assert run_command(consts.ACTION_DROP, "0000BEEF", "")[0] is False
    is_ok, message = hand_over(consts.ACTION_DROP, halves("0001397D") + [5] + halves("0000BEEF") + [2])
    assert is_ok is True
    assert message == "You drop: 5 Iron Arrow. It lies on the ground where you stand. Not dropped: number 2 of your list (no such item)."
    assert hand_over(consts.ACTION_DROP, halves("0000BEEF") + [1])[1].startswith("Nothing was dropped.")
    assert "drop = let an item of yours fall" in describe_help("drop")[0] and "For give, take and drop 'detail' says how many" in describe_help("drop")[0]

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def command(arguments: dict) -> dict:
            return tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": arguments}))

        assert "needs a target" in command({"action": "drop"})["message"]
        assert "name the items" in command({"action": "drop", "detail": "junk"})["message"]
        assert "is not how many" in command({"action": "drop", "target": "000318FA,00067180", "detail": "2,x"})["message"]
        assert "'detail' is how many" in command({"action": "drop", "target": "000318FA", "detail": "many"})["message"]
        assert command({"action": "drop", "target": "000318FA", "detail": "2"})["status"] == consts.STATUS_GAME_NOT_CONNECTED


def test_wait_for_event_wakes_for_what_matters_and_keeps_the_rest_ready():
    from src.companion.command_queue import CompanionCommandQueue, wakes_the_ai

    def event(text: str, is_urgent: bool = False, seconds_ago: int = 0) -> dict:
        return {"seconds_ago": seconds_ago, "game_time": "14:35", "urgent": is_urgent, "text": text}

    # What wakes: everything urgent, fights, the player going somewhere else, talks and quests. The rest lies ready
    for text in ("You started fighting Wolf.", "The fight of the player is over.", "The player entered Riverwood.",
                 "The player rides and you are on foot: your horse is about 120 m away from you.",
                 "You have fallen behind: the player went on without you (through a door, or far ahead), and you are no longer where the player is.",
                 "The player used the door to Sleeping Giant Inn.", "The player started talking to Orgnar.", "Quest Before the Storm. Objective done: Talk to Alvor.",
                 "New quest: The Golden Claw. Objective: Find the claw.", "Objective done: Talk to Alvor."):
        assert wakes_the_ai(event(text)), text
    for text in ("The player opened Chest.", "The player used the door 'Wooden Door'.", "The player took 3 Iron Arrow.", "The player started sneaking.",
                 "The player stopped talking to Orgnar.", "The player left Riverwood."):
        assert not wakes_the_ai(event(text)), text
    assert wakes_the_ai(event("Bandit killed Lydia.", True))
    # What the plugin watches over by itself
    for text in ("You have fallen behind: the player is about 45 m away from you.", "You have fallen behind: you are no longer where the player is.",
                 "You are stuck: you have been walking for some seconds without getting anywhere.", "You are low on arrows: 20 left.",
                 "You are low on healing potions: 1 left.", "You are out of magicka potions.", "The player reached level 12.",
                 "The player got a bounty of 40 gold with Whiterun (40 in all there).", "The player now follows the quest The Golden Claw."):
        assert wakes_the_ai(event(text)), text

    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.05)
        queue.WAKE_GAP_SECONDS = 0.4

        async def game(events=None, heard=None, report=None):
            await queue.poll(None, None, events=events, heard=heard, waiting_report=report)

        # Nothing happens: the call comes back empty when its time is up, and the game is told meanwhile that someone waits
        assert queue.is_watched is False
        waiting = asyncio.ensure_future(queue.wait_for_event(0.3))
        await asyncio.sleep(0.05)
        assert queue.is_watched is True
        empty = await waiting
        assert empty["woken"] is False and empty["taken_over"] is False and empty["events"] == [] and 0.25 <= empty["waited_seconds"] < 1.0
        assert queue.is_watched is False

        # What does not wake lies ready and comes along when the time is up
        await game(events=[event("The player took 3 Iron Arrow.")], heard=[{"seconds_ago": 1, "ref_id": "00013BA1", "name": "Orgnar", "text": "Need a room?"}])
        ready = await queue.wait_for_event(0.2)
        assert ready["woken"] is False and [entry["text"] for entry in ready["events"]] == ["The player took 3 Iron Arrow."]
        assert [line["text"] for line in ready["heard"]] == ["Need a room?"]

        # What matters wakes at once, and the answer carries everything since the last one
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game(events=[event("The player opened Chest."), event("The player entered Riverwood.")])
        woken = await asyncio.wait_for(waiting, 1.0)
        assert woken["woken"] is True and woken["waited_seconds"] < 1.0
        assert [entry["text"] for entry in woken["events"]] == ["The player opened Chest.", "The player entered Riverwood."]
        # It was taken: the next STATE does not tell it again
        assert (await queue.wait_for_event(0))["events"] == []

        # Two wake-ups lie apart, so what happens in a row comes together
        started = time.monotonic()
        await game(events=[event("Wolf started fighting the player.", True)])
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game(events=[event("The player is in a fight now.", True)])
        together = await asyncio.wait_for(waiting, 2.0)
        assert together["woken"] is True and len(together["events"]) == 2 and time.monotonic() - started >= 0.25

        # Something the companion started came to an end: that wakes as well
        await asyncio.sleep(0.45)
        await game(report="You could not sit down on Bench.")
        told = await asyncio.wait_for(queue.wait_for_event(5.0), 1.0)
        assert told["woken"] is True and told["reports"] == ["You could not sit down on Bench."]

        # While the companion speaks nothing wakes. The end of its sentence does, and brings along what happened meanwhile
        await asyncio.sleep(0.45)
        await queue.note_speech(0.5)
        assert (await queue.wait_for_event(0))["speaking_seconds"] >= 1
        await game(events=[event("Bandit killed Lydia.", True)])
        started = time.monotonic()
        spoken = await asyncio.wait_for(queue.wait_for_event(5.0), 2.0)
        assert spoken["woken"] is True and spoken["speech_over"] is True and spoken["speaking_seconds"] == 0
        assert time.monotonic() - started >= 0.4 and [entry["text"] for entry in spoken["events"]] == ["Bandit killed Lydia."]
        # A call that ends while the sentence still runs tells how long it goes on
        await queue.note_speech(5.0)
        early = await queue.wait_for_event(0.1)
        assert early["woken"] is False and early["speech_over"] is False and early["speaking_seconds"] >= 4
        await queue.note_speech(0.0)
        assert (await queue.wait_for_event(1.0))["speech_over"] is True

        # A newer call takes over: the older one waits on silently and tells so at its end, without taking anything
        await asyncio.sleep(0.45)
        older = asyncio.ensure_future(queue.wait_for_event(0.6))
        await asyncio.sleep(0.05)
        newer = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game(events=[event("The player used the door to Sleeping Giant Inn.")])
        newest = await asyncio.wait_for(newer, 1.0)
        assert newest["woken"] is True and len(newest["events"]) == 1
        old = await asyncio.wait_for(older, 2.0)
        assert old["taken_over"] is True and old["woken"] is False and old["events"] == []
        assert queue.is_watched is False

        # While someone waits, a request of the game is not held open for long, so that the game tells soon what happens
        slow_queue = CompanionCommandQueue(hold_seconds=5.0)
        slow_queue.WATCH_HOLD_SECONDS = 0.2
        waiting = asyncio.ensure_future(slow_queue.wait_for_event(2.0))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await slow_queue.poll(None, None)
        assert time.monotonic() - started < 1.0
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            pass
        # A call the AI gave up no longer counts as waiting
        assert slow_queue.is_watched is False

    asyncio.run(scenario())


def test_a_queue_that_went_through_does_not_wake_by_itself():
    from src.companion.command_queue import CompanionCommandQueue

    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.05)
        queue.WAKE_GAP_SECONDS = 0.0
        await queue.add_report("Queue: all done (follow).")
        ready = await queue.wait_for_event(0.2)
        assert ready["woken"] is False and ready["reports"] == ["Queue: all done (follow)."]
        # A queue that did not go through wakes, and so does everything else the companion started
        await queue.add_report("Queue: finished. Done: say.")
        assert (await asyncio.wait_for(queue.wait_for_event(5.0), 1.0))["woken"] is True
        await queue.add_report("You could not sit down on Bench.")
        assert (await asyncio.wait_for(queue.wait_for_event(5.0), 1.0))["woken"] is True

    asyncio.run(scenario())


def test_changes_of_place_wake_only_now_and_then():
    from src.companion.command_queue import CompanionCommandQueue, is_area_event

    def event(text: str) -> dict:
        return {"seconds_ago": 0, "game_time": "14:35", "urgent": False, "text": text}

    assert is_area_event(event("The player entered Whistling Mine.")) and is_area_event(event("The player left Whistling Mine."))
    assert not is_area_event(event("The player used the door to Whistling Mine."))

    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.05)
        queue.WAKE_GAP_SECONDS = 0.0
        queue.AREA_QUIET_SECONDS = 0.6

        async def game(events):
            await queue.poll(None, None, events=events)

        # Entering a place wakes
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game([event("The player entered Whistling Mine.")])
        assert (await asyncio.wait_for(waiting, 1.0))["woken"] is True

        # Riding past: right after that, leaving it and entering it again only lie ready
        await game([event("The player left Whistling Mine."), event("The player entered Whistling Mine.")])
        ready = await queue.wait_for_event(0.2)
        assert ready["woken"] is False and len(ready["events"]) == 2

        # A door always wakes, and brings along the place behind it
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game([event("The player used the door to Whistling Mine."), event("The player entered Whistling Mine.")])
        through = await asyncio.wait_for(waiting, 1.0)
        assert through["woken"] is True and len(through["events"]) == 2

        # Leaving a place never wakes by itself, however long ago the last change of place was told
        await asyncio.sleep(0.7)
        await game([event("The player left Whistling Mine.")])
        left = await queue.wait_for_event(0.2)
        assert left["woken"] is False and [entry["text"] for entry in left["events"]] == ["The player left Whistling Mine."]

        # A while after the AI heard of a place the last time, entering one wakes again
        await asyncio.sleep(0.7)
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await game([event("The player entered Riverwood.")])
        assert (await asyncio.wait_for(waiting, 1.0))["woken"] is True

    asyncio.run(scenario())


def test_coming_back_from_a_menu_is_an_event_and_wakes_after_a_long_one():
    from src.companion.command_queue import CompanionCommandQueue, menu_event, wakes_the_ai

    long_one = menu_event("MapMenu", 40.2)
    assert long_one["text"] == "The player is back from the map (open for 40 s)." and wakes_the_ai(long_one)
    short_one = menu_event("ContainerMenu", 2.4)
    assert short_one["text"] == "The player looked into a container or a body for 2 s." and not wakes_the_ai(short_one)
    assert menu_event("Loading Menu", 12.0)["text"] == "The player is back from a loading screen (open for 12 s)."
    # A menu the server has no words for tells nothing: what mods show on the screen closes with every loading screen
    assert menu_event("Some Mod Menu", 9.0) is None and menu_event("BTPS Ovelay Menu", 625.0) is None and menu_event("TrueHUD", 625.0) is None
    # Too short to tell, or nothing of its own
    assert menu_event("InventoryMenu", 0.6) is None and menu_event("Dialogue Menu", 30.0) is None and menu_event("TweenMenu", 30.0) is None

    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.05)
        waiting = asyncio.ensure_future(queue.wait_for_event(5.0))
        await asyncio.sleep(0.05)
        await queue.poll(None, None, menus=[("TweenMenu", 21.0), ("ContainerMenu", 2.0), ("InventoryMenu", 20.0)])
        woken = await asyncio.wait_for(waiting, 1.0)
        assert woken["woken"] is True
        assert [event["text"] for event in woken["events"]] == ["The player looked into a container or a body for 2 s.",
                                                                "The player is back from the inventory (open for 20 s)."]

    asyncio.run(scenario())


def test_wait_for_event_as_a_tool_and_as_a_stream():
    from src.companion import game_state
    from src.companion.mcp_route import SERVER_INSTRUCTIONS, describe_help
    from src.companion.state_text import describe_wait

    def line(seconds_ago: int, minutes: int, is_urgent: bool, text: str) -> str:
        return "\t".join([str(seconds_ago), str(minutes), "1" if is_urgent else "0", text])

    assert consts.WATCH_PROTOCOL <= game_state.GAME_PROTOCOL
    assert "WAIT_FOR_EVENT" in SERVER_INSTRUCTIONS and "last thing of every turn" in SERVER_INSTRUCTIONS
    assert "keeps watch for you" in describe_help("wait_for_event")[0]

    # The texts of the answer
    nothing = {"taken_over": False, "woken": False, "waited_seconds": 50.2, "connected": True, "state": None, "speech_over": False,
               "speaking_seconds": 0, "heard": [], "events": [], "reports": []}
    assert describe_wait(nothing, "WAIT_FOR_EVENT") == "Nothing happened in 50 s.\nSay nothing about this and call WAIT_FOR_EVENT again."
    assert "has taken over" in describe_wait(dict(nothing, taken_over=True), "WAIT_FOR_EVENT")
    happened = dict(nothing, woken=True, waited_seconds=12.0, speech_over=True, reports=["You changed your cover."],
                    events=[{"seconds_ago": 2, "game_time": "14:35", "urgent": False, "text": "The player entered Riverwood."}],
                    heard=[{"seconds_ago": 4, "ref_id": "00013BA1", "name": "Orgnar", "text": "Need a room?"}])
    text = describe_wait(happened, "WAIT_FOR_EVENT")
    assert text.startswith("Something happened (after 12 s of waiting).\nYou have finished speaking in the game")
    assert "What became of what you started: You changed your cover." in text and "- 2 s ago (14:35): The player entered Riverwood." in text
    assert "- 4 s ago, Orgnar: Need a room?" in text and text.endswith("then call WAIT_FOR_EVENT again.")
    assert describe_wait(dict(nothing, woken=True, speech_over=True), "WAIT_FOR_EVENT").startswith("Your sentence is over")
    assert "still speaking in the game, for about 3 s" in describe_wait(dict(nothing, speaking_seconds=3), "WAIT_FOR_EVENT")
    assert "The game is not connected right now" in describe_wait(dict(nothing, connected=False), "WAIT_FOR_EVENT")
    # STATE tells as well while the companion still speaks
    from src.companion.fake_game import build_state
    from src.companion.state_text import describe_state
    shown = {"connected": True, "fresh": True, "state": game_state.read_game_state(build_state())}
    assert "still speaking" not in describe_state(dict(shown, speaking_seconds=0))
    assert "You are still speaking in the game, for about 3 s" in describe_state(dict(shown, speaking_seconds=3))

    with TestClient(create_app(TOKEN, hold_seconds=0.1)) as client:
        def wait(seconds, headers: dict | None = None):
            return client.post(MCP_ROUTE, headers=headers or {}, json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                                                       "params": {"name": "WAIT_FOR_EVENT", "arguments": {"max_wait_seconds": seconds}}})

        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert tools[3]["name"] == "WAIT_FOR_EVENT" and "max_wait_seconds" in tools[3]["inputSchema"]["properties"]
        # Without a game it does not fail, it tells so
        answer = tool_text(wait(0))
        assert answer.startswith("Nothing happened in 0 s. The game is not connected right now")

        # The game is told that someone waits, sends what happens with its next request, and that wakes the call
        answers: list[str] = []
        watcher = threading.Thread(target=lambda: answers.append(tool_text(wait(5))))
        watcher.start()
        time.sleep(0.3)
        assert client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 1}).json()[consts.KEY_WATCH] == 1
        client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 2, consts.KEY_EVENTS: [line(1, 875, False, "The player entered Riverwood.")],
                                             consts.KEY_HEARD: ["3\t00013BA1\tOrgnar\tNeed a room?"],
                                             consts.KEY_REPORT: "You changed your cover."})
        watcher.join(timeout=4)
        assert len(answers) == 1 and answers[0].startswith("Something happened")
        assert "The player entered Riverwood." in answers[0] and "Orgnar: Need a room?" in answers[0] and "You changed your cover." in answers[0]
        # Nobody waits any more
        assert consts.KEY_WATCH not in client.post(consts.ROUTE_GAME, json={consts.KEY_POLL_ID: 3}).json()

        # An AI app that takes a stream gets the answer as one, so that a long wait is not dropped on the way
        response = wait(0, {"Accept": "application/json, text/event-stream"})
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [part for part in response.text.split("\n\n") if part.strip() != ""]
        assert events[-1].startswith("event: message\ndata: ")
        payload = json.loads(events[-1].split("data: ", 1)[1])
        assert payload["id"] == 7 and payload["result"]["content"][0]["text"].startswith("Nothing happened in 0 s.")
        # Every other call answers as plain JSON, as before
        assert rpc(client, "tools/list").headers["content-type"].startswith("application/json")


def test_wait_for_event_carries_out_a_command_and_then_waits():
    from src.companion.mcp_route import SERVER_INSTRUCTIONS, describe_help

    assert "never two in the same turn" in SERVER_INSTRUCTIONS and "as 'command'" in SERVER_INSTRUCTIONS
    assert "'command': a command to carry out before the waiting" in describe_help("wait_for_event")[0]

    with TestClient(create_app(TOKEN, hold_seconds=0.2)) as client:
        def wait(arguments: dict):
            return rpc(client, "tools/call", {"name": "WAIT_FOR_EVENT", "arguments": arguments})

        tools = rpc(client, "tools/list").json()["result"]["tools"]
        schema = tools[3]["inputSchema"]["properties"]["command"]
        assert schema["required"] == ["action"] and "follow" in schema["properties"]["action"]["enum"] and "then" in schema["properties"]

        # Something that is no command is refused, and the call does not wait
        started = time.monotonic()
        refused = wait({"command": {"detail": "no action"}, "max_wait_seconds": 30})
        assert refused.json()["result"]["isError"] is True and "has to be a command" in tool_text(refused) and time.monotonic() - started < 2.0
        # A command that fails (here: no game) is told at once as well
        failed = wait({"command": {"action": "follow"}, "max_wait_seconds": 30})
        assert failed.json()["result"]["isError"] is True and time.monotonic() - started < 15.0
        assert tool_text(failed).startswith("Your command (follow): ") and "It failed, so this call did not wait." in tool_text(failed)

        received: list[dict] = []
        stop = {"now": False, "events": []}

        def game_loop():
            results: list[dict] = []
            while not stop["now"]:
                events, stop["events"] = stop["events"], []
                reply = client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results, consts.KEY_EVENTS: events}).json()
                received.extend(reply.get(consts.KEY_COMMANDS, []))
                results = [{consts.KEY_COMMAND_ID: sent[consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "You follow the player now."}
                           for sent in reply.get(consts.KEY_COMMANDS, [])]

        game = threading.Thread(target=game_loop, daemon=True)
        game.start()
        time.sleep(0.4)
        # The command is carried out, then the same call waits and comes back when its time is up
        text = tool_text(wait({"command": {"action": "follow"}, "max_wait_seconds": 1}))
        assert [sent[consts.KEY_COMMAND_ACTION] for sent in received] == ["follow"]
        lines = text.split("\n")
        assert lines[0] == "Your command (follow): You follow the player now." and lines[1].startswith("Nothing happened in 1 s")
        # An object that comes as JSON text is read as well
        text = tool_text(wait({"command": json.dumps({"action": "follow"}), "max_wait_seconds": 0}))
        assert text.startswith("Your command (follow): You follow the player now.\n") and len(received) == 2
        # Something urgent that happened while the command ran is the wake-up already: the call does not wait
        stop["events"] = ["\t".join(["0", "875", "1", "Wolf started fighting the player."])]
        time.sleep(0.5)
        started = time.monotonic()
        text = tool_text(wait({"command": {"action": "follow"}, "max_wait_seconds": 30}))
        assert "Meanwhile: Wolf started fighting the player." in text and "so this call did not wait" in text and time.monotonic() - started < 10.0
        stop["now"] = True
        game.join(2.0)


def test_wait_stream_sends_signs_of_life_while_it_waits():
    from src.companion import mcp_route
    from src.companion.command_queue import CompanionCommandQueue

    async def collect(message: dict) -> list[str]:
        queue = CompanionCommandQueue(hold_seconds=0.05)
        return [part async for part in mcp_route._stream_reply(message, queue, None)]

    old_beat, old_check = mcp_route.STREAM_BEAT_SECONDS, mcp_route.STREAM_CHECK_SECONDS
    mcp_route.STREAM_BEAT_SECONDS, mcp_route.STREAM_CHECK_SECONDS = 0.2, 0.05
    try:
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "WAIT_FOR_EVENT", "arguments": {"max_wait_seconds": 0.7}}}
        parts = asyncio.run(collect(call))
        assert parts[:-1] != [] and all(part == ": waiting\n\n" for part in parts[:-1])
        assert json.loads(parts[-1].split("data: ", 1)[1])["result"]["content"][0]["text"].startswith("Nothing happened in 1 s.")
        # An AI app that asked to hear of progress gets it as notifications that count up
        call["params"]["_meta"] = {"progressToken": "abc"}
        parts = asyncio.run(collect(call))
        notes = [json.loads(part.split("data: ", 1)[1]) for part in parts[:-1]]
        assert len(notes) >= 1 and all(note["method"] == "notifications/progress" and note["params"]["progressToken"] == "abc" for note in notes)
        assert [note["params"]["progress"] for note in notes] == list(range(1, len(notes) + 1))
    finally:
        mcp_route.STREAM_BEAT_SECONDS, mcp_route.STREAM_CHECK_SECONDS = old_beat, old_check


def test_unfavorite_is_favorite_with_off():
    from src.companion.mcp_route import _check_step

    assert "needs a target" in _check_step({"action": "unfavorite"}) and _check_step({"action": "unfavorite", "target": "00013985"}) == ""
    with TestClient(create_app(TOKEN, hold_seconds=5)) as client:
        received_commands: list[dict] = []

        def game():
            reply = client.post(consts.ROUTE_GAME, json={consts.KEY_STATE: {"location": "Whiterun"}}).json()
            received_commands.extend(reply[consts.KEY_COMMANDS])
            results = [{consts.KEY_COMMAND_ID: received_commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "Done."}]
            client.post(consts.ROUTE_GAME, json={consts.KEY_RESULTS: results})

        threading.Thread(target=game, daemon=True).start()
        time.sleep(0.3)
        assert "needs a target" in tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "unfavorite"}}))["message"]
        answer = tool_payload(rpc(client, "tools/call", {"name": "COMMAND", "arguments": {"action": "unfavorite", "target": "00013985", "detail": "on"}}))
        assert answer["status"] == consts.STATUS_DONE
        sent = received_commands[0]
        # What reaches the game is favorite, with 0 for 'off'
        assert (sent[consts.KEY_COMMAND_ACTION], sent[consts.KEY_COMMAND_COUNT], sent[consts.KEY_COMMAND_TARGET]) == (consts.ACTION_FAVORITE, 0, "00013985")
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert "unfavorite" in tools[0]["inputSchema"]["properties"]["action"]["enum"]


def test_memory_comes_once_with_the_first_answer_of_a_session(tmp_path):
    folder = str(tmp_path / "memory")
    client = TestClient(create_app(TOKEN, hold_seconds=0.1, memory_folder=folder))
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["COMMAND", "STATE", "HELP", "WAIT_FOR_EVENT", "MEMORY"]
    rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    first = tool_text(rpc(client, "tools/call", {"name": "HELP", "arguments": {"topic": "say"}}))
    assert first.startswith("YOUR MEMORY")
    # The personality and the important topics come whole, the normal ones only with their line in the list
    assert "Aela die Jägerin" in first and "Hochwodka" in first and "Poldi" in first and "Der Held heißt Koi" in first
    assert "- ausruestung [normal]" in first and "Alter Nordkriegshammer" not in first
    second = tool_text(rpc(client, "tools/call", {"name": "HELP", "arguments": {"topic": "say"}}))
    assert "YOUR MEMORY" not in second
    # A server without a memory offers no tool MEMORY
    plain = TestClient(create_app(TOKEN, hold_seconds=0.1))
    assert "MEMORY" not in [tool["name"] for tool in rpc(plain, "tools/list").json()["result"]["tools"]]


def test_memory_keeps_reads_and_forgets_topics(tmp_path):
    import os
    folder = str(tmp_path / "memory")
    client = TestClient(create_app(TOKEN, hold_seconds=0.1, memory_folder=folder))

    def memory(arguments: dict):
        return rpc(client, "tools/call", {"name": "MEMORY", "arguments": arguments}).json()["result"]

    kept = memory({"action": "write", "topic": "Jarl Balgruuf", "text": "Der Jarl von Weißlauf. Mag keine Drachen.",
                   "summary": "Jarl von Weißlauf", "importance": "important"})
    assert not kept.get("isError") and "jarl-balgruuf" in kept["content"][0]["text"]
    assert "- jarl-balgruuf [important] Jarl Balgruuf: Jarl von Weißlauf" in memory({"action": "index"})["content"][0]["text"]
    assert "Mag keine Drachen" in memory({"action": "read", "topic": "jarl-balgruuf"})["content"][0]["text"]
    with open(os.path.join(folder, "index.md"), encoding="utf-8") as file:
        assert "jarl-balgruuf" in file.read()
    too_long = memory({"action": "write", "topic": "lang", "text": "x" * 2501})
    assert too_long["isError"] is True
    assert memory({"action": "forget", "topic": "jarl-balgruuf"}).get("isError") is not True
    assert memory({"action": "read", "topic": "jarl-balgruuf"})["isError"] is True
    assert os.path.isfile(os.path.join(folder, "vergessen", "jarl-balgruuf.md"))


def test_memory_forgets_what_was_not_used_for_long_in_the_game(tmp_path):
    import os
    from src.companion.memory import CompanionMemory

    folder = str(tmp_path / "memory")
    memory = CompanionMemory(folder)
    memory.write("alter kram", "Etwas Altes.", "", "unimportant", 100)
    memory.write("normales", "Etwas Normales.", "", "normal", 100)
    memory.write("wichtiges", "Etwas Wichtiges.", "", "important", 100)
    # An older save (game day before the one of the last use) forgets nothing
    memory.note_session_start()
    memory.take_start_text(50)
    assert os.path.isfile(os.path.join(folder, "alter-kram.md"))
    memory.note_session_start()
    start = memory.take_start_text(110)
    assert not os.path.isfile(os.path.join(folder, "alter-kram.md"))
    assert os.path.isfile(os.path.join(folder, "vergessen", "alter-kram.md"))
    assert os.path.isfile(os.path.join(folder, "normales.md"))
    assert "alter-kram" not in start
    assert os.path.isfile(os.path.join(folder, "wichtiges.md"))
    # Personality and essential topics are never forgotten, important ones after 60 game days
    memory.note_session_start()
    memory.take_start_text(100000)
    assert os.path.isfile(os.path.join(folder, "aela.md")) and os.path.isfile(os.path.join(folder, "pferde.md"))
    assert os.path.isfile(os.path.join(folder, "koi.md"))
    assert not os.path.isfile(os.path.join(folder, "normales.md")) and not os.path.isfile(os.path.join(folder, "wichtiges.md"))
    # A topic without a game day (the starting ones) cannot be told old, so it stays until it is used once
    assert os.path.isfile(os.path.join(folder, "running-gags.md"))


def test_memory_keeps_what_happened_last_over_a_new_start(tmp_path):
    from src.companion.memory import CompanionMemory

    folder = str(tmp_path / "memory")
    memory = CompanionMemory(folder)
    for number in range(35):
        memory.note_recent("event", f"Ereignis {number}", "19:00")
    memory.note_recent("said", "Hochwodka für alle!")
    again = CompanionMemory(folder)
    again.note_session_start()
    start = again.take_start_text(None)
    assert "Recently" in start and "You said: Hochwodka für alle!" in start
    assert "Ereignis 34" in start and "Ereignis 5 " not in start and "Ereignis 5\n" not in start
    assert again.take_start_text(None) == ""


def test_the_state_tells_the_game_day():
    from src.companion import game_state
    assert game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 2, 0, 26, 0, 0, 312]})["game_day"] == 312
    assert "game_day" not in game_state.read_game_state({game_state.KEY_WORLD_VALUES: [0, 600, 2, 0, 26, 0, 0]})
