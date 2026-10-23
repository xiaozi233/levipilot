"""Isolate CLI characterization from the game and the real filesystem."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

from driver.harness.errors import ModPipeError
from driver.harness.record import RecordEntry


def invoke_cli(module: ModuleType, argv: list[str], failure: str | None = None) -> dict[str, Any]:
    calls: list[Any] = []
    entry = RecordEntry(7, 1.5, 65.62, -2.5, 0, 0, 0, 90, 0, 64, 1, 0, 0, 0, 20000)

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append(["connect", args, kwargs])

        def __enter__(self) -> Client:
            if failure == "connect":
                raise ModPipeError("fixture disconnected")
            return self

        def __exit__(self, *exc: Any) -> None:
            calls.append(["close"])

        def __getattr__(self, name: str) -> Callable[..., Any]:
            def call(*args: Any, **kwargs: Any) -> Any:
                calls.append([name, args, kwargs])
                if failure == "request":
                    raise ModPipeError("fixture request failed")
                if failure == "interrupt":
                    raise KeyboardInterrupt
                if name == "profile_dump":
                    return {"event": name, "profile": {"name": "测试", "runtime": True}}
                if name == "record":
                    return [{"event": "tick", "tick": 1}, {"event": "tick", "tick": 2}]
                if name == "record_dump":
                    return {"event": "record_dump", "rows": [entry.to_dict()]}
                if name == "record_dump_all":
                    return [entry]
                return {"event": name, "args": args, "kwargs": kwargs, "text": "测试"}

            return call

    stdout, stderr = io.StringIO(), io.StringIO()
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
        Path("spool.jsonl").write_text(
            '{"event":"tick","tick":1,"text":"测试"}\n{"event":"tick","tick":2}\n',
            encoding="utf-8",
        )
        Path("route.json").write_text('[{"x":1,"z":2}]', encoding="utf-8")
        with (
            patch.object(module, "ModClient", Client),
            patch("sys.argv", ["harness"]),
            patch.dict(os.environ, {"COLUMNS": "80"}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                result["code"] = module.main(argv)
            except (SystemExit, ValueError) as exc:
                result["exception"] = [type(exc).__name__, str(exc)]
        result["files"] = {
            path.as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(Path().rglob("*"))
            if path.is_file() and path.name not in {"spool.jsonl", "route.json"}
        }
    result.update(stdout=stdout.getvalue(), stderr=stderr.getvalue(), calls=calls)
    # Normalize tuple arguments to their JSON fixture representation.
    return json.loads(json.dumps(result, ensure_ascii=False))
