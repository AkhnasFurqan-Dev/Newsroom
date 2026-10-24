"""Hold one Channel Press file lease in a separate process until stdin closes."""
import sys
from pathlib import Path

from channel_press.locking import FileLease

if __name__ == "__main__":
    with FileLease(Path(sys.argv[1])):
        print("ready", flush=True)
        sys.stdin.readline()
