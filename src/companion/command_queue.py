import asyncio
import time
import uuid
from typing import Any, Callable

from src.companion.companion_constants import companion_constants as consts
from src.companion.ref_ids import parse_ref_id, split_form_id


class CompanionCommandQueue:
    """Hands commands from the MCP side (the AI) to the game side (Skyrim).

    The game can only ask, the server can only answer. So the game sends a long polling
    request that is held open until a command is waiting (or until hold_seconds have passed),
    and sends the next request right after it got an answer. Collecting the game state takes
    the game about a second, so a request only carries it when the game was asked for it with
    the command report_state. The newest state is kept here for the MCP side to read.
    """

    MAX_STORED_RESULTS: int = 50
    # What the characters in the game said is kept here until the AI asks for the state the next time.
    # A long talk must not fill the conversation of the AI, so it only gets the newest lines, and no old ones
    MAX_HEARD_LINES: int = 10
    MAX_HEARD_AGE_SECONDS: float = 300.0
    # The game shows a longer speech in several pieces. Pieces of one speaker that follow each other this closely become one line
    HEARD_MERGE_SECONDS: float = 15.0
    MAX_MERGED_HEARD_LENGTH: int = 600
    # How many characters (and how many things) the state lists if the AI names no number, and the most it can ask for.
    # The game mod has the same two numbers (DefaultListLimit and HighestListLimit in KI_MCP_Gefaehrte_Loop.psc)
    DEFAULT_MAX_LINES: int = 10
    HIGHEST_MAX_LINES: int = 30
    # Collecting a whole inventory takes the game several seconds
    INVENTORY_TIMEOUT_SECONDS: float = 15.0
    # What became of commands that take time (loot, move_to, sit) is kept here until the AI asks for the state the next time
    MAX_REPORTS: int = 5

    def __init__(self, hold_seconds: float = 25.0, connection_grace_seconds: float = 3.0) -> None:
        self.__hold_seconds: float = hold_seconds
        self.__connection_grace_seconds: float = connection_grace_seconds
        self.__changed: asyncio.Condition = asyncio.Condition()
        self.__pending_commands: list[dict[str, Any]] = []
        self.__delivered_command_ids: set[str] = set()
        self.__results: dict[str, dict[str, Any]] = {}
        self.__state: dict[str, Any] | None = None
        self.__state_time: float = 0.0
        self.__state_version: int = 0
        self.__heard: list[dict[str, Any]] = []
        self.__reports: list[str] = []
        self.__poll_generation: int = 0
        self.__is_poll_open: bool = False
        self.__last_poll_time: float | None = None

    # ------------------------------------------------------------------ game side

    async def poll(self, state: dict[str, Any] | None, results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Called for every long polling request of the game.

        Args:
            state: The current game state (see game_state.read_game_state), or None if this request carries none
            results: Results of commands the game has executed since its last request

        Returns:
            The commands the game should execute now. Empty if nothing came up within hold_seconds.
        """
        async with self.__changed:
            self.__poll_generation += 1
            own_generation = self.__poll_generation
            self.__is_poll_open = True
            self.__last_poll_time = time.monotonic()
            if isinstance(state, dict):
                # The game hands out every line only once, so the lines must not get lost with the next state
                self.__keep_heard(state.pop("heard", None))
                # The same goes for what became of loot and move_to
                report = state.pop("report", None)
                if isinstance(report, str) and report.strip() != "":
                    self.__reports = (self.__reports + [report.strip()])[-self.MAX_REPORTS:]
                self.__state = state
                self.__state_time = time.monotonic()
                self.__state_version += 1
            self.__store_results(results)
            self.__changed.notify_all()

            try:
                await self.__wait_until(
                    lambda: len(self.__pending_commands) > 0 or self.__poll_generation != own_generation,
                    self.__hold_seconds)
                if self.__poll_generation != own_generation:
                    # A newer request arrived (eg after loading a save). The newer one gets the commands.
                    return []
                commands = self.__pending_commands
                self.__pending_commands = []
                for command in commands:
                    self.__delivered_command_ids.add(command[consts.KEY_COMMAND_ID])
                return commands
            finally:
                if self.__poll_generation == own_generation:
                    self.__is_poll_open = False
                    self.__last_poll_time = time.monotonic()
                self.__changed.notify_all()

    # ------------------------------------------------------------------- MCP side

    async def send_command(self, action: str, target: str = "", detail: str = "", result_timeout_seconds: float = 8.0,
                           count: int = 1, item: str = "", things: bool = False, around_companion: bool = False,
                           stats: bool = False, visible: bool = False, space: bool = False, find: str = "") -> dict[str, Any]:
        """Queues a command for the game and waits until the game has picked it up and reported a result

        Args:
            target: ref_id of the target character or ID of the item (see ref_ids.py), or empty
            count: For give and take: how many of the item, 0 = all of them
            item: For loot: ID of the one item to take from the body, or empty for everything.
                For gesture: ID of the animation to play, or empty to end the gesture
            around_companion: For report_state: whether the lists of the state should be seen from the companion
                instead of from the player
            stats: For report_state: whether the state should carry the level, the skills and so on of the player
            visible: For report_state: whether the list of characters should only hold those the viewer can see
            space: For report_state: whether the state should carry what is around the companion as rays find it
            find: For report_state: a name, or a part of one, to look for in the whole loaded area
            things: For report_state: whether the state should carry the things close to the player.
                'count' is then how many characters (and things of each kind) the state should list at most
            result_timeout_seconds: How long to wait for the result. The game reports it right away,
                unless a request for the state came in together with the command

        Returns:
            A dictionary with 'status' (see companion_constants.STATUS_*) and 'message'
        """
        async with self.__changed:
            if not self.__is_game_connected():
                return self.__status(consts.STATUS_GAME_NOT_CONNECTED, "The game is not polling. Is Skyrim running with a loaded save?")

            command_id = uuid.uuid4().hex
            target_high, target_low = split_form_id(parse_ref_id(target) or 0)
            item_high, item_low = split_form_id(parse_ref_id(item) or 0)
            command: dict[str, Any] = {
                consts.KEY_COMMAND_ID: command_id,
                consts.KEY_COMMAND_ACTION: action,
                consts.KEY_COMMAND_TARGET: target,
                consts.KEY_COMMAND_TARGET_HIGH: target_high,
                consts.KEY_COMMAND_TARGET_LOW: target_low,
                consts.KEY_COMMAND_DETAIL: detail,
                consts.KEY_COMMAND_COUNT: count,
                consts.KEY_COMMAND_ITEM_HIGH: item_high,
                consts.KEY_COMMAND_ITEM_LOW: item_low,
                consts.KEY_COMMAND_THINGS: int(things),
                consts.KEY_COMMAND_AROUND: int(around_companion),
                consts.KEY_COMMAND_STATS: int(stats),
                consts.KEY_COMMAND_VISIBLE: int(visible),
                consts.KEY_COMMAND_SPACE: int(space),
                consts.KEY_COMMAND_FIND: find,
            }
            self.__pending_commands.append(command)
            self.__changed.notify_all()

            is_delivered = await self.__wait_until(
                lambda: command_id in self.__delivered_command_ids,
                self.__connection_grace_seconds + 2.0)
            if not is_delivered:
                self.__pending_commands = [c for c in self.__pending_commands if c is not command]
                return self.__status(consts.STATUS_NOT_DELIVERED, "The game did not pick up the command in time.")
            self.__delivered_command_ids.discard(command_id)

            if action == consts.ACTION_REPORT_STATE:
                return self.__status(consts.STATUS_DELIVERED, "")

            has_result = await self.__wait_until(lambda: command_id in self.__results, result_timeout_seconds)
            if not has_result:
                return self.__status(consts.STATUS_DELIVERED, "The game received the command but has not reported a result yet.")
            result = self.__results.pop(command_id)
            is_ok = bool(result.get(consts.KEY_RESULT_OK, False))
            message = str(result.get(consts.KEY_RESULT_MESSAGE, ""))
            return self.__status(consts.STATUS_DONE if is_ok else consts.STATUS_FAILED, message)

    async def get_state(self, refresh_timeout_seconds: float = 6.0, inventory: str = "", corpse: str = "",
                        is_for_the_ai: bool = True, things: bool = False, max_lines: int = DEFAULT_MAX_LINES,
                        around_companion: bool = False, stats: bool = False, visible: bool = False,
                        space: bool = False, find: str = "") -> dict[str, Any]:
        """Returns the game state. If the game is connected, it is asked for a fresh state first

        Args:
            inventory: Whose inventory the fresh state should carry (companion_constants.INVENTORY_...), or empty for none
            corpse: ref_id of a dead character or a container. The fresh state then carries what is in it, instead of 'inventory'
            things: Whether the fresh state should carry the things close to the player (furniture, containers, loose items)
            max_lines: How many characters, and how many things, the fresh state should list at most
            around_companion: Whether the fresh state should list everything as the companion sees it (what is around
                the companion, with distances and directions from it) instead of as the player sees it
            stats: Whether the fresh state should carry the level, the skills and so on of the player
            visible: Whether the fresh state should only list what the viewer can see
            space: Whether the fresh state should carry what is around the companion as rays find it (walls, drops)
            find: A name, or a part of one, the game should look for in the whole loaded area (empty = no search)
            is_for_the_ai: False if the server itself needs the state (eg to pick a seat). Then what was heard and
                what became of earlier commands stays kept for the next time the AI asks

        Returns:
            A dictionary with 'connected', 'fresh', 'state_age_seconds', 'state', 'inventory_wanted', 'corpse_wanted',
            'things_wanted', 'max_lines', 'around_wanted', 'stats_wanted', 'visible_wanted', 'space_wanted', 'find_wanted',
            'heard' (what the characters in the game said since this was called the last time)
            and 'reports' (what became of loot and move_to since then)
        """
        if corpse != "":
            inventory = consts.INVENTORY_CORPSE
        if inventory != "":
            refresh_timeout_seconds = max(refresh_timeout_seconds, self.INVENTORY_TIMEOUT_SECONDS)
        async with self.__changed:
            version_before = self.__state_version
            is_connected = self.__is_game_connected()

        is_fresh = False
        if is_connected:
            request = await self.send_command(consts.ACTION_REPORT_STATE, target=corpse, detail=inventory, count=max_lines, things=things,
                                              around_companion=around_companion, stats=stats, visible=visible, space=space,
                                              find=find)
            # Only a game that picked up the request will send a fresh state for it
            if request["status"] == consts.STATUS_DELIVERED:
                async with self.__changed:
                    is_fresh = await self.__wait_until(lambda: self.__state_version > version_before, refresh_timeout_seconds)

        async with self.__changed:
            age: float | None = None
            if self.__state is not None:
                age = round(time.monotonic() - self.__state_time, 1)
            return {
                "connected": self.__is_game_connected(),
                "fresh": is_fresh,
                "state_age_seconds": age,
                "state": self.__state,
                # The kept state may still carry an inventory from an earlier question. Only a fresh one is worth showing
                "inventory_wanted": inventory if is_fresh else "",
                "inventory_missed": inventory != "" and not is_fresh,
                "corpse_wanted": corpse if is_fresh else "",
                # The kept state may still carry things from an earlier question
                "things_wanted": things and is_fresh,
                "things_missed": things and not is_fresh,
                "max_lines": max_lines,
                # Whether the game did it is in the state itself ('around_companion')
                "around_wanted": around_companion and is_fresh,
                # The kept state may still carry stats from an earlier question
                "stats_wanted": stats and is_fresh,
                "stats_missed": stats and not is_fresh,
                # Whether the game did it is in the state itself ('visible_only')
                "visible_wanted": visible and is_fresh,
                # The kept state may still carry the surroundings from an earlier question, and the companion has moved since
                "space_wanted": space and is_fresh,
                "space_missed": space and not is_fresh,
                # What the search was for. Whether the game did it is in the state itself ('found_for')
                "find_wanted": find if is_fresh else "",
                "find_missed": find != "" and not is_fresh,
                "heard": self.__take_heard() if is_for_the_ai else [],
                "reports": self.__take_reports() if is_for_the_ai else [],
            }

    async def peek_state(self) -> dict[str, Any] | None:
        """Returns the state the game sent last, without asking the game for a fresh one (None = it sent none yet)"""
        async with self.__changed:
            return self.__state

    # -------------------------------------------------------------------- helpers

    def __is_game_connected(self) -> bool:
        if self.__is_poll_open:
            return True
        if self.__last_poll_time is None:
            return False
        return time.monotonic() - self.__last_poll_time <= self.__connection_grace_seconds

    def __store_results(self, results: list[dict[str, Any]] | None):
        if not isinstance(results, list):
            return
        for result in results:
            if isinstance(result, dict) and consts.KEY_COMMAND_ID in result:
                self.__results[str(result[consts.KEY_COMMAND_ID])] = result
        while len(self.__results) > self.MAX_STORED_RESULTS:
            self.__results.pop(next(iter(self.__results)))

    def __keep_heard(self, heard: Any):
        if not isinstance(heard, list):
            return
        now = time.monotonic()
        for line in heard:
            if not isinstance(line, dict):
                continue
            heard_time = now - float(line.get("seconds_ago", 0))
            text = str(line.get("text", ""))
            previous = self.__heard[-1] if len(self.__heard) > 0 else None
            if (previous is not None and previous["ref_id"] == line.get("ref_id")
                    and 0.0 <= heard_time - previous["time"] <= self.HEARD_MERGE_SECONDS
                    and len(previous["text"]) + len(text) < self.MAX_MERGED_HEARD_LENGTH):
                previous["text"] += " " + text
                previous["time"] = heard_time
                continue
            self.__heard.append({"ref_id": line.get("ref_id"), "name": line.get("name"), "text": text, "time": heard_time})
        # Bounded even if the AI never asks
        self.__heard = self.__heard[-self.MAX_STORED_RESULTS:]

    def __take_heard(self) -> list[dict[str, Any]]:
        """The lines the AI has not been given yet, oldest first. They are forgotten afterwards"""
        now = time.monotonic()
        lines = [line for line in self.__heard if now - line["time"] <= self.MAX_HEARD_AGE_SECONDS]
        self.__heard = []
        return [{
            "seconds_ago": max(0, round(now - line["time"])),
            "ref_id": line["ref_id"],
            "name": line["name"],
            "text": line["text"],
        } for line in lines[-self.MAX_HEARD_LINES:]]

    def __take_reports(self) -> list[str]:
        """What became of loot and move_to and the AI has not been told yet, oldest first. Forgotten afterwards"""
        reports = self.__reports
        self.__reports = []
        return reports

    async def __wait_until(self, predicate: Callable[[], bool], timeout_seconds: float) -> bool:
        """Waits (with the condition lock held by the caller) until predicate is true or the timeout hits"""
        try:
            await asyncio.wait_for(self.__changed.wait_for(predicate), timeout_seconds)
            return True
        except asyncio.TimeoutError:
            return predicate()

    @staticmethod
    def __status(status: str, message: str) -> dict[str, Any]:
        return {"status": status, "message": message}
