"""The lip movement for what the companion says (command say).

The game moves the lips of a speaking character by a file <name of the line>.lip that lies next to the sound file.
Such a file is made from the sound and the spoken text by one of two programs, the same ones Mantella uses
(see src/tts/ttsable.py of Mantella, _generate_voiceline_files):

- LipGenerator.exe of the Creation Kit, in <LipGen folder>\\LipGenerator. It is the faster one
- FaceFXWrapper.exe (https://github.com/Nukem9/FaceFXWrapper), which needs the file FonixData.cdf of the Creation Kit

Making the file takes time for every sentence, so the lip movement can be switched off (see settings.py).
"""
import ctypes
import os
import subprocess
import time
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
# FaceFXWrapper.exe is a 32-bit program that carries a whole Creation Kit inside and loads it into its own memory.
# Now and then that does not fit, and the program then opens a window ("Memory Error - Not enough memory to run
# application") and waits until someone clicks it away. The window comes up in front of the game.
# So on Windows the program runs on a desktop of its own that nobody sees: a window it opens there shows nowhere.
# Such a window means the run has failed, so the program is ended at once and started again, LIP_TRIES times in all
HIDDEN_DESKTOP_NAME: str = "KI_MCP_Gefaehrte_Lips"
HIDDEN_DESKTOP_STATION: str = "WinSta0"
LIP_TRIES: int = 2
LIP_POLL_MILLISECONDS: int = 50
# The window class of the message boxes of Windows
MESSAGE_BOX_CLASS: str = "#32770"
WINDOW_CLASS_LENGTH: int = 64
# Numbers of the Windows API (winuser.h, winbase.h)
DESKTOP_ALL_ACCESS: int = 0x10000000
CREATE_NO_WINDOW_FLAG: int = 0x08000000
WAIT_OBJECT: int = 0
ENDED_BY_US_EXIT_CODE: int = 1
NO_TITLE_TEXT: str = "(no title)"

_hidden_desktop: int | None = None


class _StartupInfo(ctypes.Structure):
    """STARTUPINFOW of the Windows API"""
    _fields_ = [("cb", ctypes.c_uint32), ("lpReserved", ctypes.c_wchar_p), ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_uint32), ("dwY", ctypes.c_uint32), ("dwXSize", ctypes.c_uint32), ("dwYSize", ctypes.c_uint32),
                ("dwXCountChars", ctypes.c_uint32), ("dwYCountChars", ctypes.c_uint32), ("dwFillAttribute", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("wShowWindow", ctypes.c_uint16), ("cbReserved2", ctypes.c_uint16),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.c_void_p), ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    """PROCESS_INFORMATION of the Windows API"""
    _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p), ("dwProcessId", ctypes.c_uint32),
                ("dwThreadId", ctypes.c_uint32)]


def run_hidden(command: list[str], working_folder: str, timeout_seconds: float) -> str:
    """Runs a program on a desktop nobody sees and waits for it. Windows only.

    Returns:
        The title of the message box the program opened (it is ended then; NO_TITLE_TEXT if the box has no title),
        or an empty text if there was none: the program ran to its end, or took too long and was ended

    Raises:
        OSError: The desktop could not be made or the program could not be started
    """
    global _hidden_desktop
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateDesktopW.restype = ctypes.c_void_p
    user.CreateDesktopW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateProcessW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation)]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    window_callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user.EnumDesktopWindows.argtypes = [ctypes.c_void_p, window_callback, ctypes.c_void_p]
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

    if _hidden_desktop is None:
        # Made once and kept: Windows gives it back when the server ends
        desktop = user.CreateDesktopW(HIDDEN_DESKTOP_NAME, None, None, 0, DESKTOP_ALL_ACCESS, None)
        if not desktop:
            raise OSError(ctypes.get_last_error(), "the hidden desktop could not be made")
        _hidden_desktop = desktop

    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(_StartupInfo)
    startup.lpDesktop = HIDDEN_DESKTOP_STATION + "\\" + HIDDEN_DESKTOP_NAME
    process = _ProcessInformation()
    # Windows may write into the command line, so it gets a buffer of its own
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    if not kernel.CreateProcessW(None, command_line, None, None, 0, CREATE_NO_WINDOW_FLAG, None, working_folder,
                                 ctypes.byref(startup), ctypes.byref(process)):
        raise OSError(ctypes.get_last_error(), f"{command[0]} could not be started")

    box_title = ""

    def look_at_window(window: int, _: int) -> int:
        nonlocal box_title
        owner = ctypes.c_uint32(0)
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == process.dwProcessId and user.IsWindowVisible(window):
            class_name = ctypes.create_unicode_buffer(WINDOW_CLASS_LENGTH)
            user.GetClassNameW(window, class_name, WINDOW_CLASS_LENGTH)
            if class_name.value == MESSAGE_BOX_CLASS:
                title = ctypes.create_unicode_buffer(WINDOW_CLASS_LENGTH)
                user.GetWindowTextW(window, title, WINDOW_CLASS_LENGTH)
                box_title = title.value or NO_TITLE_TEXT
                return 0
        return 1

    callback = window_callback(look_at_window)
    try:
        end_time = time.monotonic() + timeout_seconds
        while kernel.WaitForSingleObject(process.hProcess, LIP_POLL_MILLISECONDS) != WAIT_OBJECT:
            user.EnumDesktopWindows(_hidden_desktop, callback, None)
            if box_title != "" or time.monotonic() > end_time:
                kernel.TerminateProcess(process.hProcess, ENDED_BY_US_EXIT_CODE)
                kernel.WaitForSingleObject(process.hProcess, LIP_POLL_MILLISECONDS)
                break
    finally:
        kernel.CloseHandle(process.hThread)
        kernel.CloseHandle(process.hProcess)
    return box_title
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
        self.__has_told_about_plain_way: bool = False
        # How often the program ended in a message box instead of a lip file (see run_hidden)
        self.failed_runs: int = 0

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
            if self.__lip_command is None and os.name == "nt":
                self.__run_without_windows(command, working_folder, lip_file)
            else:
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

    def __run_without_windows(self, command: list[str], working_folder: str, lip_file: str) -> None:
        """Runs the program so that a window it opens shows nowhere (see HIDDEN_DESKTOP_NAME). A run that ends in
        such a window is tried again. If the hidden desktop cannot be had, the program runs the plain way"""
        for attempt in range(LIP_TRIES):
            try:
                box_title = run_hidden(command, working_folder, LIP_TIMEOUT_SECONDS)
            except (OSError, AttributeError, ctypes.ArgumentError) as error:
                if not self.__has_told_about_plain_way:
                    self.__has_told_about_plain_way = True
                    print(f"Lips: the program for the lip files cannot run hidden ({error}), so its windows may show.", flush=True)
                subprocess.run(command, cwd=working_folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=LIP_TIMEOUT_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            if box_title == "":
                return
            self.failed_runs += 1
            print(f"Lips: {os.path.basename(command[0])} opened the message box '{box_title}' instead of making the lip file "
                  f"(try {attempt + 1} of {LIP_TRIES}, {self.failed_runs} such runs so far). It was ended without the window showing.", flush=True)
            if os.path.exists(lip_file):
                os.remove(lip_file)
