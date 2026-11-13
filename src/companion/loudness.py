"""Makes the sound of what the companion says louder or quieter, for the setting voice_volume and for say with 'loud'.

The sound files of the voices already use nearly the whole range a sound file has, so simply multiplying would cut
the peaks off flat, which crackles. Above a threshold the sound is therefore pressed together softly instead: the quiet
parts get louder in full, the loud parts only a little. That is what makes a voice sound raised.
"""
import array
import math
import sys
import wave

SAMPLE_BYTES: int = 2
HIGHEST_SAMPLE: float = 32767.0
# Up to this part of the full range the sound is only multiplied, above it is pressed together
SOFT_LIMIT_START: float = 0.6
LOWEST_FACTOR: float = 0.1
HIGHEST_FACTOR: float = 4.0
# What 'loud' multiplies the sound by, on top of the setting voice_volume
LOUD_FACTOR: float = 2.5


def scale_sample(value: float, factor: float) -> float:
    """One sample between -1 and 1 times the factor, pressed together softly above SOFT_LIMIT_START so that it stays below 1"""
    scaled = value * factor
    size = abs(scaled)
    if size <= SOFT_LIMIT_START:
        return scaled
    room = 1.0 - SOFT_LIMIT_START
    pressed = SOFT_LIMIT_START + room * math.tanh((size - SOFT_LIMIT_START) / room)
    return pressed if scaled > 0 else -pressed


def change_volume(sound_file: str, factor: float) -> bool:
    """Rewrites the sound file with its volume multiplied by the factor (1 = as it is)

    Returns:
        Whether the file was changed. Only 16-bit WAV files are, which is what Piper and XTTS make
    """
    factor = min(max(factor, LOWEST_FACTOR), HIGHEST_FACTOR)
    if abs(factor - 1.0) < 0.01:
        return False
    try:
        with wave.open(sound_file, "rb") as opened:
            if opened.getsampwidth() != SAMPLE_BYTES or opened.getcomptype() != "NONE":
                return False
            settings = opened.getparams()
            samples = array.array("h")
            samples.frombytes(opened.readframes(opened.getnframes()))
    except (OSError, wave.Error, EOFError, ValueError):
        return False
    # WAV files are little-endian whatever the machine is
    if sys.byteorder == "big":
        samples.byteswap()
    if factor <= 1.0:
        changed = array.array("h", (int(sample * factor) for sample in samples))
    else:
        changed = array.array("h", (int(scale_sample(sample / HIGHEST_SAMPLE, factor) * HIGHEST_SAMPLE) for sample in samples))
    if sys.byteorder == "big":
        changed.byteswap()
    try:
        with wave.open(sound_file, "wb") as opened:
            opened.setparams(settings)
            opened.writeframes(changed.tobytes())
    except (OSError, wave.Error):
        return False
    return True
