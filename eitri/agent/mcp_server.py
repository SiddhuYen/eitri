"""Expose the eitri tools to a headless Claude Code session over MCP (stdio).

Started by the Claude Code CLI with EITRI_PROJECT pointing at the project
folder. Tool results carry the report text and the review image.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ImageContent, TextContent

from ..project import Project
from .tools import ToolResult, Tools


IMAGE_MODE = os.environ.get("EITRI_IMAGE_MODE", "path")   # "path" or "inline"


def _content(res: ToolResult) -> list[Any]:
    """Tool result content. Images go back as paths by default: the host's Read tool
    then shows them as real image blocks, whereas an inline MCP image is also
    serialised as base64 text into the model's context (tens of thousands of tokens)."""
    text = res.text
    images = [Path(i) for i in res.images if Path(i).exists()]
    if images and IMAGE_MODE == "path":
        text += "\n\nReview image: " + ", ".join(str(p) for p in images) + "  <- Read this file now and check it."
        return [TextContent(type="text", text=text)]
    out: list[Any] = [TextContent(type="text", text=text)]
    for p in images:
        out.append(Image(path=str(p)).to_image_content())
    return out


def make_server(project: Project, quality: float = 1.0) -> MCPServer:
    tools = Tools(project, render_quality=quality)
    server = MCPServer("eitri", instructions="CAD modelling tools for the eitri agent.")

    @server.tool(description=Tools.build_model.__doc__)
    def build_model(code: str, note: str = "") -> list[Any]:
        return _content(tools.build_model(code, note))

    @server.tool(description=Tools.edit_model.__doc__)
    def edit_model(old: str, new: str, note: str = "") -> list[Any]:
        return _content(tools.edit_model(old, new, note))

    @server.tool(description=Tools.set_params.__doc__)
    def set_params(params: dict[str, Any] | str | None = None, config: str | None = None) -> list[Any]:
        return _content(tools.set_params(params, config=config))

    @server.tool(description=Tools.read_model.__doc__)
    def read_model() -> str:
        return tools.read_model().text

    @server.tool(description=Tools.inspect.__doc__)
    def inspect(code: str) -> str:
        return tools.inspect(code).text

    @server.tool(description=Tools.view.__doc__)
    def view(views: list[str] | None = None, section: str | None = None, exploded: bool = False,
             hide: list[str] | None = None, only: list[str] | None = None, turntable: int = 0) -> list[Any]:
        return _content(tools.view(views, section, exploded, hide, only, turntable=turntable))

    @server.tool(description=Tools.library.__doc__)
    def library(query: str = "") -> str:
        return tools.library(query).text

    @server.tool(description=Tools.export.__doc__)
    def export(formats: list[str]) -> str:
        return tools.export(formats).text

    @server.tool(description=Tools.design_notes.__doc__)
    def design_notes(content: str) -> str:
        return tools.design_notes(content).text

    @server.tool(description=Tools.harness_reference.__doc__)
    def harness_reference() -> str:
        return tools.harness_reference().text

    @server.tool(description=Tools.cam.__doc__)
    def cam(ops: list | str | None = None, part: str | None = None, post: str = "grbl", material: str | None = None, safe_z: float = 5.0) -> list[Any]:
        return _content(tools.cam(ops, part, post, material, safe_z))

    @server.tool(description=Tools.cut_file.__doc__)
    def cut_file(part: str | None = None, kerf: float = 0.2, fmt: str = "dxf", flat_pattern: bool = False) -> str:
        return tools.cut_file(part, kerf, fmt, flat_pattern).text

    @server.tool(description=Tools.print_prep.__doc__)
    def print_prep(bed: list[float] | str = "220x220", layer: float = 0.2, orient: bool = True, parts: list[str] | None = None) -> str:
        return tools.print_prep(bed, layer, orient, parts).text

    @server.tool(description=Tools.compare_revisions.__doc__)
    def compare_revisions(a: int | None = None, b: int | None = None) -> str:
        return tools.compare_revisions(a, b).text

    @server.tool(description=Tools.drawing.__doc__)
    def drawing(views: list[str] | None = None, sheet: str = "A4", style: str = "iso", projection: str = "third", notes: list[str] | str | None = None,
                annotations: list[dict] | str | None = None, gdt: list[dict] | str | None = None, datums: list[dict] | str | None = None,
                scales: dict | str | None = None, pdf: bool = False,
                dims: list[dict] | str | None = None) -> str:
        if isinstance(notes, str):
            notes = [n for n in notes.split("\n") if n.strip()]
        return _content(tools.drawing(views, sheet, style, projection, notes, annotations, gdt, datums, scales, pdf, dims))

    @server.tool(description=Tools.list_edits.__doc__)
    def list_edits() -> str:
        return tools.list_edits().text

    @server.tool(description=Tools.update_edit.__doc__)
    def update_edit(index: int, patch: dict[str, Any] | str) -> list[Any]:
        return _content(tools.update_edit(index, patch))

    @server.tool(description=Tools.sketch_op.__doc__)
    def sketch_op(index: int, op: str, args: dict[str, Any] | str | None = None) -> list[Any]:
        return _content(tools.sketch_op(index, op, args))

    @server.tool(description=Tools.remove_edit.__doc__)
    def remove_edit(index: int) -> list[Any]:
        return _content(tools.remove_edit(index))

    return server


def main() -> None:
    folder = os.environ.get("EITRI_PROJECT") or os.getcwd()
    name = os.environ.get("EITRI_MODEL_NAME") or None
    quality = float(os.environ.get("EITRI_RENDER_QUALITY", "1.0"))
    project = Project(folder, name=name)
    from ..kernel import warm_worker_async
    warm_worker_async()                      # the CAD kernel loads while Claude reads the request
    server = make_server(project, quality)
    server.run("stdio")


if __name__ == "__main__":
    main()
