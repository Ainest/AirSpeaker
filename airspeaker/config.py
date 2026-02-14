"""AirSpeaker configuration constants."""

# --- HTTP Streaming Server ---
STREAM_PORT = 18573
STREAM_ENDPOINT = "/stream"

# --- Audio (ffmpeg) ---
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE = "192k"
BLACKHOLE_DEVICE = "BlackHole 2ch"

# --- ffmpeg low-latency flags ---
FFMPEG_CMD_TEMPLATE = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    # Low-latency input options
    "-fflags", "nobuffer",
    "-analyzeduration", "0",
    "-probesize", "32",
    # Input: BlackHole via AVFoundation (audio only)
    "-f", "avfoundation",
    "-i", ":{device}",
    # Output: MP3 CBR to stdout
    "-acodec", "libmp3lame",
    "-b:a", BITRATE,
    "-ar", str(SAMPLE_RATE),
    "-ac", str(CHANNELS),
    "-flush_packets", "1",
    "-f", "mp3",
    "pipe:1",
]

# --- Chromecast ---
CAST_DISCOVERY_TIMEOUT = 10  # seconds
CAST_RETRY_INTERVAL = 5  # seconds between reconnection attempts

# --- Misc ---
APP_NAME = "AirSpeaker"
