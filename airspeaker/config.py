"""AirSpeaker configuration constants."""

# --- HTTP Streaming Server ---
STREAM_PORT = 18573
STREAM_ENDPOINT = "/stream"

# --- Audio (ffmpeg) ---
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE = "192k"
BLACKHOLE_DEVICE = "BlackHole 2ch"

# --- Codec selection ---
# "aac" = lower latency (ADTS framing, Chromecast native)
# "mp3" = wider compatibility
CODEC = "aac"

# Codec-specific ffmpeg output flags and MIME types
CODEC_PROFILES = {
    "aac": {
        "ffmpeg_args": [
            "-acodec", "aac",
            "-b:a", BITRATE,
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-flush_packets", "1",
            "-f", "adts",
        ],
        "content_type": "audio/aac",
    },
    "mp3": {
        "ffmpeg_args": [
            "-acodec", "libmp3lame",
            "-b:a", BITRATE,
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-flush_packets", "1",
            "-f", "mp3",
        ],
        "content_type": "audio/mpeg",
    },
}

# --- ffmpeg base flags (codec-independent) ---
FFMPEG_BASE_ARGS = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    # Low-latency input options
    "-fflags", "nobuffer",
    "-analyzeduration", "100000",  # 0.1s (safe but fast)
    "-probesize", "32768",         # 32KB (reliable detection)
    # Input: BlackHole via AVFoundation (audio only)
    "-f", "avfoundation",
    "-i", ":{device}",
]


def build_ffmpeg_cmd(device: str) -> list[str]:
    """Build the full ffmpeg command for the selected codec."""
    profile = CODEC_PROFILES[CODEC]
    cmd = [arg.replace("{device}", device) for arg in FFMPEG_BASE_ARGS]
    cmd += profile["ffmpeg_args"]
    cmd.append("pipe:1")
    return cmd


def stream_content_type() -> str:
    """Return the HTTP Content-Type for the selected codec."""
    return CODEC_PROFILES[CODEC]["content_type"]


# --- Chromecast ---
CAST_DISCOVERY_TIMEOUT = 10  # seconds
CAST_RETRY_INTERVAL = 5  # seconds between reconnection attempts

# --- Misc ---
APP_NAME = "AirSpeaker"
