"""Start point for the AI companion server (instead of main.py).

Starts only what the companion needs: the long polling route for the game and the MCP route for the AI.
Nothing of Mantella's speech-to-text, LLM or text-to-speech code is loaded.

Run from the repo root:  python companion_main.py
"""
import argparse
import os
import secrets

from fastapi import FastAPI
import uvicorn

from src.companion.command_queue import CompanionCommandQueue
from src.companion.companion_constants import companion_constants as consts
from src.companion.game_route import add_game_route
from src.companion.mcp_route import add_mcp_route
from src.companion.voice import DEFAULT_GAME_FOLDER, DEFAULT_PIPER_FOLDER, CompanionVoice

TOKEN_FILE: str = "companion_token.txt"
DEFAULT_PORT: int = 4998 # Mantella itself uses 4999
DEFAULT_HOLD_SECONDS: float = 25.0


def load_or_create_token(token_file: str) -> str:
    """The token is the secret part of the MCP URL. It is created once and then reused, so the URL stays the same"""
    if os.path.isfile(token_file):
        with open(token_file, "r", encoding="utf-8") as file:
            token = file.read().strip()
        if token != "":
            return token
    token = secrets.token_urlsafe(24)
    with open(token_file, "w", encoding="utf-8") as file:
        file.write(token)
    return token


def create_app(token: str, hold_seconds: float = DEFAULT_HOLD_SECONDS, voice: CompanionVoice | None = None) -> FastAPI:
    """
    Args:
        voice: Makes the sound for the command say (see src/companion/voice.py). Without it the companion cannot speak
    """
    app = FastAPI()
    queue = CompanionCommandQueue(hold_seconds=hold_seconds)
    add_game_route(app, queue)
    add_mcp_route(app, queue, token, voice)
    return app


def main():
    parser = argparse.ArgumentParser(description="AI companion server for Skyrim")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port for the game and the MCP route (default {DEFAULT_PORT})")
    parser.add_argument("--hold-seconds", type=float, default=DEFAULT_HOLD_SECONDS, help="How long a request of the game is held open while there is no command")
    parser.add_argument("--game-folder", default=DEFAULT_GAME_FOLDER, help="Folder of the game, for the voice of the companion (default: %(default)s)")
    parser.add_argument("--piper-folder", default=DEFAULT_PIPER_FOLDER, help="Folder with piper.exe from the Mantella download, for the voice of the companion (default: %(default)s)")
    args = parser.parse_args()

    token = load_or_create_token(TOKEN_FILE)
    voice = CompanionVoice(args.game_folder, args.piper_folder)
    app = create_app(token, args.hold_seconds, voice)

    print("")
    print("AI companion server")
    print(f"  Game route (this machine only): http://localhost:{args.port}{consts.ROUTE_GAME}")
    print(f"  MCP route:                      http://localhost:{args.port}{consts.ROUTE_MCP_PREFIX}{token}")
    print(f"  For the AI app, put the tunnel address in front: https://<tunnel-address>{consts.ROUTE_MCP_PREFIX}{token}")
    print(f"  Voice of the companion (command say): {voice.describe_setup()}")
    print("")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
