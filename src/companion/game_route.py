from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.game_state import read_game_state

# Headers that Cloudflare (and other reverse proxies) add to every request they forward.
# The game always calls from this machine directly, so a request carrying one of these came from outside.
FORWARDING_HEADERS: list[str] = ["cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-forwarded-proto", "forwarded"]


def add_game_route(app: FastAPI, queue: CompanionCommandQueue):
    """Adds the long polling route that the game (Papyrus via SKSE_HTTP) calls

    Important for the SKSE_HTTP plugin:
    - Every status other than 200 raises the error event in the game, so 'nothing to do' is a normal 200 reply
    - A reply that is not valid JSON raises no event at all, so every reply is a JSON object
    - The game ignores every reply that does not carry the poll id of its newest request, so the id is sent back

    The game only sends its state when it was asked for it (report_state), and in a short form (see game_state.py).
    """
    @app.post(consts.ROUTE_GAME)
    async def companion(request: Request):
        for header in FORWARDING_HEADERS:
            if header in request.headers:
                return JSONResponse(status_code=403, content={"error": "This route is only available on this machine."})

        body: Any = None
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            body = {}

        commands = await queue.poll(read_game_state(body.get(consts.KEY_STATE)), body.get(consts.KEY_RESULTS))

        reply: dict[str, Any] = {consts.KEY_MANTELLA_REPLY_TYPE: consts.MANTELLA_REPLY_TYPE_IGNORED}
        poll_id = body.get(consts.KEY_POLL_ID)
        if isinstance(poll_id, int) and not isinstance(poll_id, bool):
            reply[consts.KEY_POLL_ID] = poll_id
        if len(commands) > 0:
            reply[consts.KEY_REPLY_TYPE] = consts.REPLY_TYPE_COMMANDS
            reply[consts.KEY_COMMANDS] = commands
        else:
            reply[consts.KEY_REPLY_TYPE] = consts.REPLY_TYPE_IDLE
        return reply
