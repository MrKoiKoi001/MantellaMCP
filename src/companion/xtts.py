"""Sound for the voice of the companion from an XTTS server, instead of from Piper.

XTTS (Coqui XTTS v2) speaks many languages, German among them, and can take on a voice from a short recording.
It runs as a server of its own on the graphics card, and this file only asks it over HTTP the way OpenAI's
speech route is asked:

    POST <url>/v1/audio/speech   {"input": text, "voice": name of the voice, "language": "de"}   -> a WAV file
    GET  <url>/health            -> answers while the server runs

The server this was written for is the player's own (xtts_server.py, started with start-xtts-server.bat).
Whether it is used is set in companion.ini (voice = xtts). If it does not answer, voice.py lets Piper speak.
"""
import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import wave

DEFAULT_XTTS_URL: str = "http://127.0.0.1:8880"
DEFAULT_XTTS_VOICE: str = "Uta Obando"
DEFAULT_XTTS_LANGUAGE: str = "de"
SPEECH_ROUTE: str = "/v1/audio/speech"
HEALTH_ROUTE: str = "/health"
# With the game running next to it, a short sentence took up to five seconds on the player's machine
SPEECH_TIMEOUT_SECONDS: float = 30.0
HEALTH_TIMEOUT_SECONDS: float = 2.0
# Loading the model and the first sentence to warm up took about half a minute on the player's machine
START_TIMEOUT_SECONDS: float = 180.0
START_POLL_SECONDS: float = 1.0
# The languages XTTS v2 speaks, by the names the AI is told to write in
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "pl": "Polish",
    "tr": "Turkish", "ru": "Russian", "nl": "Dutch", "cs": "Czech", "ar": "Arabic", "zh-cn": "Chinese", "hu": "Hungarian",
    "ko": "Korean", "ja": "Japanese", "hi": "Hindi",
}


class XttsVoice:
    """Asks an XTTS server for the sound of a sentence"""

    def __init__(self, url: str = DEFAULT_XTTS_URL, voice: str = DEFAULT_XTTS_VOICE, language: str = DEFAULT_XTTS_LANGUAGE) -> None:
        """
        Args:
            url: Where the XTTS server listens, without a route (eg http://127.0.0.1:8880)
            voice: A voice the XTTS server knows: one of those built in, or the name of a recording in its folder of voices
            language: The language the text is in, as XTTS names it (eg de)
        """
        self.__url: str = url.strip().rstrip("/")
        self.__voice: str = voice.strip()
        self.__language: str = language.strip().lower()
        # The server runs on this machine. A proxy that is set for the internet must not get in between
        self.__opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def language_name(self) -> str:
        """The language the voice speaks, as a word for the AI (eg German)"""
        return LANGUAGE_NAMES.get(self.__language, self.__language)

    def find_problem(self) -> str:
        """Returns why the XTTS server cannot be used right now, or an empty text if it answers"""
        try:
            with self.__opener.open(self.__url + HEALTH_ROUTE, timeout=HEALTH_TIMEOUT_SECONDS) as answer:
                answer.read()
        except (OSError, ValueError) as error:
            return f"the XTTS server does not answer at {self.__url} ({self.__reason_of(error)})"
        return ""

    def start_server(self, start_file: str, start_command: list[str] | None = None, timeout_seconds: float = START_TIMEOUT_SECONDS) -> str:
        """Starts the XTTS server in a window of its own if it is not running, and waits until it answers.

        The window stays open when this server stops, so the XTTS server is still there the next time.

        Args:
            start_file: The file that starts the XTTS server (eg its start-xtts-server.bat)
            start_command: The command to run instead of the file. Only tests set it

        Returns:
            What happened, as one line for the start of the server
        """
        if self.find_problem() == "":
            return "is running already"
        if start_command is None:
            if not os.path.isfile(start_file):
                return f"could not be started: {start_file} not found (see xtts_start in companion.ini)"
            start_command = ["cmd.exe", "/c", start_file]
        print(f"  Starting the XTTS server ({start_file}). That takes about half a minute ...", flush=True)
        try:
            process = subprocess.Popen(start_command, cwd=os.path.dirname(os.path.abspath(start_file)) or None,
                                       creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as error:
            return f"could not be started: {error}"
        started_at = time.monotonic()
        while time.monotonic() - started_at < timeout_seconds:
            if self.find_problem() == "":
                return f"started with {start_file}, ready after {time.monotonic() - started_at:.0f} seconds"
            if process.poll() is not None:
                return f"was started with {start_file} but stopped again right away. Start that file by hand to see why"
            time.sleep(START_POLL_SECONDS)
        return f"was started with {start_file} but did not answer within {timeout_seconds:.0f} seconds. Look into its window"

    def describe_setup(self) -> str:
        """Which XTTS server, voice and language, for the start of the server"""
        return f"XTTS at {self.__url}, voice '{self.__voice}', language {self.language_name}"

    def make(self, text: str, sound_file: str) -> None:
        """Lets the XTTS server speak the text and saves the sound

        Raises:
            RuntimeError: The server did not answer, or what it sent is no sound
        """
        body = json.dumps({"input": text, "voice": self.__voice, "language": self.__language}).encode("utf-8")
        request = urllib.request.Request(self.__url + SPEECH_ROUTE, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.__opener.open(request, timeout=SPEECH_TIMEOUT_SECONDS) as answer:
                sound = answer.read()
        except (OSError, ValueError) as error:
            raise RuntimeError(f"the XTTS server did not make the sound ({self.__reason_of(error)})") from error
        try:
            with wave.open(io.BytesIO(sound), "rb") as opened:
                frame_count = opened.getnframes()
        except (wave.Error, EOFError) as error:
            raise RuntimeError("the XTTS server sent something that is no WAV file") from error
        if frame_count == 0:
            raise RuntimeError("the XTTS server sent a sound without length")
        with open(sound_file, "wb") as opened:
            opened.write(sound)

    @staticmethod
    def __reason_of(error: Exception) -> str:
        if isinstance(error, urllib.error.HTTPError):
            return f"it answered with the error {error.code}"
        if isinstance(error, urllib.error.URLError):
            return str(error.reason)
        return str(error) or type(error).__name__
