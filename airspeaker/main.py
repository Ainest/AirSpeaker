"""AirSpeaker entry point."""

import logging
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Verify ffmpeg is available
    import shutil

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found. Install via: brew install ffmpeg", file=sys.stderr)
        sys.exit(1)

    from .app import AirSpeakerApp

    app = AirSpeakerApp()
    app.run()


if __name__ == "__main__":
    main()
