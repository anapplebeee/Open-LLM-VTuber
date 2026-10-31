import base64
import io
import shutil
import subprocess

from pydub import AudioSegment
from pydub.utils import make_chunks
from ..agent.output_types import Actions, DisplayText


def _get_volume_by_chunks(audio: AudioSegment, chunk_length_ms: int) -> list:
    """
    Calculate the normalized volume (RMS) for each chunk of the audio.

    Parameters:
        audio (AudioSegment): The audio segment to process.
        chunk_length_ms (int): The length of each audio chunk in milliseconds.

    Returns:
        list: Normalized volumes for each chunk.
    """
    chunks = make_chunks(audio, chunk_length_ms)
    volumes = [chunk.rms for chunk in chunks]
    max_volume = max(volumes)
    if max_volume == 0:
        raise ValueError("Audio is empty or all zero.")
    return [volume / max_volume for volume in volumes]


def _audio_to_wav_bytes(audio_path: str) -> bytes:
    """Convert any TTS output to WAV without requiring ffprobe."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ffmpeg = None
    if ffmpeg:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                audio_path,
                "-f",
                "wav",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
        return result.stdout
    audio = AudioSegment.from_file(audio_path)
    return audio.export(format="wav").read()


def prepare_audio_payload(
    audio_path: str | None,
    chunk_length_ms: int = 20,
    display_text: DisplayText = None,
    actions: Actions = None,
    forwarded: bool = False,
) -> dict[str, any]:
    """
    Prepares the audio payload for sending to a broadcast endpoint.
    If audio_path is None, returns a payload with audio=None for silent display.

    Parameters:
        audio_path (str | None): The path to the audio file to be processed, or None for silent display
        chunk_length_ms (int): The length of each audio chunk in milliseconds
        display_text (DisplayText, optional): Text to be displayed with the audio
        actions (Actions, optional): Actions associated with the audio

    Returns:
        dict: The audio payload to be sent
    """
    if isinstance(display_text, DisplayText):
        display_text = display_text.to_dict()
    action_payload = actions.to_dict() if actions else None
    # Current OLV frontend reads expressions from the top-level audio payload
    # while the backend historically exposed only the Actions object.
    expressions = action_payload.get("expressions") if action_payload else None

    if not audio_path:
        # Return payload for silent display
        return {
            "type": "audio",
            "audio": None,
            "volumes": [],
            "slice_length": chunk_length_ms,
            "display_text": display_text,
            "actions": action_payload,
            "expressions": expressions,
            "forwarded": forwarded,
        }

    try:
        audio_bytes = _audio_to_wav_bytes(audio_path)
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
    except Exception as e:
        raise ValueError(
            f"Error loading or converting generated audio file to wav file '{audio_path}': {e}"
        )
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    volumes = _get_volume_by_chunks(audio, chunk_length_ms)

    payload = {
        "type": "audio",
        "audio": audio_base64,
        "volumes": volumes,
        "slice_length": chunk_length_ms,
        "display_text": display_text,
        "actions": action_payload,
        "expressions": expressions,
        "forwarded": forwarded,
    }

    return payload


# Example usage:
# payload, duration = prepare_audio_payload("path/to/audio.mp3", display_text="Hello", expression_list=[0,1,2])
