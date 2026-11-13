"""The settings of the companion server. They are kept in a file next to the server (companion.ini),
so that nobody has to type them when starting it.

The file is made with its notes the first time the server starts. A value that is left empty stands for
what the server would take on its own. What is given when starting the server (eg --game-folder) counts first.
"""
import configparser
import os
from dataclasses import dataclass

from src.companion.voice import DEFAULT_GAME_FOLDER, DEFAULT_PIPER_FOLDER

SETTINGS_FILE: str = "companion.ini"
SECTION: str = "companion"
KEY_GAME_FOLDER: str = "game_folder"
KEY_PIPER_FOLDER: str = "piper_folder"
KEY_LIPS: str = "lips"
KEY_LIPGEN_FOLDER: str = "lipgen_folder"
KEY_FACEFX_FOLDER: str = "facefx_folder"
KEY_FONIX_FILE: str = "fonix_file"
ON_WORDS: list[str] = ["on", "yes", "true", "1"]

DEFAULT_TEXT: str = f"""; Settings of the AI companion server. The server reads them when it starts.
; A value that is left empty stands for what is written in the note above it.

[{SECTION}]

; The folder of the game, for the voice of the companion (command say).
; Empty: {DEFAULT_GAME_FOLDER}
{KEY_GAME_FOLDER} =

; The folder with piper.exe from the Mantella download, which makes the voice.
; Empty: the folder "piper" next to the server
{KEY_PIPER_FOLDER} =

; on = the lips of the companion move while it speaks. That takes a moment longer for every sentence.
; off = the mouth stays shut.
; After changing this, start the server first and then the game: the game only finds lip files that are there when it starts.
{KEY_LIPS} = off

; The lip movement is made by one of two programs. The server takes the first one it finds:
; 1. LipGenerator.exe of the Creation Kit, in <{KEY_LIPGEN_FOLDER}>\\LipGenerator
;    Empty: <game folder>\\Tools\\LipGen
{KEY_LIPGEN_FOLDER} =

; 2. FaceFXWrapper.exe (it comes with Mantella's game mod, in Sound\\Voice\\Processing). The folder it is in.
;    Empty: <game folder>\\Data\\Sound\\Voice\\Processing
{KEY_FACEFX_FOLDER} =

;    FaceFXWrapper.exe needs the file FonixData.cdf of the Creation Kit (Data\\Sound\\Voice\\Processing of the game
;    the Creation Kit was installed for). The file with its folder.
;    Empty: FonixData.cdf in the folder of FaceFXWrapper.exe
{KEY_FONIX_FILE} =
"""


@dataclass
class CompanionSettings:
    game_folder: str = DEFAULT_GAME_FOLDER
    piper_folder: str = DEFAULT_PIPER_FOLDER
    lips: bool = False
    lipgen_folder: str = ""
    facefx_folder: str = ""
    fonix_file: str = ""


def load_settings(file: str = SETTINGS_FILE) -> tuple[CompanionSettings, str]:
    """Reads the settings. A file that is missing is made with its notes, and then everything is as the notes say.

    Returns:
        The settings, and one line for the start of the server that says where they came from
    """
    settings = CompanionSettings()
    if not os.path.isfile(file):
        try:
            with open(file, "w", encoding="utf-8") as opened:
                opened.write(DEFAULT_TEXT)
        except OSError as error:
            return settings, f"{file} could not be made ({error}), so nothing is set"
        return settings, f"{os.path.abspath(file)} (new, made just now)"

    # No interpolation: a folder may hold a % sign
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(file, "r", encoding="utf-8-sig") as opened:
            parser.read_file(opened)
    except (OSError, configparser.Error, UnicodeDecodeError) as error:
        return settings, f"{file} could not be read ({error}), so nothing is set"
    if not parser.has_section(SECTION):
        return settings, f"{file} has no part [{SECTION}], so nothing is set"

    def read(key: str) -> str:
        # Someone who copies a folder out of the Explorer gets it with quotation marks around it
        return parser.get(SECTION, key, fallback="").strip().strip('"').strip()

    settings.game_folder = read(KEY_GAME_FOLDER) or DEFAULT_GAME_FOLDER
    settings.piper_folder = read(KEY_PIPER_FOLDER) or DEFAULT_PIPER_FOLDER
    settings.lips = read(KEY_LIPS).lower() in ON_WORDS
    settings.lipgen_folder = read(KEY_LIPGEN_FOLDER)
    settings.facefx_folder = read(KEY_FACEFX_FOLDER)
    settings.fonix_file = read(KEY_FONIX_FILE)
    return settings, os.path.abspath(file)
