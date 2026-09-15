from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path


def emit(log_path: Path, direction: str, raw: str) -> None:
    record = {
        "ts_unix": time.time(),
        "direction": direction,
        "raw": raw.rstrip("\n"),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: stdio_tap.py <wire.jsonl> <child> [args...]", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_cmd = sys.argv[2:]

    proc = subprocess.Popen(
        child_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    def pump_stdin() -> None:
        try:
            for line in sys.stdin:
                emit(log_path, "client_to_server", line)
                proc.stdin.write(line)
                proc.stdin.flush()
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    thread = threading.Thread(target=pump_stdin, daemon=True)
    thread.start()

    for line in proc.stdout:
        emit(log_path, "server_to_client", line)
        sys.stdout.write(line)
        sys.stdout.flush()

    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
