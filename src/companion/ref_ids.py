"""Ref IDs of characters, the way the game mod and the AI exchange them.

A ref_id is the form ID of a character in the game, written as eight hex digits (eg 000A2C94).
Papyrus cannot read hex text, so for the game the server adds the same ID as a number.
"""

REF_ID_DIGITS: int = 8


def parse_ref_id(text: str) -> int | None:
    """Returns the form ID as a number from 0 to FFFFFFFF, or None if the text is not a ref_id"""
    cleaned = text.strip()
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) == 0 or len(cleaned) > REF_ID_DIGITS:
        return None
    if any(character not in "0123456789abcdefABCDEF" for character in cleaned):
        return None
    return int(cleaned, 16)


def format_ref_id(form_id: int) -> str:
    """Returns the form ID the way the game mod writes it: eight hex digits in upper case"""
    return f"{form_id:08X}"


def split_form_id(form_id: int) -> tuple[int, int]:
    """Returns the upper and the lower 16 bits of the form ID.

    Papyrus only knows signed 32 bit numbers, and IDs from 80000000 on would be negative there.
    Two halves are always small positive numbers, and the game mod puts them together with bit operations.
    """
    return form_id >> 16, form_id & 0xFFFF
