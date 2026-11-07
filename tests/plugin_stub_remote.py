"""A stub of eitri.io's combined-workspace MCP server (briefs/2026-11-06-plugin-combined-remote-mcp.md), for the
plugin's tests: streamable HTTP (JSON responses), bearer-token auth, the eight combined_* tools over an in-memory
project store, and a toy build whose checks fail until the files fix them. It follows the shared tool contract
only; nothing in it is how eitri.io builds or checks a design.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = {
    "combined_reference": {"topic": {"type": "string"}},
    "combined_new_project": {"name": {"type": "string"}, "goal": {"type": "string"}},
    "combined_list_projects": {},
    "combined_read_files": {"project_id": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}},
    "combined_put_files": {"project_id": {"type": "string"}, "files": {"type": "object"},
                           "delete": {"type": "array", "items": {"type": "string"}}},
    "combined_build": {"project_id": {"type": "string"}},
    "combined_results": {"project_id": {"type": "string"}},
    "combined_export": {"project_id": {"type": "string"}, "formats": {"type": "array", "items": {"type": "string"}}},
}
REQUIRED = {"combined_new_project": ["name", "goal"], "combined_read_files": ["project_id"],
            "combined_put_files": ["project_id", "files"], "combined_build": ["project_id"],
            "combined_results": ["project_id"], "combined_export": ["project_id", "formats"]}
NO_TOKEN = "No valid eitri.io token. Make one at eitri.io/account, then run `eitri plugin login`."


class StubRemote:
    def __init__(self, token: str = "eitri_pat_stubtoken0123456789"):
        self.token = token
        self.projects: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------------ the contract
    def call(self, name: str, a: dict) -> tuple[str, bool]:
        self.calls.append((name, a))
        if name == "combined_reference":
            return ("Combined project layout (stub): cad/<part>.py, wiring/<harness>.py, motion/<mechanism>.py, "
                    "electronics/<circuit>.py, firmware/<sketch>.py; links between files are declared in system.py."), False
        if name == "combined_new_project":
            pid = uuid.uuid4().hex[:8]
            self.projects[pid] = {"name": a["name"], "goal": a["goal"], "files": {}, "results": None}
            return json.dumps({"project_id": pid, "view": f"https://eitri.io/p/{pid}"}), False
        if name == "combined_list_projects":
            return json.dumps([{"project_id": k, "name": v["name"]} for k, v in self.projects.items()]), False
        p = self.projects.get(a.get("project_id", ""))
        if p is None:
            return f"no project {a.get('project_id')!r}", True
        if name == "combined_read_files":
            paths = a.get("paths") or list(p["files"])
            return json.dumps({k: p["files"][k] for k in paths if k in p["files"]}), False
        if name == "combined_put_files":
            p["files"].update(a.get("files") or {})
            for d in a.get("delete") or []:
                p["files"].pop(d, None)
            return json.dumps({"files": sorted(p["files"])}), False
        if name == "combined_build":
            p["results"] = self._build(p)
            return json.dumps(p["results"]), False
        if name == "combined_results":
            return json.dumps(p["results"] or {"state": "not built"}), False
        if name == "combined_export":
            pid = a["project_id"]
            return json.dumps({f: f"https://eitri.io/p/{pid}/export.{f}" for f in a["formats"]}), False
        return f"unknown tool {name}", True

    @staticmethod
    def _build(p: dict) -> dict:
        files = p["files"]
        checks, steps = [], []
        motion = files.get("motion/arm.py", "")
        if not motion:
            steps.append("motion not built yet: write motion/arm.py")
        else:
            ok = "MG996R" in motion
            checks.append({"name": "torque joint shoulder", "severity": "pass" if ok else "fail",
                           "measured": "4.1 kg·cm" if ok else "4.1 kg·cm", "limit": "9.4 kg·cm stall" if ok else "1.8 kg·cm stall",
                           "source": "datasheet (stub)"})
            if not ok:
                steps.append("torque joint shoulder fails because the SG90 stalls at 1.8 kg·cm: use a stronger servo")
        if "wiring/harness.py" not in files:
            steps.append("wiring not built yet: write wiring/harness.py")
        else:
            checks.append({"name": "servo supply wire ampacity", "severity": "pass", "measured": "2.5 A", "limit": "5 A",
                           "source": "wire table (stub)"})
        return {"checks": checks, "next_steps": steps, "queue_position": 0,
                "all_pass": bool(checks) and not steps and all(c["severity"] == "pass" for c in checks)}

    # ------------------------------------------------------------------ HTTP / JSON-RPC
    def _handler(self):
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj=None, headers: dict | None = None):
                body = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                if obj is not None:
                    self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(405, {"error": "use POST"})

            def do_DELETE(self):
                self._send(200, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                msg = json.loads(self.rfile.read(n) or b"{}")
                if self.headers.get("Authorization") != f"Bearer {stub.token}":
                    self._send(401, {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32001, "message": NO_TOKEN}})
                    return
                if "id" not in msg:                              # a notification
                    self._send(202)
                    return
                m, params = msg.get("method"), msg.get("params") or {}
                if m == "initialize":
                    res = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "eitri-combined-stub", "version": "0"}}
                    self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": res}, {"Mcp-Session-Id": "stub-session"})
                    return
                if m == "tools/list":
                    tools = [{"name": k, "description": k.replace("_", " "),
                              "inputSchema": {"type": "object", "properties": v, "required": REQUIRED.get(k, [])}}
                             for k, v in TOOLS.items()]
                    self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})
                    return
                if m == "tools/call":
                    text, err = stub.call(params.get("name", ""), params.get("arguments") or {})
                    self._send(200, {"jsonrpc": "2.0", "id": msg["id"],
                                     "result": {"content": [{"type": "text", "text": text}], "isError": err}})
                    return
                if m == "ping":
                    self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    return
                self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"no method {m}"}})

        return H
