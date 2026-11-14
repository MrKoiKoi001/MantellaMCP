"""The settings of the companion server. They are kept in a file next to the server (companion.ini),
so that nobody has to type them when starting it.

The file is made with its notes the first time the server starts. A value that is left empty stands for
what the server would take on its own. What is given when starting the server (eg --game-folder) counts first.
"""
import configparser
import os
from dataclasses import dataclass

from src.companion.voice import DEFAULT_GAME_FOLDER, DEFAULT_PIPER_FOLDER
from src.companion.xtts import DEFAULT_XTTS_LANGUAGE, DEFAULT_XTTS_URL, DEFAULT_XTTS_VOICE

SETTINGS_FILE: str = "companion.ini"
SECTION: str = "companion"
KEY_GAME_FOLDER: str = "game_folder"
KEY_VR: str = "vr"
KEY_GAME_FOLDER_SE: str = "game_folder_se"
DEFAULT_GAME_FOLDER_SE: str = r"E:\SteamLibrary\steamapps\common\Skyrim Special Edition"
OFF_WORDS: list[str] = ["off", "no", "false", "0"]
KEY_PIPER_FOLDER: str = "piper_folder"
KEY_VOICE: str = "voice"
KEY_XTTS_URL: str = "xtts_url"
KEY_XTTS_VOICE: str = "xtts_voice"
KEY_XTTS_LANGUAGE: str = "xtts_language"
KEY_XTTS_START: str = "xtts_start"
KEY_VOICE_VOLUME: str = "voice_volume"
KEY_TALK_GESTURES: str = "talk_gestures"
KEY_MUTE_GAME_LINES: str = "mute_game_lines"
DEFAULT_VOICE_VOLUME: int = 100
LOWEST_VOICE_VOLUME: int = 10
HIGHEST_VOICE_VOLUME: int = 400
VOICE_PIPER: str = "piper"
VOICE_XTTS: str = "xtts"
KEY_LIPS: str = "lips"
KEY_LIPGEN_FOLDER: str = "lipgen_folder"
KEY_FACEFX_FOLDER: str = "facefx_folder"
KEY_FONIX_FILE: str = "fonix_file"
ON_WORDS: list[str] = ["on", "yes", "true", "1"]

DEFAULT_TEXT: str = f"""; Settings of the AI companion server. The server reads them when it starts.
; A value that is left empty stands for what is written in the note above it.

[{SECTION}]

; Which game the companion plays in. The server and build.py of the game mod both go by this.
; on = Skyrim VR, in the folder {KEY_GAME_FOLDER}
; off = Skyrim Special Edition (without VR), in the folder {KEY_GAME_FOLDER_SE}
; After changing this: run build.py --install of the game mod and start the server anew.
{KEY_VR} = on

; The folder of Skyrim VR.
; Empty: {DEFAULT_GAME_FOLDER}
{KEY_GAME_FOLDER} =

; The folder of Skyrim Special Edition.
; Empty: {DEFAULT_GAME_FOLDER_SE}
{KEY_GAME_FOLDER_SE} =

; The folder with piper.exe from the Mantella download, which makes the voice.
; Empty: the folder "piper" next to the server
{KEY_PIPER_FOLDER} =

; What makes the voice of the companion:
; {VOICE_PIPER} = Piper, with the voice the character has in the game. Quick, but it only pronounces English.
; {VOICE_XTTS} = an XTTS server that runs next to this one (start it first). It speaks other languages too, German among them,
;        and takes a few seconds for every sentence. If it does not answer, Piper speaks instead.
; After changing this, start the server anew and connect the AI anew: it is told which language to write in.
; Empty: {VOICE_PIPER}
{KEY_VOICE} =

; Only for {KEY_VOICE} = {VOICE_XTTS}: where the XTTS server listens.
; Empty: {DEFAULT_XTTS_URL}
{KEY_XTTS_URL} =

; Only for {KEY_VOICE} = {VOICE_XTTS}: the voice, one the XTTS server knows (one of those built in, or the name of a recording
; in its folder of voices).
; Empty: {DEFAULT_XTTS_VOICE}
{KEY_XTTS_VOICE} =

; Only for {KEY_VOICE} = {VOICE_XTTS}: the language the companion speaks, as XTTS names it (de, en, fr ...).
; Empty: {DEFAULT_XTTS_LANGUAGE}
{KEY_XTTS_LANGUAGE} =

; Only for {KEY_VOICE} = {VOICE_XTTS}: the file that starts the XTTS server (eg D:\\xtts\\start-xtts-server.bat). If the XTTS server
; is not running when this server starts, this server starts it in a window of its own and waits until it is ready.
; Empty: the XTTS server is not started, start it yourself
{KEY_XTTS_START} =

; How loud the voice of the companion is put into the game, in percent: 100 = as the voice makes it, 200 = twice as loud
; ({LOWEST_VOICE_VOLUME} to {HIGHEST_VOICE_VOLUME}). Above 100 the loud parts are pressed together, so that nothing crackles.
; A sentence the companion calls out (say with loud) is louder still.
; Empty: {DEFAULT_VOICE_VOLUME}
{KEY_VOICE_VOLUME} =

; on = the companion moves its hands while it talks, as long as it stands around.
; off = it keeps still.
{KEY_TALK_GESTURES} = on

; on = what the game lets the companion say on its own (idle talk, taunts in a fight, greetings) is cut off, so that
;      only what the AI says with its own voice is heard. Grunts and cries stay, other characters talk as always.
; off = the companion also says the sentences of the game.
; Counts with the next STATE of the AI, the game does not have to be started anew.
{KEY_MUTE_GAME_LINES} = off

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
    # The folder of the game that is played: the one of Skyrim VR or the one of Skyrim Special Edition, as 'vr' says
    game_folder: str = DEFAULT_GAME_FOLDER
    vr: bool = True
    piper_folder: str = DEFAULT_PIPER_FOLDER
    voice: str = VOICE_PIPER
    xtts_url: str = DEFAULT_XTTS_URL
    xtts_voice: str = DEFAULT_XTTS_VOICE
    xtts_language: str = DEFAULT_XTTS_LANGUAGE
    xtts_start: str = ""
    voice_volume: int = DEFAULT_VOICE_VOLUME
    talk_gestures: bool = True
    mute_game_lines: bool = False
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

    # A file from before this setting says nothing about it, and then the game is Skyrim VR
    settings.vr = read(KEY_VR).lower() not in OFF_WORDS
    if settings.vr:
        settings.game_folder = read(KEY_GAME_FOLDER) or DEFAULT_GAME_FOLDER
    else:
        settings.game_folder = read(KEY_GAME_FOLDER_SE) or DEFAULT_GAME_FOLDER_SE
    settings.piper_folder = read(KEY_PIPER_FOLDER) or DEFAULT_PIPER_FOLDER
    # Anything but xtts leaves Piper to speak, a typing mistake too
    settings.voice = VOICE_XTTS if read(KEY_VOICE).lower() == VOICE_XTTS else VOICE_PIPER
    settings.xtts_url = read(KEY_XTTS_URL) or DEFAULT_XTTS_URL
    settings.xtts_voice = read(KEY_XTTS_VOICE) or DEFAULT_XTTS_VOICE
    settings.xtts_language = read(KEY_XTTS_LANGUAGE) or DEFAULT_XTTS_LANGUAGE
    settings.xtts_start = read(KEY_XTTS_START)
    volume_text = read(KEY_VOICE_VOLUME).rstrip("%").strip()
    if volume_text.isdecimal():
        settings.voice_volume = min(max(int(volume_text), LOWEST_VOICE_VOLUME), HIGHEST_VOICE_VOLUME)
    # A file from before this setting says nothing about it, and then the hands move
    talk_text = read(KEY_TALK_GESTURES).lower()
    settings.talk_gestures = talk_text in ON_WORDS or talk_text == ""
    settings.mute_game_lines = read(KEY_MUTE_GAME_LINES).lower() in ON_WORDS
    settings.lips = read(KEY_LIPS).lower() in ON_WORDS
    settings.lipgen_folder = read(KEY_LIPGEN_FOLDER)
    settings.facefx_folder = read(KEY_FACEFX_FOLDER)
    settings.fonix_file = read(KEY_FONIX_FILE)
    return settings, os.path.abspath(file)
