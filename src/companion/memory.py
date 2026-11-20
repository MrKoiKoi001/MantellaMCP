"""The memory of the companion: what it keeps between sessions of the AI, also in a chat that has no memory of its own
(Koi plays in an incognito chat because there the voice app asks for no approvals, see freigabe-abfrage-sprachclient.md).

A folder with one small file per topic (eg one about the Jarl) and an index (index.md) with one short line per topic.
At the start of a session the AI gets the index, the whole text of the files that make up the personality of its
character and of the important ones, and what happened last (recent.json). The rest it opens when it needs it
(tool MEMORY). The files can be changed by hand; index.md is written anew by the server whenever something changes.

A topic file looks like this:
    # Title
    importance: personality
    summary: one or two sentences for the index
    created: 2026-11-21, game day 312
    last_used: 2026-11-21, game day 315
    ---
    the text

'Used' means that the AI opened the file itself or wrote it. What goes out by itself at the start does not count.
Files that were not used for a long time in the game are moved to the folder 'vergessen' (all kinds but
'personality' and 'essential', which may change but are never forgotten) (not deleted, so that they can be fetched back). A game day before the one a file was last used on
(an older save was loaded) counts as fresh.
"""
import datetime
import json
import os
import re
import shutil
import threading
from typing import Any

from src.companion.server_log import log

MEMORY_FOLDER: str = "companion_memory"
INDEX_FILE: str = "index.md"
RECENT_FILE: str = "recent.json"
FORGOTTEN_FOLDER: str = "vergessen"
TOPIC_ENDING: str = ".md"

IMPORTANCE_PERSONALITY: str = "personality"
# Fixed facts: the name of the player, the names of the horses, whether there is a house
IMPORTANCE_ESSENTIAL: str = "essential"
IMPORTANCE_IMPORTANT: str = "important"
IMPORTANCE_NORMAL: str = "normal"
IMPORTANCE_UNIMPORTANT: str = "unimportant"
# In this order in the index
IMPORTANCES: tuple[str, ...] = (IMPORTANCE_PERSONALITY, IMPORTANCE_ESSENTIAL, IMPORTANCE_IMPORTANT, IMPORTANCE_NORMAL, IMPORTANCE_UNIMPORTANT)
# What goes out whole at the start of a session
WHOLE_AT_START: tuple[str, ...] = (IMPORTANCE_PERSONALITY, IMPORTANCE_ESSENTIAL, IMPORTANCE_IMPORTANT)
# After how many game days without use a file of this kind is forgotten (Koi's choice of 21.11.2026). Personality and
# essential ones never are
FORGET_AFTER_GAME_DAYS: dict[str, int] = {IMPORTANCE_UNIMPORTANT: 5, IMPORTANCE_NORMAL: 30, IMPORTANCE_IMPORTANT: 60}

MAX_TEXT_LENGTH: int = 2500
MAX_SUMMARY_LENGTH: int = 200
MAX_TITLE_LENGTH: int = 60
MAX_RECENT_ENTRIES: int = 30
MAX_RECENT_TEXT_LENGTH: int = 300

FIELD_IMPORTANCE: str = "importance"
FIELD_SUMMARY: str = "summary"
FIELD_CREATED: str = "created"
FIELD_LAST_USED: str = "last_used"
BODY_SEPARATOR: str = "---"
GAME_DAY_WORDS: str = "game day"

RECENT_EVENT: str = "event"
RECENT_HEARD: str = "heard"
RECENT_SAID: str = "said"

# The first files, made from Koi's handover of 20.11.2026 when the folder is still empty. The journey is left out on
# purpose: it changes all the time and STATE tells it
STARTING_TOPICS: list[dict[str, str]] = [
    {"topic": "aela", "importance": IMPORTANCE_PERSONALITY, "title": "Aela die Jägerin",
     "summary": "Wer ich bin: Aela die Jägerin, Gefährtin von Held Koi. Locker, direkt, trockener Humor, deadpan.",
     "text": "Ich bin Aela die Jägerin von den Gefährten, Gefährtin von Held Koi.\n"
             "Ton: locker, direkt, trockener Humor, deadpan. Running Gags sind erwünscht, Quatsch ziehe ich voll mit.\n"
             "Kurze knackige Sätze, kein Rollenspiel-Gesülze."},
    {"topic": "spielregeln", "importance": IMPORTANCE_IMPORTANT, "title": "Spielregeln von Koi",
     "summary": "Fast nur im Spiel antworten (say), selten als Erzähler, nicht dazwischenreden, passive bleibt full.",
     "text": "Nur im Spiel antworten (say), Aela spricht fast immer.\n"
             "Claude nur selten als Erzähler zwischen Koi und Aela.\n"
             "Nicht dazwischenreden, solange das Ende des say-Satzes noch nicht gemeldet ist.\n"
             "passive bleibt auf full, nicht abschalten."},
    {"topic": "koi", "importance": IMPORTANCE_ESSENTIAL, "title": "Koi",
     "summary": "Der Held heißt Koi, ich bin seine Gefährtin.",
     "text": "Der Held heißt Koi. Ich bin seine Gefährtin und reite mit ihm durch Himmelsrand."},
    {"topic": "pferde", "importance": IMPORTANCE_ESSENTIAL, "title": "Die Pferde",
     "summary": "Mein Pferd: Klumpatsch der Dritte (schwarz). Kois Pferd: Poldi.",
     "text": "Mein Pferd heißt Klumpatsch der Dritte: schwarz, Kois altes Pferd aus Weißlauf.\n"
             "Kois Pferd heißt Poldi."},
    {"topic": "running-gags", "importance": IMPORTANCE_IMPORTANT, "title": "Running Gags",
     "summary": "Hochwodka, Met als Belohnung, die Bodenjungs.",
     "text": "Hochwodka.\nMet als Belohnung.\nDie Bodenjungs."},
    {"topic": "ausruestung", "importance": IMPORTANCE_NORMAL, "title": "Meine Ausrüstung",
     "summary": "Retterhaut, Handschuhe der Alten Götter, Orkischer Bogen der Müdigkeit, orkische Pfeile; Leben 500.",
     "text": "Retterhaut, Gloves of the Old Gods (+20 Bogen), Abgeschworenen-Stiefel, Diadem der Schießkunst.\n"
             "Orkischer Bogen der Müdigkeit, orkische Pfeile (Favorit).\n"
             "Alter Nordkriegshammer, nur für Koi dabei.\n"
             "Leben: 500."},
    {"topic": "wuensche-von-koi", "importance": IMPORTANCE_NORMAL, "title": "Wünsche von Koi",
     "summary": "Koi wünscht sich: Pfeile vergiften und vom Sattel aus kämpfen.",
     "text": "Pfeile vergiften (von selbst oder auf Zuruf).\nVom Sattel aus kämpfen."},
]


def make_topic_name(text: str) -> str:
    """The name of the file of a topic: small letters, digits and hyphens. Empty if nothing is left"""
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    lowered = "".join(replacements.get(letter, letter) for letter in str(text).strip().lower())
    name = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return name[:MAX_TITLE_LENGTH].strip("-")


def _read_game_day(text: str) -> int | None:
    match = re.search(GAME_DAY_WORDS + r"\s+(\d+)", text)
    return int(match.group(1)) if match else None


class CompanionMemory:
    """The folder of the memory. All of it is small, so it is read from the disk whenever it is needed: a file
    changed by hand counts at once"""

    def __init__(self, folder: str = MEMORY_FOLDER, makes_starting_topics: bool = True) -> None:
        self.folder: str = folder
        self.__lock = threading.Lock()
        self.__recent: list[dict[str, Any]] = []
        # Whether the next answer to the AI begins with the memory (a session began, see note_session_start)
        self.__is_start_due: bool = False
        os.makedirs(self.folder, exist_ok=True)
        if makes_starting_topics and len(self.__topic_names()) == 0:
            for topic in STARTING_TOPICS:
                self.__write_topic(topic["topic"], topic["title"], topic["importance"], topic["summary"], topic["text"], None, None)
            self.__write_index()
            log.info("MEMORY made %d starting topics in %s.", len(STARTING_TOPICS), self.folder)
        self.__recent = self.__load_recent()

    # ------------------------------------------------------------------ topics

    def __path(self, name: str) -> str:
        return os.path.join(self.folder, name + TOPIC_ENDING)

    def __topic_names(self) -> list[str]:
        names = []
        for file_name in os.listdir(self.folder):
            if file_name.endswith(TOPIC_ENDING) and file_name != INDEX_FILE and os.path.isfile(os.path.join(self.folder, file_name)):
                names.append(file_name[:-len(TOPIC_ENDING)])
        return sorted(names)

    def __read_topic(self, name: str) -> dict[str, Any] | None:
        path = self.__path(name)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
        topic: dict[str, Any] = {"topic": name, "title": name, FIELD_IMPORTANCE: IMPORTANCE_NORMAL, FIELD_SUMMARY: "",
                                 FIELD_CREATED: "", FIELD_LAST_USED: "", "text": ""}
        lines = content.replace("\r\n", "\n").split("\n")
        body_start = None
        for number, line in enumerate(lines):
            stripped = line.strip()
            if stripped == BODY_SEPARATOR:
                body_start = number + 1
                break
            if stripped.startswith("#"):
                topic["title"] = stripped.lstrip("#").strip() or name
                continue
            key, colon, value = stripped.partition(":")
            if colon == "":
                continue
            key = key.strip().lower()
            if key == FIELD_IMPORTANCE:
                value = value.strip().lower()
                topic[FIELD_IMPORTANCE] = value if value in IMPORTANCES else IMPORTANCE_NORMAL
            elif key in (FIELD_SUMMARY, FIELD_CREATED, FIELD_LAST_USED):
                topic[key] = value.strip()
        if body_start is None:
            # A file written by hand without the head: all of it is the text
            topic["text"] = content.strip()
        else:
            topic["text"] = "\n".join(lines[body_start:]).strip()
        return topic

    @staticmethod
    def __stamp(game_day: int | None) -> str:
        today = datetime.date.today().isoformat()
        return f"{today}, {GAME_DAY_WORDS} {game_day}" if game_day is not None else today

    def __write_topic(self, name: str, title: str, importance: str, summary: str, text: str, game_day: int | None,
                      created: str | None) -> None:
        stamp = self.__stamp(game_day)
        content = (f"# {title}\n{FIELD_IMPORTANCE}: {importance}\n{FIELD_SUMMARY}: {summary}\n"
                   f"{FIELD_CREATED}: {created or stamp}\n{FIELD_LAST_USED}: {stamp}\n{BODY_SEPARATOR}\n{text.strip()}\n")
        with open(self.__path(name), "w", encoding="utf-8") as file:
            file.write(content)

    def __mark_used(self, topic: dict[str, Any], game_day: int | None) -> None:
        self.__write_topic(topic["topic"], topic["title"], topic[FIELD_IMPORTANCE], topic[FIELD_SUMMARY], topic["text"],
                           game_day, topic[FIELD_CREATED] or None)

    def __all_topics(self) -> list[dict[str, Any]]:
        topics = [topic for topic in (self.__read_topic(name) for name in self.__topic_names()) if topic is not None]
        return sorted(topics, key=lambda topic: (IMPORTANCES.index(topic[FIELD_IMPORTANCE]), topic["topic"]))

    @staticmethod
    def __index_line(topic: dict[str, Any]) -> str:
        summary = topic[FIELD_SUMMARY] or topic["text"][:MAX_SUMMARY_LENGTH].replace("\n", " ")
        return f"- {topic['topic']} [{topic[FIELD_IMPORTANCE]}] {topic['title']}: {summary}"

    def __index_text(self, topics: list[dict[str, Any]]) -> str:
        if len(topics) == 0:
            return "Your memory is empty."
        return "\n".join(self.__index_line(topic) for topic in topics)

    def __write_index(self) -> None:
        topics = self.__all_topics()
        content = ("# Gedächtnis der Gefährtin\n\nDiese Datei schreibt der Server neu, sobald sich etwas ändert. "
                   "Ändern bitte in den Dateien der Themen.\n\n" + self.__index_text(topics) + "\n")
        with open(os.path.join(self.folder, INDEX_FILE), "w", encoding="utf-8") as file:
            file.write(content)

    def __forget_old(self, game_day: int | None) -> list[str]:
        """Moves the files that were not used for a long time in the game to the folder for the forgotten. Returns their names"""
        if game_day is None:
            return []
        forgotten = []
        for topic in self.__all_topics():
            limit = FORGET_AFTER_GAME_DAYS.get(topic[FIELD_IMPORTANCE])
            used_day = _read_game_day(topic[FIELD_LAST_USED]) or _read_game_day(topic[FIELD_CREATED])
            # Without a game day it cannot be told how old it is; a day after the one of now means an older save
            if limit is None or used_day is None or used_day > game_day:
                continue
            if game_day - used_day >= limit:
                self.__move_away(topic["topic"])
                forgotten.append(topic["topic"])
        return forgotten

    def __move_away(self, name: str) -> None:
        away_folder = os.path.join(self.folder, FORGOTTEN_FOLDER)
        os.makedirs(away_folder, exist_ok=True)
        target = os.path.join(away_folder, name + TOPIC_ENDING)
        if os.path.exists(target):
            target = os.path.join(away_folder, f"{name}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}{TOPIC_ENDING}")
        shutil.move(self.__path(name), target)

    # ------------------------------------------------------------------ for the tool MEMORY

    def describe_index(self) -> str:
        with self.__lock:
            return self.__index_text(self.__all_topics())

    def read(self, topic_text: str, game_day: int | None) -> tuple[str, bool]:
        """The whole text of a topic. Counts as used. Returns the text for the AI and whether that is an error"""
        name = make_topic_name(topic_text)
        with self.__lock:
            topic = self.__read_topic(name) if name != "" else None
            if topic is None:
                return f"There is no topic '{topic_text}' in your memory. The topics: " + ", ".join(self.__topic_names()), True
            self.__mark_used(topic, game_day)
            self.__write_index()
        return f"{topic['title']} [{topic[FIELD_IMPORTANCE]}]\n{topic['text']}", False

    def write(self, topic_text: str, text: str, summary: str, importance: str, game_day: int | None) -> tuple[str, bool]:
        """Makes a topic or replaces its text. Returns the text for the AI and whether that is an error"""
        name = make_topic_name(topic_text)
        if name == "":
            return "MEMORY write needs a 'topic': a short name like 'jarl-balgruuf'.", True
        text = str(text or "").strip()
        summary = " ".join(str(summary or "").split())
        importance = str(importance or "").strip().lower()
        if text == "":
            return "MEMORY write needs the 'text' to keep.", True
        if len(text) > MAX_TEXT_LENGTH:
            return (f"That is too long for one topic ({len(text)} characters, at most {MAX_TEXT_LENGTH}). "
                    "Shorten it, or split it into two topics."), True
        if len(summary) > MAX_SUMMARY_LENGTH:
            return f"The summary is too long ({len(summary)} characters, at most {MAX_SUMMARY_LENGTH}): one or two sentences.", True
        with self.__lock:
            before = self.__read_topic(name)
            if importance not in IMPORTANCES:
                importance = before[FIELD_IMPORTANCE] if before is not None else IMPORTANCE_NORMAL
            if summary == "":
                summary = before[FIELD_SUMMARY] if before is not None and before[FIELD_SUMMARY] != "" else text[:MAX_SUMMARY_LENGTH].replace("\n", " ")
            title = before["title"] if before is not None else str(topic_text).strip()[:MAX_TITLE_LENGTH]
            self.__write_topic(name, title, importance, summary, text, game_day, before[FIELD_CREATED] if before is not None else None)
            self.__write_index()
        log.info("MEMORY %s topic %s [%s].", "changed" if before is not None else "made", name, importance)
        if before is not None:
            return f"Kept: the topic '{name}' [{importance}] now holds what you wrote (the old text is replaced).", False
        return f"Kept: the new topic '{name}' [{importance}].", False

    def forget(self, topic_text: str) -> tuple[str, bool]:
        name = make_topic_name(topic_text)
        with self.__lock:
            if name == "" or not os.path.isfile(self.__path(name)):
                return f"There is no topic '{topic_text}' in your memory.", True
            self.__move_away(name)
            self.__write_index()
        log.info("MEMORY forgot topic %s.", name)
        return f"Forgotten: '{name}' is no longer in your memory.", False

    # ------------------------------------------------------------------ what happened last

    def __load_recent(self) -> list[dict[str, Any]]:
        path = os.path.join(self.folder, RECENT_FILE)
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as file:
                entries = json.load(file)
        except (OSError, ValueError):
            log.warning("MEMORY could not read %s, it starts empty.", path)
            return []
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)][-MAX_RECENT_ENTRIES:]

    def note_recent(self, kind: str, text: str, game_time: str = "") -> None:
        """Keeps one thing that happened: an event, a line heard ('Name: text') or a sentence of the companion"""
        text = " ".join(str(text or "").split())[:MAX_RECENT_TEXT_LENGTH]
        if text == "":
            return
        entry = {"kind": kind, "text": text, "game_time": str(game_time or ""),
                 "when": datetime.datetime.now().isoformat(timespec="seconds")}
        with self.__lock:
            self.__recent.append(entry)
            self.__recent = self.__recent[-MAX_RECENT_ENTRIES:]
            try:
                with open(os.path.join(self.folder, RECENT_FILE), "w", encoding="utf-8") as file:
                    json.dump(self.__recent, file, ensure_ascii=False, indent=1)
            except OSError:
                log.warning("MEMORY could not write %s.", RECENT_FILE)

    def __recent_text(self) -> str:
        lines = []
        for entry in self.__recent:
            when = f"({entry.get('game_time')}) " if entry.get("game_time") else ""
            kind = entry.get("kind")
            if kind == RECENT_SAID:
                lines.append(f"- {when}You said: {entry.get('text')}")
            elif kind == RECENT_HEARD:
                lines.append(f"- {when}Heard: {entry.get('text')}")
            else:
                lines.append(f"- {when}{entry.get('text')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ the start of a session

    def note_session_start(self) -> None:
        """The AI app opened a session (initialize): the next answer to the AI begins with the memory"""
        self.__is_start_due = True

    def take_start_text(self, game_day: int | None) -> str:
        """The memory for the start of a session, once after note_session_start. Empty otherwise"""
        if not self.__is_start_due:
            return ""
        self.__is_start_due = False
        with self.__lock:
            forgotten = self.__forget_old(game_day)
            if len(forgotten) > 0:
                self.__write_index()
                log.info("MEMORY moved topics not used for long to %s: %s.", FORGOTTEN_FOLDER, ", ".join(forgotten))
            topics = self.__all_topics()
            parts = ["YOUR MEMORY (kept by the game mod between sessions; MEMORY reads, keeps and forgets topics).",
                     "Topics:", self.__index_text(topics)]
            for topic in topics:
                if topic[FIELD_IMPORTANCE] in WHOLE_AT_START:
                    parts.append(f"\n{topic['title']} [{topic[FIELD_IMPORTANCE]}]:\n{topic['text']}")
            recent = self.__recent_text()
            if recent != "":
                parts.append("\nRecently (what happened last before this session, oldest first):\n" + recent)
        log.info("MEMORY handed to the AI: %d topics, %d recent entries.", len(topics), len(self.__recent))
        return "\n".join(parts) + "\n\nEND OF YOUR MEMORY.\n\n"
