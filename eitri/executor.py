"""Where model code runs.

A model file is Python, and every build, inspect and render executes it. Locally (the default) that
happens in a subprocess or the warm worker on this machine, as it always has. A hosted deployment
installs a remote executor with set_executor(): each job then runs in a fresh sandbox (eitri.sandbox is
the entry point inside the container), and this process only ships the model source in and takes the
outputs back.

With EITRI_NO_LOCAL_EXEC=1 this process must never execute model code: load_model() and every local
build path raise SandboxRequired. A hosted web process sets it, so a code path that was missed fails
closed instead of running someone's model on the server.

The job format is shared by both sides of the sandbox: a tar holding job.json and project/<files> goes
in; a tar of the job's output folder comes out. Everything that comes out is untrusted: unpack() takes
only plain files with safe relative names, within size and count limits.
"""
from __future__ import annotations

import io
import json
import os
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAX_INPUT_BYTES = 8 * 1024 * 1024          # model source + small inputs
MAX_OUTPUT_BYTES = 96 * 1024 * 1024        # geometry, renders, exports of one job
MAX_OUTPUT_FILES = 64
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class SandboxRequired(RuntimeError):
    """Model code was about to run in a process that must not run it."""


def local_exec_allowed() -> bool:
    return os.environ.get("EITRI_NO_LOCAL_EXEC", "") != "1"


def require_local_exec(what: str) -> None:
    if not local_exec_allowed():
        raise SandboxRequired(f"{what} executes model code, which this process may not do; it must run in the sandbox")


@dataclass
class ExecResult:
    code: int                                   # the job's exit code; -9 = killed (timeout, limits)
    stdout: str = ""                            # what the job printed (bounded)
    stderr: str = ""
    files: dict[str, bytes] = field(default_factory=dict)   # outputs, by safe relative name
    seconds: float = 0.0
    error: str | None = None                    # why the sandbox itself failed (not the model)


class Executor(Protocol):
    def run(self, kind: str, project_dir: Path, model_name: str, args: list[str], timeout: float, **job) -> ExecResult:
        """Run one job (build | python | render) on the project's model; extra job fields (code=,
        out_name=) travel in job.json."""
        ...


_CURRENT: Executor | None = None


def set_executor(executor: Executor | None) -> None:
    global _CURRENT
    _CURRENT = executor


def current() -> Executor | None:
    return _CURRENT


# ---------------------------------------------------------------------------- the job format

def safe_name(name: str) -> bool:
    """A relative path of safe components (no '..', no absolute, no hidden files, bounded depth)."""
    parts = name.replace("\\", "/").split("/")
    return 0 < len(parts) <= 4 and all(_SAFE_NAME.match(p) and p not in (".", "..") for p in parts)


def project_inputs(project_dir: Path, model_name: str) -> dict[str, bytes]:
    """What a job may see of a project: its model file and the files under inputs/ (bounded)."""
    if not safe_name(model_name) or "/" in model_name:
        raise ValueError(f"unsafe model file name: {model_name!r}")
    files = {model_name: (project_dir / model_name).read_bytes()}
    inputs = project_dir / "inputs"
    if inputs.is_dir():
        for f in sorted(inputs.iterdir()):
            if f.is_file() and not f.is_symlink() and safe_name(f.name):
                files[f"inputs/{f.name}"] = f.read_bytes()
    if sum(len(v) for v in files.values()) > MAX_INPUT_BYTES:
        raise ValueError("the model and its inputs are larger than the sandbox accepts")
    return files


def pack(job: dict, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
        add("job.json", json.dumps(job).encode())
        for name, data in files.items():
            if not safe_name(name):
                raise ValueError(f"unsafe input name: {name!r}")
            add(f"project/{name}", data)
    return buf.getvalue()


def unpack(data: bytes, max_bytes: int = MAX_OUTPUT_BYTES, max_files: int = MAX_OUTPUT_FILES) -> dict[str, bytes]:
    """The plain files of an untrusted tar, by name. Raises ValueError on anything else: links, devices,
    unsafe names, too many files, too many bytes."""
    if len(data) > max_bytes + 1024 * 1024:
        raise ValueError("the output is larger than the limit")
    out: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for m in tar:
            if m.isdir():
                continue
            if not m.isfile() or not safe_name(m.name):
                raise ValueError(f"refused output entry {m.name!r}")
            total += m.size
            if total > max_bytes or len(out) >= max_files:
                raise ValueError("the output exceeds the size or file-count limit")
            f = tar.extractfile(m)
            out[m.name] = f.read() if f else b""
    return out


def write_files(files: dict[str, bytes], dest: Path) -> list[Path]:
    """Write unpacked outputs under dest (names were validated by unpack; checked again here)."""
    dest = dest.resolve()
    written = []
    for name, data in files.items():
        if not safe_name(name):
            raise ValueError(f"unsafe output name {name!r}")
        target = (dest / name).resolve()
        if dest not in target.parents:
            raise ValueError(f"output {name!r} escapes its folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written


def sanitize_result(result: dict, out_dir: Path) -> dict:
    """A build's result.json from the sandbox, with every output path re-anchored in out_dir: only the file
    name is kept, and only if the job really returned a file of that name (written into out_dir), so a
    path the job names - any file on this machine - can only ever resolve to the job's own output."""
    outputs = {}
    for key, path in (result.get("outputs") or {}).items():
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        if safe_name(name) and (out_dir / name).is_file():
            outputs[str(key)] = str(out_dir / name)
    result = dict(result)
    result["outputs"] = outputs
    return result
