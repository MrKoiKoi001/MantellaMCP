import asyncio

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts


def run(coroutine):
    return asyncio.run(coroutine)


def test_poll_returns_empty_after_hold_time():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.1)
        return await queue.poll({"location": "Whiterun"}, [])
    assert run(scenario()) == []


def test_command_fails_when_game_is_not_polling():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.1)
        return await queue.send_command(consts.ACTION_FOLLOW)
    assert run(scenario())["status"] == consts.STATUS_GAME_NOT_CONNECTED


def test_command_is_delivered_to_open_poll_and_result_comes_back():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=5)

        async def game():
            commands = await queue.poll({"location": "Whiterun"}, [])
            results = [{consts.KEY_COMMAND_ID: commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: True, consts.KEY_RESULT_MESSAGE: "ok"}]
            next_poll = asyncio.create_task(queue.poll({"location": "Whiterun"}, results))
            return commands, next_poll

        game_task = asyncio.create_task(game())
        await asyncio.sleep(0.05)
        result = await queue.send_command(consts.ACTION_ATTACK, "FF000A01")
        commands, next_poll = await game_task
        next_poll.cancel()
        return result, commands

    result, commands = run(scenario())
    assert result == {"status": consts.STATUS_DONE, "message": "ok"}
    assert commands[0][consts.KEY_COMMAND_ACTION] == consts.ACTION_ATTACK
    assert commands[0][consts.KEY_COMMAND_TARGET] == "FF000A01"


def test_failed_result_is_reported_as_failed():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=5)

        async def game():
            commands = await queue.poll(None, [])
            results = [{consts.KEY_COMMAND_ID: commands[0][consts.KEY_COMMAND_ID], consts.KEY_RESULT_OK: False, consts.KEY_RESULT_MESSAGE: "Target not found"}]
            return asyncio.create_task(queue.poll(None, results))

        game_task = asyncio.create_task(game())
        await asyncio.sleep(0.05)
        result = await queue.send_command(consts.ACTION_ATTACK, "FF000A01")
        (await game_task).cancel()
        return result

    assert run(scenario()) == {"status": consts.STATUS_FAILED, "message": "Target not found"}


def test_get_state_asks_game_for_fresh_state():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=5)

        async def game():
            first_commands = await queue.poll({"location": "Whiterun"}, [])
            next_poll = asyncio.create_task(queue.poll({"location": "Riverwood"}, []))
            return first_commands, next_poll

        game_task = asyncio.create_task(game())
        await asyncio.sleep(0.05)
        state = await queue.get_state()
        first_commands, next_poll = await game_task
        next_poll.cancel()
        return state, first_commands

    state, first_commands = run(scenario())
    assert first_commands[0][consts.KEY_COMMAND_ACTION] == consts.ACTION_REPORT_STATE
    assert state["connected"] is True
    assert state["fresh"] is True
    assert state["state"] == {"location": "Riverwood"}


def test_get_state_without_game_returns_not_connected():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=0.1)
        return await queue.get_state()
    state = run(scenario())
    assert state["connected"] is False
    assert state["state"] is None


def test_newer_poll_replaces_older_poll():
    async def scenario():
        queue = CompanionCommandQueue(hold_seconds=5)
        older_poll = asyncio.create_task(queue.poll(None, []))
        await asyncio.sleep(0.05)
        newer_poll = asyncio.create_task(queue.poll(None, []))
        older_commands = await asyncio.wait_for(older_poll, 1)
        await asyncio.sleep(0.05)
        result_task = asyncio.create_task(queue.send_command(consts.ACTION_WAIT, result_timeout_seconds=0.1))
        newer_commands = await asyncio.wait_for(newer_poll, 1)
        result = await result_task
        return older_commands, newer_commands, result

    older_commands, newer_commands, result = run(scenario())
    assert older_commands == []
    assert newer_commands[0][consts.KEY_COMMAND_ACTION] == consts.ACTION_WAIT
    assert result["status"] == consts.STATUS_DELIVERED
