"""The voice of the companion in the game, for the command say.

The text is turned into sound by Piper, the text-to-speech program Mantella comes with (the folder "piper" of the
Mantella download, with one voice model per voice of the game). The sound file is then put where the game looks
for the sound of one of the two lines the game mod can make the companion speak:

    <game>\\Data\\Sound\\Voice\\KI_MCP_Gefaehrte.esp\\<voice of the companion>\\<name of the line>.wav

Piper is talked to the same way src/tts/piper.py of Mantella does it: the program keeps running, takes
"load_model <file>" and "synthesize <text>" as lines, answers "Model loaded" and writes out.wav into its working folder.
Mantella's own class is not used, because it needs all of Mantella's settings to start.

If the lip movement is switched on, a lip file made from the sound goes next to the sound file (see lips.py).
"""
import csv
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from queue import Empty, Queue

from src.companion.lips import LIP_ENDING, PLACEHOLDER_FILE, SOUND_ENDING, LipSync

GAME_PLUGIN_NAME: str = "KI_MCP_Gefaehrte.esp"
# The names the game gives the sound files of the two lines (see VOICE_LINE_FILES in build.py of the game mod).
# Line 1 is the first one, line 2 the second
LINE_FILES: list[str] = ["KI_MCP_Say_KI_MCP_SayOne_00000805_1.wav", "KI_MCP_Say_KI_MCP_SayTwo_00000807_1.wav"]
PIPER_PROGRAM: str = "piper.exe"
# Where the voice models are below the Piper folder, as in src/tts/piper.py of Mantella
MODELS_SUBFOLDER: tuple[str, ...] = ("models", "skyrim", "low")
MODEL_ENDING: str = ".onnx"
OUTPUT_FILE: str = "out.wav"
MODEL_LOADED_TEXT: str = "Model loaded"
LOAD_TIMEOUT_SECONDS: float = 20.0
SYNTHESIZE_TIMEOUT_SECONDS: float = 15.0
POLL_SECONDS: float = 0.01
# A longer speech would keep the companion talking for minutes
MAX_TEXT_LENGTH: int = 600
CHARACTERS_FILE: Path = Path(__file__).resolve().parents[2] / "data" / "Skyrim" / "skyrim_characters.csv"
DEFAULT_GAME_FOLDER: str = r"C:\Program Files (x86)\Steam\steamapps\common\SkyrimVR"
DEFAULT_PIPER_FOLDER: str = str(Path(__file__).resolve().parents[2] / "piper")

_voice_folders_by_name: dict[str, str] | None = None


def lip_file_of(line_file: str) -> str:
    """The name of the lip file that belongs to the sound file of a line"""
    return line_file[:-len(SOUND_ENDING)] + LIP_ENDING


def find_voice_folder(character_name: str, file: Path = CHARACTERS_FILE) -> str:
    """Returns the voice of a character of the game by its name (eg Lydia -> FemaleEvenToned), or an empty text.

    Only for a game mod whose plugin cannot tell the voice yet. The table is Mantella's own list of characters.
    """
    global _voice_folders_by_name
    if _voice_folders_by_name is None or file != CHARACTERS_FILE:
        folders: dict[str, str] = {}
        try:
            with open(file, newline="", encoding="utf-8-sig", errors="replace") as opened:
                for row in csv.DictReader(opened):
                    name = str(row.get("name") or "").strip().lower()
                    folder = str(row.get("skyrim_voice_folder") or "").strip()
                    if name != "" and folder != "" and name not in folders:
                        folders[name] = folder
        except (OSError, csv.Error):
            folders = {}
        if file != CHARACTERS_FILE:
            return folders.get(character_name.strip().lower(), "")
        _voice_folders_by_name = folders
    return _voice_folders_by_name.get(character_name.strip().lower(), "")


def clean_text(text: str) -> str:
    """The text the way Piper should get it: one line, without the stars Piper would read aloud"""
    return " ".join(text.replace("*", "").split())


class CompanionVoice:
    """Makes the sound of what the companion says and puts it into the game folder"""

    def __init__(self, game_folder: str = DEFAULT_GAME_FOLDER, piper_folder: str = DEFAULT_PIPER_FOLDER,
                 piper_command: list[str] | None = None, lips: LipSync | None = None) -> None:
        """
        Args:
            piper_command: The command that starts Piper. Only tests set it, to put a stand-in in Piper's place
            lips: Makes the lip movement for what is said. Without it the mouth of the companion stays shut
        """
        self.__lips: LipSync | None = lips
        # Lips that are wanted but cannot be made count as switched off. describe_lips says why
        self.__are_lips_on: bool = lips is not None and lips.find_problem() == ""
        self.__game_folder: str = game_folder
        self.__piper_folder: Path = Path(piper_folder)
        self.__piper_command: list[str] = piper_command if piper_command is not None else [str(self.__piper_folder / PIPER_PROGRAM)]
        self.__is_stand_in: bool = piper_command is not None
        self.__work_folder: str = tempfile.mkdtemp(prefix="companion_voice_")
        self.__lock = threading.Lock()
        self.__process: subprocess.Popen | None = None
        self.__output: Queue = Queue()
        self.__loaded_model: str = ""
        self.__next_line: int = 1

    @property
    def voice_root(self) -> str:
        return os.path.join(self.__game_folder, "Data", "Sound", "Voice", GAME_PLUGIN_NAME)

    def describe_setup(self) -> str:
        """One line for the start of the server: whether speaking can work"""
        if not self.__is_stand_in and not os.path.isfile(self.__piper_command[0]):
            return f"off: {self.__piper_command[0]} not found (see piper_folder in companion.ini)"
        if not os.path.isdir(self.voice_root):
            return f"off: {self.voice_root} not found (run build.py --install of the game mod, or see game_folder in companion.ini)"
        return f"on: Piper in {self.__piper_folder}, sound goes to {self.voice_root}"

    def describe_lips(self) -> str:
        """One line for the start of the server: whether the lips of the companion move"""
        if self.__lips is None:
            return "off (lips = off in companion.ini)"
        return self.__lips.describe_setup()

    def prepare_lip_files(self) -> str:
        """Makes the folders of the voices fit the setting for the lips. Call it once when the server starts.

        The game only finds the files that are there when it starts. So with the lips switched on, every line gets a
        lip file to begin with (the placeholder), which speak then writes over. With the lips switched off, the lip files
        of the lines are taken away, so that the mouth stays shut instead of moving to an old sentence.

        Returns:
            What was changed as one line for the start of the server, or an empty text if nothing was
        """
        if not os.path.isdir(self.voice_root):
            return ""
        added_count = 0
        removed_count = 0
        failed_count = 0
        for folder_name in sorted(os.listdir(self.voice_root)):
            folder = os.path.join(self.voice_root, folder_name)
            if not os.path.isdir(folder):
                continue
            for line_file in LINE_FILES:
                lip_file = os.path.join(folder, lip_file_of(line_file))
                try:
                    if not self.__are_lips_on:
                        if os.path.isfile(lip_file):
                            os.remove(lip_file)
                            removed_count += 1
                    elif not os.path.isfile(lip_file) and os.path.isfile(os.path.join(folder, line_file)):
                        shutil.copyfile(PLACEHOLDER_FILE, lip_file)
                        added_count += 1
                except OSError:
                    failed_count += 1
        parts: list[str] = []
        if added_count > 0:
            parts.append(f"{added_count} lip files put next to the sound files of the lines")
        if removed_count > 0:
            parts.append(f"{removed_count} lip files of the lines taken away")
        if len(parts) > 0:
            parts.append("if the game is running, it has to be started anew for this to count")
        if failed_count > 0:
            parts.append(f"{failed_count} lip files could not be changed (while the game runs it may hold on to them; "
                         "if a permission is missing, run build.py --install of the game mod again as administrator)")
        return ", ".join(parts)

    def speak(self, text: str, voice_folder: str) -> tuple[bool, str, int]:
        """Turns the text into sound and puts it into the game folder. Takes about a second, so call it from a thread.

        Args:
            voice_folder: The voice of the companion as the game names it (eg FemaleEvenToned)

        Returns:
            Whether it worked, the text for the AI if it did not, and the number of the line the game mod has to speak (1 or 2)
        """
        cleaned = clean_text(text)
        if cleaned == "":
            return False, "There is nothing to say.", 0
        with self.__lock:
            target_folder = os.path.join(self.voice_root, voice_folder)
            if not os.path.isdir(target_folder):
                return False, (f"The game has no folder for your voice '{voice_folder}' ({target_folder}). The player has to add the voice "
                               "to voice_folders.txt of the game mod if it is missing there, run build.py --install and restart the game."), 0
            model = self.__piper_folder.joinpath(*MODELS_SUBFOLDER) / (voice_folder.lower().replace(" ", "") + MODEL_ENDING)
            if not self.__is_stand_in:
                if not os.path.isfile(self.__piper_command[0]):
                    return False, (f"Piper is not installed where the server looks for it ({self.__piper_command[0]}). The player has to copy "
                                   "the folder 'piper' of the Mantella download there, or name its folder as piper_folder in companion.ini."), 0
                if not model.is_file():
                    return False, f"Piper has no voice model for your voice '{voice_folder}' ({model}).", 0
            try:
                sound_file = self.__synthesize(cleaned, str(model))
            except (OSError, RuntimeError) as error:
                self.__stop_piper()
                return False, f"Piper could not make the sound: {error}", 0
            # Without a lip file of its own, the sentence gets the placeholder: the lips of the sentence before would not fit
            lip_file = ""
            if self.__lips is not None and self.__are_lips_on:
                lip_file = self.__lips.make(sound_file, cleaned) or str(PLACEHOLDER_FILE)

            # The game may still hold on to the file of the line that was spoken last, so the two lines take turns
            last_error = ""
            for line in (self.__next_line, 3 - self.__next_line):
                try:
                    shutil.copyfile(sound_file, os.path.join(target_folder, LINE_FILES[line - 1]))
                except OSError as error:
                    last_error = str(error)
                    continue
                if lip_file != "":
                    try:
                        shutil.copyfile(lip_file, os.path.join(target_folder, lip_file_of(LINE_FILES[line - 1])))
                    except OSError:
                        # The sound counts. Without the new lip file the mouth moves wrongly at worst
                        pass
                self.__next_line = 3 - line
                return True, "", line
            return False, (f"The server could not write the sound into {target_folder}: {last_error}. If that is a missing permission, "
                           "the player has to run build.py --install of the game mod again (as administrator)."), 0

    def __synthesize(self, text: str, model: str) -> str:
        self.__start_piper()
        if self.__loaded_model != model:
            self.__send(f"load_model {model}")
            self.__wait_for(lambda: self.__has_printed(MODEL_LOADED_TEXT), LOAD_TIMEOUT_SECONDS, "the voice model did not load")
            self.__loaded_model = model
        sound_file = os.path.join(self.__work_folder, OUTPUT_FILE)
        if os.path.exists(sound_file):
            os.remove(sound_file)
        self.__send(f"synthesize {text}")
        self.__wait_for(lambda: self.__has_sound(sound_file), SYNTHESIZE_TIMEOUT_SECONDS, "no sound file came out")
        return sound_file

    def __start_piper(self) -> None:
        if self.__process is not None and self.__process.poll() is None:
            return
        self.__loaded_model = ""
        self.__output = Queue()
        self.__process = subprocess.Popen(self.__piper_command, cwd=self.__work_folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, universal_newlines=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self.__read_output, args=(self.__process, self.__output), daemon=True).start()

    @staticmethod
    def __read_output(process: subprocess.Popen, output: Queue) -> None:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            output.put(line)

    def __stop_piper(self) -> None:
        if self.__process is not None and self.__process.poll() is None:
            self.__process.kill()
        self.__process = None
        self.__loaded_model = ""

    def __send(self, line: str) -> None:
        assert self.__process is not None and self.__process.stdin is not None
        self.__process.stdin.write(line + "\n")
        self.__process.stdin.flush()

    def __has_printed(self, wanted: str) -> bool:
        try:
            while True:
                if wanted in self.__output.get_nowait():
                    return True
        except Empty:
            return False

    @staticmethod
    def __has_sound(sound_file: str) -> bool:
        """A file that is still being written has no length yet"""
        if not os.path.exists(sound_file):
            return False
        try:
            with wave.open(sound_file, "rb") as opened:
                return opened.getnframes() > 0
        except (OSError, wave.Error, EOFError):
            return False

    def __wait_for(self, is_done, timeout_seconds: float, problem: str) -> None:
        end_time = time.monotonic() + timeout_seconds
        while time.monotonic() < end_time:
            if is_done():
                return
            if self.__process is None or self.__process.poll() is not None:
                raise RuntimeError("Piper has stopped")
            time.sleep(POLL_SECONDS)
        raise RuntimeError(problem)
