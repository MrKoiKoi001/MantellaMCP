"""Finds animations of the game ("Idles") by a word in their name, for the gestures of the companion.

The game keeps its animations as entries of the type IDLE in Skyrim.esm: each with a name (eg IdleDrink), the event
that starts the animation, the entry it hangs under and its conditions. This file reads them once and searches them.
Only Skyrim.esm is read: the entries of the other master files would need their form IDs worked out from the load order.
"""
import os
import re
import struct
import zlib
from dataclasses import dataclass

from src.companion.ref_ids import format_ref_id, parse_ref_id

MASTER_FILE: str = os.path.join("Data", "Skyrim.esm")
HEADER_SIZE: int = 24
FIELD_HEADER_SIZE: int = 6
GROUP_TYPE: bytes = b"GRUP"
IDLE_TYPE: bytes = b"IDLE"
COMPRESSED_FLAG: int = 0x00040000
FIELD_NAME: str = "EDID"
FIELD_EVENT: str = "ENAM"
FIELD_PARENT: str = "ANAM"
FIELD_CONDITION: str = "CTDA"
# A condition of an entry: the number of the function "GetSitting", and the value that means "sits"
CONDITION_GET_SITTING: int = 159
SITTING_VALUE: float = 3.0
MAX_MATCHES: int = 20
REF_ID_LENGTH: int = 8
# Animations whose name has one of these parts are nothing a companion plays as a gesture: dying, fighting, animals, monsters
UNWANTED_PARTS: set[str] = {
    "death", "ragdoll", "dragon", "wolf", "dog", "cow", "bear", "horse", "werewolf", "vampire", "kill", "stagger", "sheath",
    "sheathe", "attack", "recoil", "spider", "chaurus", "falmer", "draugr", "giant", "troll", "mammoth", "atronach", "wisp",
    "hagraven", "spriggan", "skeever", "horker", "cat", "crab", "mcrab", "deer", "goat", "chicken", "hare", "fish", "wraith",
    "sphere", "cent", "bleedout", "bleed", "knockdown", "knock", "block", "bash", "shout", "equip", "unequip", "mount", "mounted",
    "dismount", "swim", "sneak", "jump", "sprint", "rider", "priest", "witchlight", "combat", "weapon", "bow", "camera", "pa",
    "player", "offset", "exit", "instant", "port", "ambush", "hole", "coffin", "alcove", "sarcophagus",
}


@dataclass
class IdleEntry:
    """One animation of the game"""
    form_id: int
    name: str
    event: str
    # Whether the entry, or one it hangs under, has conditions the game checks before it plays it
    has_conditions: bool = False
    needs_sitting: bool = False

    @property
    def ref_id(self) -> str:
        return format_ref_id(self.form_id)


def read_idles(file_path: str) -> list[IdleEntry]:
    """Reads the animations out of a master file of the game. Raises OSError or ValueError if that does not work"""
    records: dict[int, dict] = {}
    with open(file_path, "rb") as opened:
        header = opened.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise ValueError("The file is too short to be a master file of the game.")
        # The file starts with one entry about itself, then come the groups, one per type of entry
        opened.seek(HEADER_SIZE + struct.unpack("<I", header[4:8])[0])
        while True:
            header = opened.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                break
            if header[:4] != GROUP_TYPE:
                raise ValueError("The file is not built like a master file of the game.")
            group_size = struct.unpack("<I", header[4:8])[0]
            if header[8:12] != IDLE_TYPE:
                opened.seek(group_size - HEADER_SIZE, os.SEEK_CUR)
                continue
            data = opened.read(group_size - HEADER_SIZE)
            place = 0
            while place + HEADER_SIZE <= len(data):
                size, flags, form_id = struct.unpack("<III", data[place + 4:place + 16])
                body = data[place + HEADER_SIZE:place + HEADER_SIZE + size]
                if flags & COMPRESSED_FLAG:
                    body = zlib.decompress(body[4:])
                records[form_id] = _read_fields(body)
                place += HEADER_SIZE + size
            break

    entries: list[IdleEntry] = []
    for form_id, record in records.items():
        if record["name"] == "" or record["event"] == "":
            # Without an event it is a group other animations hang under, not an animation
            continue
        entry = IdleEntry(form_id, record["name"], record["event"])
        # The conditions of the entries above count as well
        above = record
        for _ in range(len(records)):
            entry.has_conditions = entry.has_conditions or above["has_conditions"]
            entry.needs_sitting = entry.needs_sitting or above["needs_sitting"]
            above = records.get(above["parent"])
            if above is None:
                break
        entries.append(entry)
    return entries


def _read_fields(body: bytes) -> dict:
    record: dict = {"name": "", "event": "", "parent": 0, "has_conditions": False, "needs_sitting": False}
    place = 0
    while place + FIELD_HEADER_SIZE <= len(body):
        field = body[place:place + 4].decode("latin1")
        size = struct.unpack("<H", body[place + 4:place + FIELD_HEADER_SIZE])[0]
        value = body[place + FIELD_HEADER_SIZE:place + FIELD_HEADER_SIZE + size]
        if field == FIELD_NAME:
            record["name"] = value.rstrip(b"\0").decode("latin1")
        elif field == FIELD_EVENT:
            record["event"] = value.rstrip(b"\0").decode("latin1")
        elif field == FIELD_PARENT and len(value) >= 4:
            record["parent"] = struct.unpack("<I", value[:4])[0]
        elif field == FIELD_CONDITION and len(value) >= 10:
            record["has_conditions"] = True
            compared_to = struct.unpack("<f", value[4:8])[0]
            function = struct.unpack("<H", value[8:10])[0]
            if function == CONDITION_GET_SITTING and compared_to == SITTING_VALUE:
                record["needs_sitting"] = True
        place += FIELD_HEADER_SIZE + size
    return record


def split_name(name: str) -> list[str]:
    """The parts a name of the game is put together from, in lower case: 'ChairEatingStart' -> chair, eating, start"""
    return [part.lower() for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)]


def find_idles(entries: list[IdleEntry], text: str, limit: int = MAX_MATCHES) -> tuple[list[IdleEntry], int]:
    """Looks for the animations whose name has a part that starts with every word of the text
    ('eat' finds ChairEatingStart, but not IdlePrisonerBeat)

    Returns:
        The matches, the ones without conditions first, and how many there are in all
    """
    words = [word for word in re.split(r"[\s_,]+", text.strip().lower()) if word != ""]
    if len(words) == 0:
        return [], 0
    matches: list[IdleEntry] = []
    for entry in entries:
        parts = split_name(entry.name) + split_name(entry.event)
        if any(part in UNWANTED_PARTS for part in parts):
            continue
        if all(any(part.startswith(word) for part in parts) for word in words):
            matches.append(entry)
    matches.sort(key=lambda entry: (entry.has_conditions, entry.name.lower()))
    return matches[:limit], len(matches)


class IdleLibrary:
    """The animations of the game, read when they are asked for the first time"""

    def __init__(self, game_folder: str) -> None:
        self.__file_path: str = os.path.join(game_folder, MASTER_FILE) if game_folder != "" else ""
        self.__entries: list[IdleEntry] | None = None
        # Why the animations could not be read, or empty
        self.problem: str = ""

    @property
    def entries(self) -> list[IdleEntry]:
        if self.__entries is None:
            self.__entries = []
            if self.__file_path == "":
                self.problem = "The server does not know the folder of the game, so it cannot read the animations of the game."
            else:
                try:
                    self.__entries = read_idles(self.__file_path)
                except (OSError, ValueError, struct.error, zlib.error) as error:
                    self.problem = (f"The server could not read the animations of the game from {self.__file_path} "
                                    f"(see game_folder in companion.ini): {error}")
        return self.__entries

    def find(self, text: str) -> tuple[list[IdleEntry], int]:
        return find_idles(self.entries, text)

    def get(self, name_or_id: str) -> IdleEntry | None:
        """The animation with this name (as the game writes it, upper and lower case do not matter) or this form ID"""
        wanted = name_or_id.strip().lower()
        if wanted == "":
            return None
        form_id = parse_ref_id(wanted) if len(wanted) == REF_ID_LENGTH else None
        for entry in self.entries:
            if entry.name.lower() == wanted or (form_id is not None and entry.form_id == form_id):
                return entry
        return None
