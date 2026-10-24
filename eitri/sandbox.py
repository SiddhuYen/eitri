"""The program each sandbox container runs: one job, then exit.

    docker run -i ... eitri-sandbox   <  job tar   >  output tar

stdin: a tar with job.json and project/<model file and inputs> (see eitri.executor.pack). stdout: a tar
of the job's output folder plus status.json ({"code": ...}). Everything the model prints goes to stderr,
so it cannot corrupt the output stream. Jobs:

    build      the build runner (eitri._runner): geometry, checks, review image, exports
    python     run a snippet with the built parts in scope (the agent's `inspect` tool); output in stdout.txt
    render     extra views (eitri._render_cli; the agent's `view` tool)
    selfcheck  what this container can see: user, environment variable names, network, writable paths

The container is the security boundary (no network, read-only image, non-root, limits); this program
only keeps the protocol clean.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tarfile
import traceback
from pathlib import Path

from .executor import MAX_INPUT_BYTES, safe_name


def _read_input(work: Path) -> dict:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1024 * 1024)
    inp = work / "in"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for m in tar:
            if not m.isfile() or not (m.name == "job.json" or (m.name.startswith("project/") and safe_name(m.name[8:]))):
                raise ValueError(f"refused input entry {m.name!r}")
            target = inp / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(m).read())
    return json.loads((inp / "job.json").read_text(encoding="utf-8"))


def _python_job(model: Path, code: str, out: Path) -> int:
    from eitri.model import load_model
    import eitri
    scope: dict = {"__name__": "__inspect__"}
    exec("from eitri import *\nfrom eitri import bd\n", scope)               # the names the docstring promises
    buf = io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(buf):
        try:
            m = load_model(model)
            built = m.build()
            from eitri.harness.model import Harness
            if isinstance(built, Harness):
                from eitri.harness.inspect import harness_scope
                scope.update(harness_scope(built, m.params))
            else:
                scope.update(p=m.params, parts=built, model=built.compound, eitri=eitri)
            exec(compile(code, "<inspect>", "exec"), scope)
        except Exception:
            traceback.print_exc(file=buf)
            rc = 1
    (out / "stdout.txt").write_text(buf.getvalue()[-200_000:], encoding="utf-8")
    return rc


def _selfcheck(out: Path, scan: bool = False) -> int:
    report: dict = {"uid": os.getuid(), "gid": os.getgid(), "env": sorted(os.environ)}
    net = {}
    for host, port in (("1.1.1.1", 53), ("api.anthropic.com", 443), ("169.254.169.254", 80)):
        try:
            with socket.create_connection((host, port), timeout=2):
                net[f"{host}:{port}"] = "connected"
        except OSError as exc:
            net[f"{host}:{port}"] = f"blocked ({type(exc).__name__})"
    report["network"] = net
    writable = {}
    for p in ("/", "/usr", "/etc", "/home", "/opt", "/work", "/tmp"):
        try:
            probe = Path(p) / ".eitri-write-probe"
            probe.write_text("x")
            probe.unlink()
            writable[p] = True
        except OSError:
            writable[p] = False
    report["writable"] = writable
    if scan:                                                   # an Anthropic key anywhere readable in the image?
        import re
        pattern = re.compile(rb"sk-ant-[a-z0-9]{2,8}-[A-Za-z0-9_\-]{20,}")
        found = [k for k, v in os.environ.items() if pattern.search(v.encode())]
        scanned = 0
        for root in ("/etc", "/home", "/root", "/opt", "/srv", "/usr/local", "/app", "/src", "/work", "/tmp"):
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                for fn in filenames:
                    f = os.path.join(dirpath, fn)
                    try:
                        if os.path.islink(f) or os.path.getsize(f) > 4_000_000:
                            continue
                        with open(f, "rb") as fh:
                            if pattern.search(fh.read()):
                                found.append(f)
                        scanned += 1
                    except OSError:
                        continue
        report["key_found"] = found[:20]
        report["files_scanned"] = scanned
    (out / "selfcheck.json").write_text(json.dumps(report), encoding="utf-8")
    return 0


def main() -> int:
    out_fd = os.dup(1)                      # the real stdout carries only the output tar
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    work = Path(os.environ.get("EITRI_SANDBOX_WORK", "/work"))
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)
    code = 1
    try:
        job = _read_input(work)
        kind = job.get("kind")
        args = [str(a) for a in job.get("args") or []]
        model_name = str(job.get("model") or "")
        if kind != "selfcheck" and (not safe_name(model_name) or "/" in model_name):
            raise ValueError("bad model name")
        project = work / "in" / "project"
        model = project / model_name
        if kind != "selfcheck":
            os.chdir(project)                                  # a model may import sibling helper files
            sys.path.insert(0, str(project))
        if kind == "build":
            from eitri import _runner
            code = _runner.main(["--model", str(model), "--out", str(out)] + args)
        elif kind == "python":
            code = _python_job(model, str(job.get("code") or ""), out)
        elif kind == "render":
            name = str(job.get("out_name") or "view.png")
            if not safe_name(name) or "/" in name:
                raise ValueError("bad output name")
            from eitri import _render_cli
            code = _render_cli.main(["--model", str(model), "--out", str(out / name)] + args)
        elif kind == "selfcheck":
            code = _selfcheck(out, scan=bool(job.get("scan")))
        else:
            raise ValueError(f"unknown job kind {kind!r}")
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
    except BaseException:
        traceback.print_exc()
        code = 1
    (out / "status.json").write_text(json.dumps({"code": int(code or 0)}), encoding="utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted(out.rglob("*")):
            if f.is_file() and not f.is_symlink():
                tar.add(str(f), arcname=str(f.relative_to(out)), recursive=False)
    data = buf.getvalue()
    view = memoryview(data)
    while view:
        n = os.write(out_fd, view)
        view = view[n:]
    return 0


if __name__ == "__main__":
    sys.exit(main())
