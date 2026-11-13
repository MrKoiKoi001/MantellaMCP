"""The lip movement for what the companion says (command say).

The game moves the lips of a speaking character by a file <name of the line>.lip that lies next to the sound file.
Such a file is made from the sound and the spoken text by one of two programs, the same ones Mantella uses
(see src/tts/ttsable.py of Mantella, _generate_voiceline_files):

- LipGenerator.exe of the Creation Kit, in <LipGen folder>\\LipGenerator. It is the faster one
- FaceFXWrapper.exe (https://github.com/Nukem9/FaceFXWrapper), which needs the file FonixData.cdf of the Creation Kit

Making the file takes time for every sentence, so the lip movement can be switched off (see settings.py).
"""
import os
import subprocess
from pathlib import Path

LIP_ENDING: str = ".lip"
SOUND_ENDING: str = ".wav"
LIPGEN_PROGRAM: tuple[str, ...] = ("LipGenerator", "LipGenerator.exe")
# Where the Creation Kit puts LipGen below the folder of the game, as Mantella expects it
LIPGEN_SUBFOLDER: tuple[str, ...] = ("Tools", "LipGen")
FACEFX_PROGRAM: str = "FaceFXWrapper.exe"
FONIX_FILE: str = "FonixData.cdf"
# Where the Creation Kit puts FonixData.cdf below the folder of the game, and Mantella's game mod FaceFXWrapper.exe
PROCESSING_SUBFOLDER: tuple[str, ...] = ("Data", "Sound", "Voice", "Processing")
# What both programs are told about the game and the language. The language decides how letters turn into sounds
FACEFX_GAME: str = "Skyrim"
LIP_LANGUAGE: str = "USEnglish"
# FaceFXWrapper writes the sound once more at another sample rate, into a file with this ending. It is of no use afterwards
RESAMPLED_ENDING: str = "_r.wav"
LIP_TIMEOUT_SECONDS: float = 30.0
# A lip file that makes the mouth move for a moment. It is put next to a sound when no lip file could be made for it,
# because the file of the sentence before would not fit. It is the one Mantella's game mod comes with
# (Sound\Voice\Mantella.esp\MantellaVoice00 of the game mod)
PLACEHOLDER_FILE: Path = Path(__file__).resolve().parent / "placeholder.lip"


class LipSync:
    """Makes the lip file for a sound file"""

    def __init__(self, game_folder: str, lipgen_folder: str = "", facefx_folder: str = "", fonix_file: str = "",
                 lip_command: list[str] | None = None) -> None:
        """
        Args:
            lipgen_folder: The folder LipGen of the Creation Kit. Empty: <game>\\Tools\\LipGen
            facefx_folder: The folder with FaceFXWrapper.exe. Empty: <game>\\Data\\Sound\\Voice\\Processing
            fonix_file: The file FonixData.cdf. Empty: the one in the folder of FaceFXWrapper.exe
            lip_command: A command that is called with the sound file, the lip file to write and the text behind it.
                Only tests set it, to put a stand-in in the place of the two programs
        """
        self.__lipgen_program: Path = (Path(lipgen_folder) if lipgen_folder != "" else Path(game_folder).joinpath(*LIPGEN_SUBFOLDER)).joinpath(*LIPGEN_PROGRAM)
        facefx: Path = Path(facefx_folder) if facefx_folder != "" else Path(game_folder).joinpath(*PROCESSING_SUBFOLDER)
        self.__facefx_program: Path = facefx / FACEFX_PROGRAM
        self.__fonix_file: Path = Path(fonix_file) if fonix_file != "" else facefx / FONIX_FILE
        self.__lip_command: list[str] | None = lip_command

    def find_problem(self) -> str:
        """Why no lip file can be made, or an empty text if one of the two programs is there"""
        if self.__lip_command is not None or self.__lipgen_program.is_file():
            return ""
        if not self.__facefx_program.is_file():
            return f"neither {self.__lipgen_program} nor {self.__facefx_program} found"
        if not self.__fonix_file.is_file():
            return f"{self.__facefx_program.name} found, but not the file it needs: {self.__fonix_file}"
        return ""

    def describe_setup(self) -> str:
        """One line for the start of the server: which program makes the lip files, or why none can"""
        problem = self.find_problem()
        if problem != "":
            return f"off: {problem} (see the lip settings in companion.ini)"
        if self.__lip_command is not None:
            return "on: stand-in"
        if self.__lipgen_program.is_file():
            return f"on: {self.__lipgen_program}"
        return f"on: {self.__facefx_program} with {self.__fonix_file}"

    def make(self, sound_file: str, text: str) -> str:
        """Makes the lip file for a sound file, next to it and under the same name.

        Returns:
            The lip file, or an empty text if none came out (both programs give up on some very short sentences)
        """
        lip_file = sound_file[:-len(SOUND_ENDING)] + LIP_ENDING
        resampled_file = sound_file[:-len(SOUND_ENDING)] + RESAMPLED_ENDING
        for leftover in (lip_file, resampled_file):
            if os.path.exists(leftover):
                os.remove(leftover)
        # The text goes to the program as one of its arguments, and a quotation mark in it would end it early
        spoken = text.replace('"', "")
        working_folder = os.path.dirname(sound_file)
        if self.__lip_command is not None:
            command = self.__lip_command + [sound_file, lip_file, spoken]
        elif self.__lipgen_program.is_file():
            # LipGen writes the lip file next to the sound file on its own
            command = [str(self.__lipgen_program), sound_file, spoken, f"-Language:{LIP_LANGUAGE}", "-Automated"]
        elif self.find_problem() == "":
            command = [str(self.__facefx_program), FACEFX_GAME, LIP_LANGUAGE, str(self.__fonix_file), sound_file, resampled_file, lip_file, spoken]
            working_folder = str(self.__facefx_program.parent)
        else:
            return ""
        try:
            subprocess.run(command, cwd=working_folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=LIP_TIMEOUT_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return ""
        finally:
            if os.path.exists(resampled_file):
                try:
                    os.remove(resampled_file)
                except OSError:
                    pass
        if not os.path.isfile(lip_file) or os.path.getsize(lip_file) == 0:
            return ""
        return lip_file
