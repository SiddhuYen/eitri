"""The consolidated bill of materials of a project: printed parts, purchased hardware that has CAD geometry, purchased hardware
without any, and the harness's wire, connectors, terminals and splices, as one list, in JSON and as a table.

Sources, all declared in the model files like the rest of eitri:
  CAD model        its parts (printed unless named in HARDWARE), HARDWARE = {part name: catalog id}, PURCHASED = [(catalog id, qty)]
  harness model    eitri.harness.outputs.bom (connectors with part numbers, crimp terminals, wire by gauge and colour, splices, labels)
  catalog          eitri.catalog: part number, source link, unit price, key specs of every catalog part
Printed parts are priced as filament: their summed mass (volume x density, solid: an upper bound, a print at 20% infill uses about
half) x the catalog's PLA price. All prices are estimates and every spec the catalog has not verified against a datasheet is listed
under `unverified`.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import catalog as parts_catalog
from eitri import measure
from .projectfiles import model_files

DECLARES = ("HARDWARE", "PURCHASED", "INTEGRATION")


@dataclass
class Artifacts:
    cad: Path | None
    harness: Path | None


def artifacts(folder: Path, primary: Path | None = None) -> Artifacts:
    """The CAD model and the harness model of a project folder (either may be missing). The CAD model is the one that declares
    hardware / a mechanism / an integration, else the primary model if it is CAD; the harness is the one INTEGRATION names, else
    the only harness file, else the primary model if it is a harness."""
    folder = Path(folder)
    cad_files, har_files = model_files(folder)
    cad = None
    for f in ([primary] if primary and primary in cad_files else []) + cad_files:
        try:
            if any(f"{d} =" in f.read_text(encoding="utf-8") or f"{d}=" in f.read_text(encoding="utf-8") for d in DECLARES):
                cad = f
                break
        except OSError:
            continue
    if cad is None:
        cad = primary if primary in cad_files else (cad_files[0] if len(cad_files) == 1 else None)
    har = primary if primary in har_files else None
    if har is None and cad is not None:
        try:
            text = cad.read_text(encoding="utf-8")
            import re
            m = re.search(r"harness\s*=\s*[\"']([^\"']+)[\"']", text)
            if m and (folder / m.group(1)).is_file():
                har = folder / m.group(1)
        except OSError:
            pass
    if har is None and len(har_files) == 1:
        har = har_files[0]
    return Artifacts(cad, har)


def declares_hardware(path: Path | None) -> bool:
    """Whether a model file declares purchased hardware (HARDWARE or PURCHASED): then its project has a consolidated BOM."""
    if path is None:
        return False
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    return "HARDWARE =" in text or "PURCHASED =" in text


def _row(origin: str, category: str, ident: str, description: str, qty: float, unit: str, part_number: str = "", part: parts_catalog.CatalogPart | None = None,
         unit_price: float | None = None, notes: str = "") -> dict:
    row = {"origin": origin, "category": category, "id": ident, "description": description, "qty": qty, "unit": unit, "part_number": part_number,
           "manufacturer": "", "source": "", "unit_price_usd": unit_price, "line_total_usd": None if unit_price is None else round(unit_price * qty, 2),
           "price_note": "", "specs": {}, "unverified": [], "notes": notes}
    if part is not None:
        d = part.to_dict()
        row.update(manufacturer=part.manufacturer, source=part.source, part_number=part.part_number, price_note=part.price_note, unverified=d["unverified"], specs=d["specs"])
    return row


def _round_qty(q: float) -> float:
    return int(q) if float(q).is_integer() else round(q, 3)


def consolidated(folder: Path, primary: Path | None = None, name: str | None = None) -> dict:
    """The project's BOM as a dict (see the module docstring). Runs the model files (like every build and inspect)."""
    from eitri.model import load_model
    folder = Path(folder)
    art = artifacts(folder, primary)
    items: list[dict] = []
    notes: list[str] = []
    wanted: dict[str, float] = {}          # catalog id -> quantity, from HARDWARE and PURCHASED
    mass_total_g = 0.0

    if art.cad is not None:
        lm = load_model(art.cad)
        parts = lm.build()
        hardware = dict(getattr(lm.module, "HARDWARE", None) or {})
        for pname, ref in hardware.items():
            cid = ref.get("id") if isinstance(ref, dict) else ref
            qty = int(ref.get("qty", 1)) if isinstance(ref, dict) else 1
            if pname not in [p.name for p in parts]:
                notes.append(f"HARDWARE names part {pname!r}, which the model does not build")
            wanted[str(cid)] = wanted.get(str(cid), 0) + qty
        for entry in getattr(lm.module, "PURCHASED", None) or []:
            cid, qty = (entry[0], entry[1]) if not isinstance(entry, dict) else (entry["id"], entry.get("qty", 1))
            wanted[str(cid)] = wanted.get(str(cid), 0) + qty
        from eitri.assembly import _common_stem, bom as cad_bom
        printed = [p for p in parts if p.name not in hardware]
        class _P:                                                    # the parts the assembly BOM groups
            def __init__(self, parts): self._p = parts
            def __iter__(self): return iter(self._p)
        for r in cad_bom(_P(printed)):
            mass = (r.get("mass_total_g") if r["qty"] > 1 else r.get("mass_g")) if r.get("mass_g") is not None else None
            if mass is not None:
                mass_total_g += mass
            else:
                notes.append(f"printed part {r['name']} has no material: its filament is not counted")
            pname = r["name"] if (r["qty"] == 1 or _common_stem(r["parts"])) else " + ".join(r["parts"])      # identical shapes with unrelated names
            items.append(_row("cad", "printed", pname, f"{pname} (printed, {r['material']}, {r['volume']:g} mm³" + (f", {r['mass_g']:g} g each solid" if r.get("mass_g") is not None else "") + ")",
                              r["qty"], "pc", notes=", ".join(r["parts"])))
    for cid, qty in wanted.items():
        cp = parts_catalog.lookup(cid)
        if cp is None:
            notes.append(f"catalog has no part {cid!r}")
            items.append(_row("cad", "unknown", cid, f"{cid} (not in the catalog)", qty, "pc"))
            continue
        items.append(_row("catalog", cp.category, cp.id, cp.name, qty, cp.unit, part=cp, unit_price=cp.price_usd))
    if mass_total_g > 0:
        pla = parts_catalog.PARTS["PLA-1KG"]
        kg = mass_total_g / 1000.0
        items.append(_row("cad", "material", pla.id, "PLA filament for the printed parts (solid mass: an upper bound)", round(kg, 3), "kg", part=pla,
                          unit_price=pla.price_usd, notes=f"{mass_total_g:.0f} g of printed parts at solid density; a real print uses less"))

    if art.harness is not None:
        lm = load_model(art.harness)
        h = lm.build()
        from eitri.harness import outputs as hout
        for r in hout.bom(h):
            cp = parts_catalog.lookup(r["description"].split(" ")[0]) if r["category"] == "connector" else None
            row = _row("harness", r["category"], (cp.id if cp else r["category"]), r["description"], r["qty"], r["unit"], r.get("part_number") or "", part=cp, notes=r.get("notes", ""))
            row["unit_price_usd"] = None
            items.append(row)
    for i, r in enumerate(items, 1):
        r["item"] = i
    priced = [r for r in items if r["line_total_usd"] is not None]
    unver = sorted({r["id"] for r in items if r["unverified"]})
    return {"project": name or folder.name, "currency": "USD", "model_files": {"cad": art.cad.name if art.cad else None, "harness": art.harness.name if art.harness else None},
            "items": items,
            "totals": {"lines": len(items), "priced_lines": len(priced), "unpriced_lines": len(items) - len(priced),
                       "priced_total_usd": round(sum(r["line_total_usd"] for r in priced), 2), "printed_mass_g": round(mass_total_g, 1)},
            "unverified": {"parts": unver, "specs": sum(len(r["unverified"]) for r in items if r["origin"] == "catalog")},
            "notes": notes + ["prices are estimates (not quotes); the printed parts are priced as filament; harness wire, terminals and connectors are unpriced"]}


def to_json(bom: dict) -> str:
    return json.dumps(bom, indent=1, default=str)


def to_markdown(bom: dict) -> str:
    def money(x): return "" if x is None else f"${x:,.2f}"
    rows = ["| # | origin | part | description | qty | unit | part number | unit price | line total | unverified |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in bom["items"]:
        unv = f"{len(r['unverified'])} spec(s)" if r["unverified"] else ""
        rows.append(f"| {r['item']} | {r['origin']} | {r['id']} | {r['description']} | {_round_qty(r['qty'])} | {r['unit']} | {r['part_number']} | {money(r['unit_price_usd'])} | {money(r['line_total_usd'])} | {unv} |".replace("  ", " "))
    t = bom["totals"]
    out = [f"# Bill of materials: {bom['project']}", "", *rows, "",
           f"**Estimated total {money(t['priced_total_usd'])}** for {t['priced_lines']} of {t['lines']} lines; {t['unpriced_lines']} line(s) unpriced (harness wire, terminals, labels). "
           f"Printed parts: {t['printed_mass_g']:g} g of solid filament (upper bound)."]
    if bom["unverified"]["parts"]:
        out += ["", f"**Unverified:** {bom['unverified']['specs']} catalog spec(s), and every price, have not been compared with a datasheet or a quote "
                    f"({', '.join(bom['unverified']['parts'])}). Check the ones your design depends on (servo torque, dimensions) before you order."]
    for n in bom["notes"]:
        out.append(f"- {n}")
    return "\n".join(out) + "\n"


def to_csv(bom: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["item", "origin", "category", "id", "description", "qty", "unit", "part_number", "manufacturer", "source", "unit_price_usd", "line_total_usd", "unverified_specs"])
    for r in bom["items"]:
        w.writerow([r["item"], r["origin"], r["category"], r["id"], r["description"], r["qty"], r["unit"], r["part_number"], r["manufacturer"], r["source"],
                    "" if r["unit_price_usd"] is None else r["unit_price_usd"], "" if r["line_total_usd"] is None else r["line_total_usd"], ";".join(r["unverified"])])
    return buf.getvalue()
