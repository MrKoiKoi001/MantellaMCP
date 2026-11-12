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
]


def to_papyrus_number(ref_id: str) -> int:
    """Papyrus only knows signed numbers, so the game mod sends ref IDs from 80000000 on as negative numbers"""
    if ref_id == "":
        return 0
    form_id = int(ref_id, 16)
    if form_id >= SIGNED_NUMBER_LIMIT:
        return form_id - UNSIGNED_NUMBER_COUNT
    return form_id


def build_state(is_waiting: bool = False, target_ref_id: str = "") -> dict:
    """Same short form as BuildState in KI_MCP_Gefaehrte_Loop.psc sends. What the numbers mean: game_state.py"""
    is_fighting = target_ref_id != ""
    nearby_values: list[int] = []
    nearby_names: list[str] = []
    for place, character in enumerate(NEARBY):
        nearby_values.extend([
            to_papyrus_number(character["ref_id"]), character["distance_meters"],
            character["angle_degrees"], int(character["hostile"]),
        ])
        nearby_names.append(f"{place}{game_state.NAME_PLACE_SEPARATOR}{character['name']}")
    return {
        game_state.KEY_LOCATION: "Whiterun, Plains District",
        game_state.KEY_PLAYER_NAME: "Dragonborn",
        game_state.KEY_PLAYER_VALUES: [85, int(is_fighting), 23500, -7900, -3300, 45, 0],
        game_state.KEY_COMPANION_NAME: "Lydia",
        game_state.KEY_COMPANION_VALUES: [
            to_papyrus_number("000A2C94"), 100, 0, int(is_fighting), int(is_waiting), 1, 3, -150,
            to_papyrus_number(target_ref_id),
        ],
        game_state.KEY_NEARBY_VALUES: nearby_values,
        game_state.KEY_NEARBY_NAMES: nearby_names,
        game_state.KEY_TIMES: [0, 0],
    }


def run_command(action: str, target: str, target_ref_id: str) -> tuple[bool, str, str]:
    """Answers attack and stand_down like the game mod does

    Returns:
        Whether it worked, the text for the AI, and whom the companion fights afterwards (ref_id or empty)
    """
    if action == consts.ACTION_ATTACK:
        for character in NEARBY:
            if character["ref_id"] == target:
                return True, f"You attack {character['name']} now.", target
        return False, f"There is no character with the ref_id '{target}'. Take the ref_id from 'nearby' in STATE.", target_ref_id
    if action == consts.ACTION_STAND_DOWN:
        if target_ref_id == "":
            return True, "You were not fighting.", ""
        return True, "You stop fighting. Whoever keeps attacking you or the player will pull you back into the fight.", ""
    return True, f"Fake game did '{action}'.", target_ref_id


def is_waiting_after(action: str, is_waiting: bool) -> bool:
    """Like the game: 'wait' makes the companion stay, 'follow' makes it come along again, everything else changes nothing"""
    if action == consts.ACTION_WAIT:
        return True
    if action == consts.ACTION_FOLLOW:
        return False
    return is_waiting


def main():
    parser = argparse.ArgumentParser(description="Fake Skyrim for testing the companion server")
    parser.add_argument("--port", type=int, default=4998)
    args = parser.parse_args()
    url = f"http://localhost:{args.port}{consts.ROUTE_GAME}"

    results: list[dict] = []
    is_waiting = False
    target_ref_id = ""
    poll_id = 0
    # Like the game mod: the state goes along with the first request, and after that only when the server asked for it
    is_state_wanted = True
    print(f"Fake game polling {url} (Ctrl+C to stop)")
    while True:
        poll_id += 1
        request_body: dict = {consts.KEY_POLL_ID: poll_id, consts.KEY_RESULTS: results}
        if is_state_wanted:
            request_body[consts.KEY_STATE] = build_state(is_waiting, target_ref_id)
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
                continue
            print(f"COMMAND  action={action}  target={target}  detail={detail}")
            is_waiting = is_waiting_after(str(action), is_waiting)
            is_ok, message, target_ref_id = run_command(str(action), str(target), target_ref_id)
            results.append({
                consts.KEY_COMMAND_ID: command.get(consts.KEY_COMMAND_ID),
                consts.KEY_RESULT_OK: is_ok,
                consts.KEY_RESULT_MESSAGE: message,
            })


if __name__ == "__main__":
    main()
