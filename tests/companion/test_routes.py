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
    assert [character["distance_meters"] for character in state["nearby"]] == [8, 12, 20]
    # The ref ID came as a negative number, and the name in lower case
    assert state["nearby"][1] == {"name": "Bandit", "ref_id": "FF000A01", "distance_meters": 12, "angle_degrees": -40, "hostile": True, "dead": False}
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
        "000F6845 Whiterun Guard, 8 m right",
        "FF000A01 Bandit, 12 m ahead-left, hostile",
        "FF000A02 Bandit, 20 m behind, hostile",
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
        "000F6845 Whiterun Guard, 8 m right, from you 10 m ahead-left",
        "FF000A01 Bandit, 12 m ahead-left, from you 13 m behind, hostile",
        "FF000A02 Bandit, 20 m behind-right, from you 19 m ahead, hostile",
        "FF000A03 Bandit Outlaw, dead, 5 m ahead, from you 8 m behind-left",
        "Places: Pelagia Farm (farm) 120 m ahead-left, Whiterun Stables (stable) 260 m behind",
        "Doors: Warmaiden's 20 m right, Dragonsreach 140 m ahead-left",
        "Quests:",
        # The Jarl is north of the player, who faces north-east. Lydia faces south, so the door is behind her
        "- Before the Storm (tracked): Talk to the Jarl of Whiterun "
        "[target Jarl Balgruuf: behind the door to Dragonsreach, 140 m ahead-left, from you 141 m behind]",
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
        return describe_state({"connected": True, "fresh": True, "state_age_seconds": 0.0, "state": state}).splitlines()[0]

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
        "time": "10:00", "weather": "clear", "quests": [], "hearing": False, "nearby": []}


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
        ("Faendal", False), ("Wolf", False), ("Bandit Chief", True), ("Bandit", True)]
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
    assert state["corpse"] == {"ref_id": "FF000A03", "name": "Bandit Outlaw"}
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
    missing = "There is no dead character with the ref_id 000F6845 around. Take the ref_id from the dead in 'Nearby'."
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
    # An older game mod sends no places at all, and then nothing is said about them
    assert "places" not in game_state.read_game_state({game_state.KEY_LOCATION: "Riverwood"})
    assert game_state.read_game_state({game_state.KEY_PLACES: []})["places"] == []
