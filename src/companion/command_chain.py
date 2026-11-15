"""Commands of the AI that wait for each other: the next one only starts when the companion is done with the one before.

The game runs every command at once and only says that it started. So the chain lives here in the server:
it sends a step, waits until the game says that the companion is not busy any more (see CompanionCommandQueue.poll),
and then sends the next one. A step that fails is skipped and the chain goes on with the next one.
Only if a step takes too long or the game stops answering, the steps behind it are dropped.
"""
import asyncio
from typing import Any, Awaitable, Callable

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts

# How many steps can wait at most, how long one step may take, and how long a pause can be
MAX_WAITING_STEPS: int = 5
STEP_TIMEOUT_SECONDS: float = 120.0
HIGHEST_PAUSE_SECONDS: int = 60
MAX_SHOWN_TEXT_LENGTH: int = 30
# What the chain waits for when it does not know what the companion is doing at the moment
UNKNOWN_STEP_TEXT: str = "what you are doing"


def describe_step(step: dict[str, Any]) -> str:
    """A step as a few words for the AI: 'move_to 000A2C94', 'gesture dance', 'say "Hello there"'"""
    action = str(step.get("action", "")).strip().lower()
    target = str(step.get("target", "") or "").strip()
    detail = " ".join(str(step.get("detail", "") or "").split())
    if len(detail) > MAX_SHOWN_TEXT_LENGTH:
        detail = detail[:MAX_SHOWN_TEXT_LENGTH] + "..."
    if action == consts.ACTION_SAY and detail != "":
        detail = f'"{detail}"'
    return " ".join(text for text in [action, target, detail] if text != "")


def read_pause_seconds(step: dict[str, Any]) -> int | None:
    """How many seconds a pause step waits, or None if its 'detail' is not a number from 1 to HIGHEST_PAUSE_SECONDS"""
    words = str(step.get("detail", "") or "").lower().split()
    if len(words) == 0:
        return None
    number = words[0].removesuffix("s")
    if not number.isdecimal() or not 1 <= int(number) <= HIGHEST_PAUSE_SECONDS:
        return None
    return int(number)


class CompanionChain:
    """The steps that wait, and the task that sends them one after the other"""

    def __init__(self, queue: CompanionCommandQueue,
                 run_step: Callable[[dict[str, Any]], Awaitable[tuple[dict[str, Any], bool, float]]]) -> None:
        """
        Args:
            run_step: Sends one step to the game like a command of the AI. Returns what the game answered, whether
                that is an error, and how many seconds the step goes on after the answer (a sentence that is spoken)
        """
        self.__queue: CompanionCommandQueue = queue
        self.__run_step = run_step
        self.__waiting: list[dict[str, Any]] = []
        # What the companion does now and the chain waits for, as a few words (empty = the chain is not running)
        self.__current: str = ""
        self.__task: asyncio.Task | None = None
        self.__fails_before: int = 0
        # Whether what the companion does now can still go wrong after it started (a walk, a ride, looting).
        # A gesture or a sentence cannot: a failure the game tells of meanwhile belongs to something earlier
        self.__can_current_fail: bool = True

    @property
    def is_active(self) -> bool:
        return self.__task is not None and not self.__task.done()

    @property
    def waiting_count(self) -> int:
        return len(self.__waiting)

    def describe(self) -> str:
        """What runs and what waits as one line for the AI, or empty if nothing waits"""
        if not self.is_active:
            return ""
        parts = [f"now {self.__current}"] if self.__current != "" else []
        parts.extend(f"then {describe_step(step)}" for step in self.__waiting)
        return "; ".join(parts)

    async def clear(self) -> list[str]:
        """Drops what waits and stops waiting. Returns the steps that were dropped, as words for the AI"""
        dropped = [describe_step(step) for step in self.__waiting]
        self.__waiting = []
        self.__current = ""
        if self.__task is not None:
            self.__task.cancel()
            self.__task = None
            await self.__queue.set_quick_polls(False)
        return dropped

    def add(self, steps: list[dict[str, Any]], running_text: str = "", fails_before: int | None = None,
            first_wait_seconds: float = 0.0, can_running_fail: bool = True) -> None:
        """Puts steps at the end of the chain and starts the chain if it is not running.

        Args:
            running_text: What the companion is doing right now, which the first step waits for (only for a new chain)
            fails_before: The fail count of the game from before that was started (only for a new chain)
            first_wait_seconds: How long what the companion is doing goes on for sure (a sentence it speaks)
            can_running_fail: Whether what the companion is doing right now can still go wrong (see __can_current_fail)
        """
        self.__waiting.extend(steps)
        if self.is_active:
            return
        self.__current = running_text if running_text != "" else UNKNOWN_STEP_TEXT
        self.__fails_before = fails_before if fails_before is not None else self.__queue.fail_count
        self.__can_current_fail = can_running_fail
        self.__task = asyncio.create_task(self.__run(first_wait_seconds))

    async def __run(self, first_wait_seconds: float) -> None:
        own_task = asyncio.current_task()
        done: list[str] = []
        is_any_skipped = False
        try:
            await self.__queue.set_quick_polls(True)
            wait_seconds = first_wait_seconds
            while True:
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                if not await self.__queue.wait_until_idle(STEP_TIMEOUT_SECONDS):
                    await self.__give_up(f"'{self.__current}' took longer than {int(STEP_TIMEOUT_SECONDS)} seconds, or the game stopped answering")
                    return
                if self.__queue.fail_count > self.__fails_before:
                    if self.__can_current_fail:
                        # It had started, so it counted as done until now
                        if len(done) > 0 and done[-1] == self.__current:
                            done.pop()
                        await self.__skip(f"'{self.__current}' did not work out")
                        is_any_skipped = True
                    # Otherwise the game told of a failure of something earlier, in its own words (see the reports
                    # of the game under 'Since your last STATE'). What runs now has started and is fine
                    self.__fails_before = self.__queue.fail_count
                if len(self.__waiting) == 0:
                    break
                step = self.__waiting.pop(0)
                self.__current = describe_step(step)
                wait_seconds = 0.0
                if str(step.get("action", "")).strip().lower() == consts.ACTION_PAUSE:
                    await asyncio.sleep(read_pause_seconds(step) or 0)
                    done.append(self.__current)
                    continue
                self.__fails_before = self.__queue.fail_count
                self.__can_current_fail = str(step.get("action", "")).strip().lower() not in consts.ACTIONS_KEEPING_QUEUE
                payload, is_error, wait_seconds = await self.__run_step(step)
                if is_error:
                    await self.__skip(f"'{self.__current}' failed ({payload.get('message', '')})")
                    is_any_skipped = True
                    # Nothing new runs, so there is nothing to wait for but what the companion did before
                    self.__current = UNKNOWN_STEP_TEXT
                    self.__can_current_fail = False
                    continue
                done.append(self.__current)
            if len(done) > 0 and is_any_skipped:
                await self.__queue.add_report("Queue: finished. Done: " + ", then ".join(done) + ".")
            elif len(done) > 0:
                await self.__queue.add_report("Queue: all done (" + ", then ".join(done) + ").")
        finally:
            # A chain that was cleared has handed everything over already (see clear)
            if self.__task is own_task:
                self.__task = None
                self.__current = ""
                await self.__queue.set_quick_polls(False)

    async def __skip(self, reason: str) -> None:
        """A step failed: the AI is told, and the chain goes on with what waits behind it"""
        report = f"Queue: {reason}, so it was skipped."
        if len(self.__waiting) > 0:
            report += " The rest goes on: " + ", ".join(describe_step(step) for step in self.__waiting) + "."
        await self.__queue.add_report(report)

    async def __give_up(self, reason: str) -> None:
        dropped = [describe_step(step) for step in self.__waiting]
        self.__waiting = []
        report = f"Queue: {reason}."
        if len(dropped) > 0:
            report += " So what waited behind it was dropped: " + ", ".join(dropped) + "."
        await self.__queue.add_report(report)
