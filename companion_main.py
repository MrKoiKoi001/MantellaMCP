"""Start point for the AI companion server (instead of main.py).

Starts only what the companion needs: the long polling route for the game and the MCP route for the AI.
Nothing of Mantella's speech-to-text, LLM or text-to-speech code is loaded.

Run from the repo root:  python companion_main.py

The settings (folder of the game, folder of Piper, voice from Piper or from an XTTS server, lip movement on or off)
are in companion.ini next to this file.
The server makes that file with its notes when it is missing (see src/companion/settings.py).
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
from src.companion.lips import LipSync
from src.companion.settings import SETTINGS_FILE, VOICE_XTTS, load_settings
from src.companion.voice import CompanionVoice
from src.companion.xtts import XttsVoice

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
    parser.add_argument("--settings", default=SETTINGS_FILE, help="File with the settings (default: %(default)s). It is made if it is missing")
    parser.add_argument("--game-folder", default="", help="Folder of the game, for the voice of the companion. Counts instead of the one in the settings")
    parser.add_argument("--piper-folder", default="", help="Folder with piper.exe from the Mantella download. Counts instead of the one in the settings")
    args = parser.parse_args()

    token = load_or_create_token(TOKEN_FILE)
    settings, settings_source = load_settings(args.settings)
    game_folder = args.game_folder or settings.game_folder
    lips = LipSync(game_folder, settings.lipgen_folder, settings.facefx_folder, settings.fonix_file) if settings.lips else None
    xtts = XttsVoice(settings.xtts_url, settings.xtts_voice, settings.xtts_language) if settings.voice == VOICE_XTTS else None
    xtts_start = xtts.start_server(settings.xtts_start) if xtts is not None and settings.xtts_start != "" else ""
    voice = CompanionVoice(game_folder, args.piper_folder or settings.piper_folder, lips=lips, xtts=xtts,
                           volume_percent=settings.voice_volume, talk_gestures=settings.talk_gestures)
    lip_files_change = voice.prepare_lip_files()
    app = create_app(token, args.hold_seconds, voice)

    print("")
    print("AI companion server")
    print(f"  Game route (this machine only): http://localhost:{args.port}{consts.ROUTE_GAME}")
    print(f"  MCP route:                      http://localhost:{args.port}{consts.ROUTE_MCP_PREFIX}{token}")
    print(f"  For the AI app, put the tunnel address in front: https://<tunnel-address>{consts.ROUTE_MCP_PREFIX}{token}")
    print(f"  Settings: {settings_source}")
    if xtts_start != "":
        print(f"  XTTS server: {xtts_start}")
    print(f"  Voice of the companion (command say): {voice.describe_setup()}")
    print(f"  Volume of the voice: {settings.voice_volume} %, hands move while talking: {'on' if settings.talk_gestures else 'off'}")
    print(f"  Lips of the companion: {voice.describe_lips()}")
    if lip_files_change != "":
        print(f"  Lip files: {lip_files_change}")
    print("")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
