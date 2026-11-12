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

    async def send_command(self, action: str, target: str = "", detail: str = "", result_timeout_seconds: float = 8.0) -> dict[str, Any]:
        """Queues a command for the game and waits until the game has picked it up and reported a result

        Args:
            target: ref_id of the target character (see ref_ids.py), or empty
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
            command: dict[str, Any] = {
                consts.KEY_COMMAND_ID: command_id,
                consts.KEY_COMMAND_ACTION: action,
                consts.KEY_COMMAND_TARGET: target,
                consts.KEY_COMMAND_TARGET_HIGH: target_high,
                consts.KEY_COMMAND_TARGET_LOW: target_low,
                consts.KEY_COMMAND_DETAIL: detail,
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

    async def get_state(self, refresh_timeout_seconds: float = 6.0) -> dict[str, Any]:
        """Returns the game state. If the game is connected, it is asked for a fresh state first

        Returns:
            A dictionary with 'connected', 'fresh', 'state_age_seconds', 'state' and 'heard':
            what the characters in the game said since this was called the last time
        """
        async with self.__changed:
            version_before = self.__state_version
            is_connected = self.__is_game_connected()

        is_fresh = False
        if is_connected:
            request = await self.send_command(consts.ACTION_REPORT_STATE)
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
                "heard": self.__take_heard(),
            }

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
