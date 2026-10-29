#!/usr/bin/env python3
"""Deterministic Remote Control stand-in; never controls a real terminal."""

import json
import os
from pathlib import Path
import sys

path = Path(os.environ["FAKE_KITTY_STATE"])
args = sys.argv[1:]
command = args[args.index("--to") + 2 :]
state = json.loads(path.read_text())
if command == ["ls"]:
    print(json.dumps(state))
elif command and command[0] == "focus-window":
    window_id = int(command[-1].split(":")[1])
    found = False
    for os_window in state:
        for tab in os_window["tabs"]:
            tab["is_active"] = any(w["id"] == window_id for w in tab["windows"])
            for window in tab["windows"]:
                window["is_active"] = window["id"] == window_id
                found |= window["id"] == window_id
    if not found:
        sys.exit(1)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state))
    temp.replace(path)
else:
    sys.exit(2)
