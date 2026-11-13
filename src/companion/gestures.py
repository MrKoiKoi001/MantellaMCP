"""The gestures the companion can play with the command gesture: their names and the animations of the game behind them.

They come from the table Mantella itself uses for its emotes (data/Skyrim/skyrim_idles.csv): per line the form ID
of an animation (an "Idle" of the game) and the name it goes by. Some names have several animations.
"""
import csv
import random
from pathlib import Path

from src.companion.ref_ids import format_ref_id, parse_ref_id

IDLES_FILE: Path = Path(__file__).resolve().parents[2] / "data" / "Skyrim" / "skyrim_idles.csv"
COLUMN_ID: str = "id"
COLUMN_NAME: str = "idle_name"
# These animations are in Mantella.esp, which the companion mod does not need. Their IDs start with this instead of a number
OTHER_PLUGIN_PREFIX: str = "xx"


def load_gestures(file: Path = IDLES_FILE) -> dict[str, list[str]]:
    """Returns the animations of every gesture: name -> form IDs as eight hex digits. Empty if the table cannot be read"""
    gestures: dict[str, list[str]] = {}
    try:
        with open(file, newline="", encoding="utf-8") as opened:
            for row in csv.DictReader(opened):
                name = str(row.get(COLUMN_NAME) or "").strip().lower()
                id_text = str(row.get(COLUMN_ID) or "").strip()
                if name == "" or id_text.lower().startswith(OTHER_PLUGIN_PREFIX):
                    continue
                form_id = parse_ref_id(id_text)
                if form_id is None or form_id == 0:
                    continue
                gestures.setdefault(name, []).append(format_ref_id(form_id))
    except (OSError, csv.Error, UnicodeDecodeError):
        return {}
    return gestures


GESTURES: dict[str, list[str]] = load_gestures()


def pick_animation(name: str) -> str:
    """Returns the form ID of one of the animations of this gesture, or an empty text if there is no such gesture"""
    animations = GESTURES.get(name.strip().lower().replace(" ", "_"))
    if animations is None:
        return ""
    return random.choice(animations)
