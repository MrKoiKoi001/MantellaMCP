import asyncio
import time
import uuid
from typing import Any, Callable

from src.companion.companion_constants import companion_constants as consts
from src.companion.ref_ids import parse_ref_id, split_form_id
from src.companion.server_log import log, shorten


# From this long on describe_silence counts in minutes
SILENCE_IN_MINUTES_FROM_SECONDS: float = 120.0
# From this long on describe_menu counts in whole seconds
MENU_WHOLE_SECONDS: float = 10.0


def describe_menu(name: str, open_seconds: float) -> str:
    """A menu that was open in the game and for how long, for the log: 'BarterMenu 34 s', 'Console 0.8 s'"""
    if open_seconds < MENU_WHOLE_SECONDS:
        return f"{name} {open_seconds:.1f} s"
    return f"{name} {round(open_seconds)} s"


def describe_silence(silent_seconds: float) -> str:
    """Returns as text for the AI that the game has stopped asking for commands, and what that means

    The server only sees that the requests of the game stay away. A menu, a loading screen and a game that was
    closed look the same from here
    """
    if silent_seconds < SILENCE_IN_MINUTES_FROM_SECONDS:
        duration = f"{max(int(silent_seconds), 1)} seconds"
    else:
        duration = f"{int(silent_seconds // 60)} minutes"
    return (f"The game has not asked for commands for {duration}: it is paused (the player is probably in a menu "
            "or at a loading screen) or was closed.")


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
    INVENTORY_TIMEOUT_SECONDS: float = 20.0
    # How long the server waits for the fresh state it asked the game for. The script of the game mod waits for the
    # next picture of the game with nearly every question it asks, so a state took it up to six and a half seconds
    STATE_TIMEOUT_SECONDS: float = 12.0
    # A game that picked up commands with its last request and has not asked again is at work on them (collecting a
    # state, carrying out a command). For this long it counts as there, not as gone
    WORK_SECONDS: float = 20.0
    # What became of commands that take time (loot, move_to, sit) is kept here until the AI asks for the state the next time
    MAX_REPORTS: int = 5
    # What happened in the game is kept here until the AI asks for the state the next time: the newest events, none
    # older than this. The urgent ones (a fight begins, someone dies) hang onto the answer of the next COMMAND already
    MAX_EVENT_LINES: int = 25
    MAX_EVENT_AGE_SECONDS: float = 600.0
    MAX_URGENT_EVENTS: int = 5
    MAX_STORED_EVENTS: int = 60
    # While commands wait in a chain (see command_chain.py) the request of the game is only held open this long,
    # so that the game asks again every second and tells whether the companion is still busy
    QUICK_HOLD_SECONDS: float = 1.0
    # What the game said about being busy counts as fresh for this long
    BUSY_FRESH_SECONDS: float = 1.5
    # In a menu and at a loading screen the game stands still and its mod asks for nothing. A command waits this long
    # for a game that was there before to go on. It does not stay any longer: carried out minutes later it would be stale
    PAUSE_WAIT_SECONDS: float = 20.0
    # A game that has been silent for longer than this is not waited for at all. It may as well have been closed, and
    # then every command would hang for PAUSE_WAIT_SECONDS
    PAUSE_LONGEST_SECONDS: float = 300.0
    # From this long on the log file tells that the game was silent between two of its requests
    SILENCE_LOG_SECONDS: float = 2.0

    def __init__(self, hold_seconds: float = 25.0, connection_grace_seconds: float = 3.0,
                 pause_wait_seconds: float = PAUSE_WAIT_SECONDS) -> None:
        self.__hold_seconds: float = hold_seconds
        self.__pause_wait_seconds: float = pause_wait_seconds
        # Whether the game mod is to cut off the sentences the game lets the companion say on its own.
        # Goes along with every command, the game mod takes it from report_state
        self.mutes_game_lines: bool = False
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
        self.__events: list[dict[str, Any]] = []
        self.__poll_generation: int = 0
        self.__is_poll_open: bool = False
        self.__last_poll_time: float | None = None
        # For the log file: the commands the game got with the answer to its last request ("" = none, the request ran out)
        self.__last_handed: str = ""
        # Whether the companion is in the middle of something that takes time (a walk, sitting down, mounting), as the
        # game told with its last request. None = the game mod does not tell (it is older than the chains of commands)
        self.__busy: bool | None = None
        self.__busy_time: float = 0.0
        # How many things the companion started have failed so far, as the game counts them
        self.__fail_count: int = 0
        self.__has_quick_polls: bool = False
        self.__releases_open_poll: bool = False
        # The merchant the AI looked at or traded with last (its ref_id), for buy and sell that name no merchant
        self.last_merchant: str = ""
        # The folder of the game, for reading its animations (see idle_search.py). Empty = not known
        self.game_folder: str = ""
        # The chain of commands that wait for each other (command_chain.CompanionChain), made by the MCP side when needed
        self.chain: Any = None

    # ------------------------------------------------------------------ game side

    async def poll(self, state: dict[str, Any] | None, results: list[dict[str, Any]] | None,
                   busy: Any = None, fails: Any = None, events: list[dict[str, Any]] | None = None,
                   menus: list[tuple[str, float]] | None = None) -> list[dict[str, Any]]:
        """Called for every long polling request of the game.

        Args:
            state: The current game state (see game_state.read_game_state), or None if this request carries none
            results: Results of commands the game has executed since its last request
            busy: 1 if the companion is in the middle of something that takes time, 0 if not, None if the game mod does not tell
            fails: How many things the companion started have failed so far, None if the game mod does not tell
            events: The urgent events since the request before (see game_state.read_events), or None
            menus: The menus that were open since the request before (see game_state.read_menus), or None. Only logged

        Returns:
            The commands the game should execute now. Empty if nothing came up within hold_seconds.
        """
        async with self.__changed:
            silent_seconds = self.__silent_seconds()
            if self.__is_poll_open:
                log.info("GAME sent a new request while the one before was still open (a save was loaded, or its watchdog hit).")
            elif silent_seconds is None:
                log.info("GAME first request since the server started.")
            elif silent_seconds >= self.SILENCE_LOG_SECONDS:
                log.info("GAME back after %.1f s of silence. Before that it was handed: %s", silent_seconds, self.__last_handed
                         or "nothing, its request had run out (so it stood still: a menu, its window not in front, a loading screen; or the answer got lost)")
            if menus:
                # Newer game mods tell which menus were open in the meantime, so a silence explains itself
                log.info("GAME says these menus were open: %s", ", ".join(describe_menu(name, seconds) for name, seconds in menus))
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
                # And for what happened in the game
                self.__keep_events(state.pop("events", None))
                # Only for the log: where the plugin of the game mod and its script tell different things
                mismatch = state.pop("plugin_mismatch", None)
                if mismatch:
                    log.info("GAME says its plugin and its script disagree: %s", shorten(str(mismatch)))
                self.__state = state
                self.__state_time = time.monotonic()
                self.__state_version += 1
                # The game mod measures itself how long collecting took, and how long of that the characters around took
                if state.get("quick") is True:
                    log.info("GAME sent its state (number %d), built in one go. It took the script %s ms, of that the plugin %s ms.",
                             self.__state_version, state.get("build_ms", "?"), state.get("scan_ms", "?"))
                else:
                    log.info("GAME sent its state (number %d). Collecting it took the script %s ms, of that the characters around %s ms.",
                             self.__state_version, state.get("build_ms", "?"), state.get("scan_ms", "?"))
            self.__keep_events(events)
            if isinstance(events, list) and len(events) > 0:
                log.info("GAME sent %d urgent events: %s", len(events), shorten(" | ".join(str(event.get("text", "")) for event in events if isinstance(event, dict))))
            self.__store_results(results)
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict):
                        log.info("GAME result of command %s: %s: %s", str(result.get(consts.KEY_COMMAND_ID, ""))[:8],
                                 "ok" if result.get(consts.KEY_RESULT_OK) else "failed", shorten(result.get(consts.KEY_RESULT_MESSAGE, "")))
            if isinstance(busy, int):
                if self.__busy != (busy != 0):
                    log.info("GAME says the companion is %s.", "busy" if busy != 0 else "not busy any more")
                self.__busy = busy != 0
                self.__busy_time = time.monotonic()
            if isinstance(fails, int) and not isinstance(fails, bool):
                if fails != self.__fail_count:
                    log.info("GAME counts %d things that failed (before: %d).", fails, self.__fail_count)
                self.__fail_count = fails
            self.__releases_open_poll = False
            self.__changed.notify_all()

            try:
                await self.__wait_until(
                    lambda: len(self.__pending_commands) > 0 or self.__poll_generation != own_generation or self.__releases_open_poll,
                    self.QUICK_HOLD_SECONDS if self.__has_quick_polls else self.__hold_seconds)
                if self.__poll_generation != own_generation:
                    # A newer request arrived (eg after loading a save). The newer one gets the commands.
                    return []
                self.__last_handed = ""
                commands = self.__pending_commands
                self.__pending_commands = []
                for command in commands:
                    self.__delivered_command_ids.add(command[consts.KEY_COMMAND_ID])
                self.__last_handed = "; ".join(self.__describe_command(command) for command in commands)
                return commands
            finally:
                if self.__poll_generation == own_generation:
                    self.__is_poll_open = False
                    self.__last_poll_time = time.monotonic()
                self.__changed.notify_all()

    # ------------------------------------------------------------------- MCP side

    async def send_command(self, action: str, target: str = "", detail: str = "", result_timeout_seconds: float = 8.0,
                           count: int = 1, item: str = "", things: bool = False, around_companion: bool = False,
                           stats: bool = False, visible: bool = False, space: bool = False, find: str = "",
                           talk: str = "", say_number: int = 0, face: int = consts.FACE_NONE,
                           run: bool = False, say_ms: int = 0, space_meters: int = 0,
                           space_degrees: int = consts.SPACE_NO_DIRECTION, space_from_facing: bool = False,
                           space_angle: int = consts.SPACE_FULL_CIRCLE_DEGREES,
                           extra: dict[str, Any] | None = None, waits_for_pause: bool = True,
                           pickup_seconds: float = 0.0) -> dict[str, Any]:
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
            space_meters: For report_state with space: how far the rays reach, 0 = the usual range
            space_degrees: For report_state with space: the direction the rays look into in degrees,
                SPACE_NO_DIRECTION = all around
            space_from_facing: Whether space_degrees counts from where the companion faces (otherwise from north)
            space_angle: For report_state with space and a direction: over how many degrees the rays fan out
            find: For report_state: a name, or a part of one, to look for in the whole loaded area
            talk: For say: the animation event that moves the hands of the companion while it talks, or empty
                (an animation that goes by an ID instead goes into 'item')
            say_number: For say, say_talk and say_end: the number of the sentence
            face: For say: the number of the look on the companion's face while it speaks, FACE_NONE = none
            run: For move_to, move, enter and follow: whether the companion runs instead of walking
            extra: More entries for the command as the game reads them (key -> value), for what only one action needs
            say_ms: For say: how long the sound of the sentence is in milliseconds
            things: For report_state: whether the state should carry the things close to the player.
                'count' is then how many characters (and things of each kind) the state should list at most
            waits_for_pause: Whether the command waits for a game that stands still (a menu, a loading screen) to go on,
                for up to PAUSE_WAIT_SECONDS. False = it only waits the short time the game needs to ask again
            result_timeout_seconds: How long to wait for the result. The game reports it right away,
                unless a request for the state came in together with the command

        Returns:
            A dictionary with 'status' (see companion_constants.STATUS_*) and 'message'
        """
        async with self.__changed:
            started = time.monotonic()
            silent_seconds = self.__silent_seconds()
            if silent_seconds is None:
                log.info("COMMAND %s: not sent, no game has asked for commands since the server started.", shorten(f"{action} {target} {detail}"))
                return self.__status(consts.STATUS_GAME_NOT_CONNECTED, "The game is not polling. Is Skyrim running with a loaded save?")
            if silent_seconds > self.PAUSE_LONGEST_SECONDS:
                log.info("COMMAND %s: not sent, the game has been silent for %.0f s.", shorten(f"{action} {target} {detail}"), silent_seconds)
                return self.__status(consts.STATUS_GAME_NOT_CONNECTED, describe_silence(silent_seconds) + " The command was not carried out.")

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
                consts.KEY_COMMAND_SPACE_METERS: space_meters,
                consts.KEY_COMMAND_SPACE_DEGREES: space_degrees,
                consts.KEY_COMMAND_SPACE_FACING: int(space_from_facing),
                consts.KEY_COMMAND_SPACE_ANGLE: space_angle,
                consts.KEY_COMMAND_FIND: find,
                consts.KEY_COMMAND_TALK: talk,
                consts.KEY_COMMAND_SAY_NUMBER: say_number,
                consts.KEY_COMMAND_FACE: face,
                consts.KEY_COMMAND_RUN: int(run),
                consts.KEY_COMMAND_SAY_MS: say_ms,
                consts.KEY_COMMAND_MUTE: int(self.mutes_game_lines),
            }
            if extra is not None:
                command.update(extra)
            label = self.__describe_command(command)
            if silent_seconds > self.__connection_grace_seconds:
                log.info("COMMAND %s: the game has been silent for %.1f s, waiting for it.", label, silent_seconds)
            self.__pending_commands.append(command)
            self.__changed.notify_all()

            # A game that stands still (a menu, a loading screen) is waited for. pickup_seconds: the caller's own wait,
            # for a game that is still at work on what it was handed before
            pickup_seconds = max(pickup_seconds, self.__connection_grace_seconds + 2.0)
            if waits_for_pause:
                pickup_seconds = max(pickup_seconds, self.__pause_wait_seconds)
            is_delivered = await self.__wait_until(lambda: command_id in self.__delivered_command_ids, pickup_seconds)
            if not is_delivered:
                self.__pending_commands = [c for c in self.__pending_commands if c is not command]
                log.info("COMMAND %s: NOT picked up after %.1f s. The game has been silent for %.1f s.", label, time.monotonic() - started,
                         self.__silent_seconds() or 0.0)
                if not waits_for_pause:
                    return self.__status(consts.STATUS_NOT_DELIVERED, "The game did not pick up the command in time.")
                if self.__last_handed != "":
                    # It took something before and has not come back: it is at work, not in a menu
                    return self.__status(consts.STATUS_NOT_DELIVERED, f"The game has been busy for {max(int(self.__silent_seconds() or 0.0), 1)} seconds with "
                                         "what it was told before and did not take this command. It was NOT carried out and will not be made up for. "
                                         "Try again in a moment.")
                return self.__status(consts.STATUS_NOT_DELIVERED, describe_silence(self.__silent_seconds() or 0.0)
                                     + " The command was NOT carried out and will not be made up for. Try again when the game goes on.")
            self.__delivered_command_ids.discard(command_id)
            log.info("COMMAND %s: picked up by the game after %.1f s.", label, time.monotonic() - started)

            if action == consts.ACTION_REPORT_STATE:
                return self.__status(consts.STATUS_DELIVERED, "")

            has_result = await self.__wait_until(lambda: command_id in self.__results, result_timeout_seconds)
            if not has_result:
                log.info("COMMAND %s: no result after %.1f s. The game has %s.", label, time.monotonic() - started,
                         "asked again since" if self.__is_poll_open else f"not asked again, silent for {self.__silent_seconds() or 0.0:.1f} s")
                if not self.__is_poll_open:
                    # The game mod asks again right after it got an answer, unless the game stands still or a command keeps it
                    return self.__status(consts.STATUS_DELIVERED, "The game received the command but has not reported a result yet: "
                                         "it is paused (the player may be in a menu) or busy. It carries the command out as soon as it goes on.")
                return self.__status(consts.STATUS_DELIVERED, "The game received the command but has not reported a result yet.")
            result = self.__results.pop(command_id)
            is_ok = bool(result.get(consts.KEY_RESULT_OK, False))
            message = str(result.get(consts.KEY_RESULT_MESSAGE, ""))
            log.info("COMMAND %s: result after %.1f s.", label, time.monotonic() - started)
            return self.__status(consts.STATUS_DONE if is_ok else consts.STATUS_FAILED, message)

    async def get_state(self, refresh_timeout_seconds: float = STATE_TIMEOUT_SECONDS, inventory: str = "", corpse: str = "",
                        is_for_the_ai: bool = True, things: bool = False, max_lines: int = DEFAULT_MAX_LINES,
                        around_companion: bool = False, stats: bool = False, visible: bool = False,
                        space: bool = False, find: str = "", space_meters: int = 0,
                        space_degrees: int = consts.SPACE_NO_DIRECTION, space_from_facing: bool = False,
                        space_angle: int = consts.SPACE_FULL_CIRCLE_DEGREES, space_direction: str = "",
                        inventory_filter: str = "", read: str = "", read_page: int = 1, merchant: str = "",
                        slow: bool = False) -> dict[str, Any]:
        """Returns the game state. If the game is connected, it is asked for a fresh state first

        Args:
            inventory: Whose inventory the fresh state should carry (companion_constants.INVENTORY_...), or empty for none
            inventory_filter: Only a part of that inventory (companion_constants.FILTER_FAVORITES or FILTER_EQUIPPED), or empty for all of it
            corpse: ref_id of a dead character or a container. The fresh state then carries what is in it, instead of 'inventory'
            merchant: ref_id of a merchant. The fresh state then carries what it offers, instead of 'inventory'
            slow: True = the game mod builds the state the old way, question by question, although its plugin could
                build it in one go (see companion_constants.STATE_OPTION_SLOW). For comparing the two ways
            things: Whether the fresh state should carry the things close to the player (furniture, containers, loose items)
            max_lines: How many characters, and how many things, the fresh state should list at most
            around_companion: Whether the fresh state should list everything as the companion sees it (what is around
                the companion, with distances and directions from it) instead of as the player sees it
            stats: Whether the fresh state should carry the level, the skills and so on of the player
            visible: Whether the fresh state should only list what the viewer can see
            space: Whether the fresh state should carry what is around the companion as rays find it (walls, drops)
            space_meters, space_degrees, space_from_facing, space_angle: How the rays should look (see send_command)
            space_direction: The word of the AI for the direction the rays look into, for the text of the answer (empty = none)
            find: A name, or a part of one, the game should look for in the whole loaded area (empty = no search)
            read: The ID of a book or a note the fresh state should carry a page of (empty = none), and which page (from 1)
            is_for_the_ai: False if the server itself needs the state (eg to pick a seat). Then what was heard, what
                happened and what became of earlier commands stays kept for the next time the AI asks

        Returns:
            A dictionary with 'connected', 'silent_seconds', 'fresh', 'state_age_seconds', 'state', 'inventory_wanted', 'corpse_wanted',
            'things_wanted', 'max_lines', 'around_wanted', 'stats_wanted', 'visible_wanted', 'space_wanted', 'find_wanted',
            'read_wanted', 'heard' (what the characters in the game said since this was called the last time),
            'events' (what happened in the game since then) and 'reports' (what became of loot and move_to since then)
        """
        if corpse != "":
            inventory = consts.INVENTORY_CORPSE
            merchant = ""
        elif merchant != "":
            inventory = consts.INVENTORY_MERCHANT
        if inventory != "":
            refresh_timeout_seconds = max(refresh_timeout_seconds, self.INVENTORY_TIMEOUT_SECONDS)
        async with self.__changed:
            version_before = self.__state_version
            is_connected = self.__is_game_connected()

        is_fresh = False
        if is_connected:
            extra: dict[str, Any] = {}
            if inventory_filter != "":
                extra[consts.KEY_COMMAND_INVENTORY_FILTER] = inventory_filter
            read_high, read_low = split_form_id(parse_ref_id(read) or 0)
            if read != "":
                extra.update({consts.KEY_COMMAND_READ: read, consts.KEY_COMMAND_READ_HIGH: read_high,
                              consts.KEY_COMMAND_READ_LOW: read_low, consts.KEY_COMMAND_READ_PAGE: read_page})
            # All of it once more as one list of numbers, which newer game mods read instead (see KEY_COMMAND_STATE_OPTIONS)
            target_high, target_low = split_form_id(parse_ref_id(corpse or merchant) or 0)
            extra[consts.KEY_COMMAND_STATE_OPTIONS] = [
                max_lines, int(things), int(around_companion), int(stats), int(visible), int(space), space_meters, space_degrees,
                int(space_from_facing), space_angle, read_high, read_low, read_page, int(self.mutes_game_lines), int(slow),
                target_high, target_low, int(inventory != ""), int(find != ""), int(inventory_filter != ""), int(read != "")]
            request = await self.send_command(consts.ACTION_REPORT_STATE, target=corpse or merchant, detail=inventory, count=max_lines, things=things,
                                              around_companion=around_companion, stats=stats, visible=visible, space=space,
                                              find=find, space_meters=space_meters, space_degrees=space_degrees,
                                              space_from_facing=space_from_facing, space_angle=space_angle,
                                              extra=extra, waits_for_pause=False,
                                              pickup_seconds=refresh_timeout_seconds)
            # Only a game that picked up the request will send a fresh state for it
            if request["status"] == consts.STATUS_DELIVERED:
                async with self.__changed:
                    is_fresh = await self.__wait_until(lambda: self.__state_version > version_before, refresh_timeout_seconds)

        async with self.__changed:
            age: float | None = None
            if self.__state is not None:
                age = round(time.monotonic() - self.__state_time, 1)
            if not is_fresh:
                log.info("STATE got no fresh state: the game %s, the state it has is %s s old.",
                         "is connected" if self.__is_game_connected() else f"has been silent for {self.__silent_seconds() or 0.0:.1f} s"
                         if self.__silent_seconds() is not None else "never asked for commands", age)
            return {
                "connected": self.__is_game_connected(),
                # For how long the game has not asked for commands (0 = it is asking right now, None = it never did)
                "silent_seconds": self.__silent_seconds(),
                "fresh": is_fresh,
                "state_age_seconds": age,
                "state": self.__state,
                # The kept state may still carry an inventory from an earlier question. Only a fresh one is worth showing
                "inventory_wanted": inventory if is_fresh else "",
                "inventory_filter": inventory_filter,
                "inventory_missed": inventory != "" and not is_fresh,
                "corpse_wanted": corpse if is_fresh else "",
                "merchant_wanted": merchant if is_fresh else "",
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
                # How the AI wanted the rays to look. How they did look is in the state itself ('space_look')
                "space_direction_wanted": space_direction,
                "space_meters_wanted": space_meters,
                # What the search was for. Whether the game did it is in the state itself ('found_for')
                "find_wanted": find if is_fresh else "",
                "find_missed": find != "" and not is_fresh,
                # The kept state may still carry a page from an earlier question
                "read_wanted": read if is_fresh else "",
                "read_missed": read != "" and not is_fresh,
                "heard": self.__take_heard() if is_for_the_ai else [],
                "events": self.__take_events() if is_for_the_ai else [],
                "reports": self.__take_reports() if is_for_the_ai else [],
            }

    @property
    def is_busy_known(self) -> bool:
        """Whether the game mod tells if the companion is busy (older ones do not, so chains of commands cannot work)"""
        return self.__busy is not None

    @property
    def fail_count(self) -> int:
        """How many things the companion started have failed so far, as the game counted them with its last request"""
        return self.__fail_count

    async def set_quick_polls(self, is_wanted: bool) -> None:
        """Makes the game ask every second instead of waiting for a command, so that it tells soon when the companion
        is done with something. A request that is open at the moment is answered at once"""
        async with self.__changed:
            self.__has_quick_polls = is_wanted
            if is_wanted:
                self.__releases_open_poll = True
            self.__changed.notify_all()

    async def wait_until_idle(self, timeout_seconds: float) -> bool:
        """Waits until the game says that the companion is not busy any more. What the game said counts only if it
        said so just now, so the next request of the game is waited for otherwise

        Returns:
            False if the companion was still busy (or the game did not tell) when the time was up
        """
        async with self.__changed:
            return await self.__wait_until(
                lambda: self.__busy is False and time.monotonic() - self.__busy_time <= self.BUSY_FRESH_SECONDS,
                timeout_seconds)

    async def add_report(self, report: str) -> None:
        """Keeps a sentence for the AI until it asks for the state the next time (under 'Since your last STATE')"""
        async with self.__changed:
            self.__reports = (self.__reports + [report])[-self.MAX_REPORTS:]

    async def take_urgent_events(self) -> list[dict[str, Any]]:
        """The urgent events the AI has not been told yet (a fight begins, someone dies), oldest first, for the answer
        to a COMMAND. They are forgotten afterwards, the other events stay for the next STATE"""
        async with self.__changed:
            now = time.monotonic()
            urgent = [event for event in self.__events if event["urgent"] and now - event["time"] <= self.MAX_EVENT_AGE_SECONDS]
            self.__events = [event for event in self.__events if not event["urgent"]]
            return [self.__describe_event(event, now) for event in urgent[-self.MAX_URGENT_EVENTS:]]

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
        silent_seconds = time.monotonic() - self.__last_poll_time
        if silent_seconds <= self.__connection_grace_seconds:
            return True
        # At work on what it picked up with its last request (see WORK_SECONDS)
        return self.__last_handed != "" and silent_seconds <= self.WORK_SECONDS

    @staticmethod
    def __describe_command(command: dict[str, Any]) -> str:
        """A command as the log file names it: the start of its ID, then action, target and detail"""
        words = [str(command.get(key, "") or "") for key in (consts.KEY_COMMAND_ACTION, consts.KEY_COMMAND_TARGET, consts.KEY_COMMAND_DETAIL)]
        return shorten(str(command.get(consts.KEY_COMMAND_ID, ""))[:8] + " " + " ".join(word for word in words if word != ""), 120)

    def __silent_seconds(self) -> float | None:
        """For how long the game has not asked for commands (0 = a request of it is open, None = it never asked)"""
        if self.__is_poll_open:
            return 0.0
        if self.__last_poll_time is None:
            return None
        return time.monotonic() - self.__last_poll_time

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

    def __keep_events(self, events: Any):
        if not isinstance(events, list):
            return
        now = time.monotonic()
        for event in events:
            if not isinstance(event, dict):
                continue
            self.__events.append({"time": now - float(event.get("seconds_ago", 0)), "game_time": str(event.get("game_time", "")),
                                  "urgent": bool(event.get("urgent", False)), "text": str(event.get("text", ""))})
        # Urgent ones come with an earlier request than what happened before them, so the order is put right here.
        # Bounded even if the AI never asks
        self.__events = sorted(self.__events, key=lambda event: event["time"])[-self.MAX_STORED_EVENTS:]

    def __take_events(self) -> list[dict[str, Any]]:
        """What happened and the AI has not been told yet, oldest first. Forgotten afterwards"""
        now = time.monotonic()
        events = [event for event in self.__events if now - event["time"] <= self.MAX_EVENT_AGE_SECONDS]
        self.__events = []
        return [self.__describe_event(event, now) for event in events[-self.MAX_EVENT_LINES:]]

    @staticmethod
    def __describe_event(event: dict[str, Any], now: float) -> dict[str, Any]:
        return {"seconds_ago": max(0, round(now - event["time"])), "game_time": event["game_time"],
                "urgent": event["urgent"], "text": event["text"]}

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
