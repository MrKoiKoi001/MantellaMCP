import json
import threading
import time

from fastapi.testclient import TestClient

from companion_main import create_app
from src.companion.companion_constants import companion_constants as consts

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
    assert [tool["name"] for tool in tools] == ["COMMAND", "STATE"]


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
        "time": "10:00", "weather": "clear", "quests": [], "protocol": 0, "around_companion": False, "visible_only": False, "hearing": False, "nearby": []}


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
        "The player's inventory:",
        "- Weapons: Iron Sword 00012E49 (equipped)",
        "- Potions and food: Potion of Minor Healing 0003EADD x5",
        "- Other: Lockpick 0000000A x12",
    ]
    assert not any("inventory" in line for line in text_for(both))
    both["inventory_own"]["total"] = 143
    assert text_for(both, consts.INVENTORY_OWN)[-1] == "- The list is cut off: only the first 120 of 143 kinds of items were checked."
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
        return await _find_nearest_seat(queue), (await queue.get_state())["heard"]

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
        assert "come_this_way" in tools[0]["description"]

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
        assert command(client, {"action": "say", "detail": "Watch out!"}) == {"status": consts.STATUS_DONE, "message": "You say it aloud."}
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
                                         "Places: Pelagia Farm 000C0001 (farm) 120 m ahead-left, Whiterun Stables 000C0002 (stable) 260 m behind"]
    shown = "\n".join(seen)
    assert "Wooden Bench" not in shown and "Strongbox" not in shown and "Grindstone" not in shown
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
        assert asked == [{"input": "Hallo, mein Thane.", "voice": "Uta Obando", "language": "de"}]
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
    # 200 % times what 'loud' adds would be five times as loud, and four times is the most there is
    assert read_sound(voice_folder / LINE_FILES[1])[0] == 4000
    quiet = CompanionVoice(str(game_folder), str(tmp_path / "piper"), piper_command=[sys.executable, str(piper)])
    assert quiet.speak("Halt!", "FemaleEvenToned", True)[0] is True and quiet.has_talk_gestures is False

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
        assert answer == {"status": consts.STATUS_DONE, "message": "You say it aloud."}
        sent = received_commands[0]
        assert sent[consts.KEY_COMMAND_TALK] in consts.TALK_EVENTS and sent[consts.KEY_COMMAND_DETAIL] == "Archers on the tower."
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {"sheathe", "draw", "open", "close"} <= set(tools[0]["inputSchema"]["properties"]["action"]["enum"])
        assert tools[0]["inputSchema"]["properties"]["loud"]["type"] == "boolean"
        assert "about 200 meters" in tools[1]["description"] and "lying asleep" in tools[1]["description"]
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
