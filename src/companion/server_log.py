"""The log file of the companion server.

The server only sees its two sides: the requests of the game and the calls of the AI. When the AI is told that
the game is gone, the file shows afterwards what happened: for how long the game was silent, what it had been
handed before, and what became of every command. Requests of the game that carry nothing and get nothing are
left out, so that the file stays readable.

Nothing is written until start_log_file was called: the tests run without a file.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

LOGGER_NAME: str = "companion"
LOG_FILE: str = "companion_server.log"
# A full file is put aside as companion_server.log.1 and so on
LOG_FILE_BYTES: int = 2_000_000
LOG_FILE_BACKUPS: int = 3
MAX_TEXT_LENGTH: int = 300
CUT_OFF_MARK: str = "..."

log: logging.Logger = logging.getLogger(LOGGER_NAME)
log.setLevel(logging.INFO)
# Without this the lines would also go to the terminal through the logger of the web server
log.propagate = False
log.addHandler(logging.NullHandler())


def start_log_file(log_file: str = LOG_FILE) -> str:
    """Makes the lines go into the file from now on. Returns the full path of the file"""
    handler = RotatingFileHandler(log_file, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    return os.path.abspath(log_file)


def shorten(text: Any, max_length: int = MAX_TEXT_LENGTH) -> str:
    """One line of at most max_length characters"""
    line = " ".join(str(text).split())
    if len(line) > max_length:
        return line[:max_length - len(CUT_OFF_MARK)] + CUT_OFF_MARK
    return line
