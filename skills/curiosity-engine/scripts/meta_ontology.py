#!/usr/bin/env python3
"""Optional workspace meta-ontology.

The file is ``<workspace>/.curator/meta-ontology.yaml``. When it is
absent, or when it declares no typed edges (edges that name both
``source`` and ``target`` node types), the graph, retrieval, and the
viewer keep today's behaviour.

A typed edge is a frontmatter list on the source page. ``supports``
from evidence to hypothesis is recorded as ``supports:`` on the
evidence page and becomes a page-to-page relationship of that name.
Edges that omit both endpoints describe relationships the graph
already draws (wikilink, cites, relates_to) and add nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_NAME = "meta-ontology.yaml"

_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_TOP_KEYS = frozenset({"version", "nodes", "edges", "schema_keys"})
_NODE_KEYS = frozenset({
    "type", "label", "folder", "prefix", "color", "claims", "description",
})
_EDGE_KEYS = frozenset({
    "type", "label", "description", "source", "target", "color",
})
_SCHEMA_KEYS = frozenset({
    "key", "description", "applies_to", "values", "minimum", "maximum",
})
# Distinct strokes when a typed edge does not set its own colour.
_EDGE_COLORS = ("#0072b2", "#d55e00", "#cc79a7", "#009e73", "#e69f00", "#56b4e9")
# Built-in node colour. Fits the existing categorical palette and is not
# one of its current swatches. A workspace file may override it.
HYPOTHESIS_COLOR = "#7570b3"
# Colours the deterministic fallback must not reuse. Includes the
# built-in palette, hypothesis, white (unclassified) and the grey default.
_RESERVED_COLORS = frozenset({
    "#4d1ae8", "#1d6996", "#38a6a5", "#0f8554", "#73af48", "#edad08",
    "#e17c05", "#cc503e", "#94346e", "#6f4070", "#9656a2", "#5e4fa2",
    "#c51b8a", "#7570b3", "#ffffff", "#bbbbbb",
})
# Viewer buckets these frontmatter types onto another canonical name.
_TYPE_ALIASES = {
    "summary-table": "table",
    "extracted-table": "table",
}
_SKIP_FILES = frozenset({"index.md", "log.md", "schema.md"})
_SKIP_DIR_PARTS = frozenset({"_assets", "_archive", ".git", "_suspect"})


class MetaOntologyError(ValueError):
    """The config file is present and not usable."""


@dataclass(frozen=True)
class NodeTerm:
    type: str
    label: str
    description: str
    folder: str | None = None
    prefix: str | None = None
    color: str | None = None
    claims: bool | None = None


@dataclass(frozen=True)
class EdgeTerm:
    type: str
    label: str
    description: str
    source: tuple[str, ...] = ()
    target: tuple[str, ...] = ()
    color: str | None = None

    @property
    def typed(self) -> bool:
        """True when both ends name node types."""
        return bool(self.source and self.target)


@dataclass(frozen=True)
class SchemaKey:
    key: str
    description: str
    applies_to: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class MetaOntology:
    nodes: tuple[NodeTerm, ...]
    edges: tuple[EdgeTerm, ...]
    schema_keys: tuple[SchemaKey, ...]
    path: Path | None = None

    def node(self, type_name: str) -> NodeTerm | None:
        want = (type_name or "").strip().lower()
        for n in self.nodes:
            if n.type == want:
                return n
        return None

    def edge(self, type_name: str) -> EdgeTerm | None:
        want = (type_name or "").strip().lower()
        for e in self.edges:
            if e.type == want:
                return e
        return None

    def edge_color(self, type_name: str) -> str | None:
        """Stroke for a typed edge. An explicit colour wins."""
        typed_i = 0
        want = (type_name or "").strip().lower()
        for edge in self.edges:
            if not edge.typed:
                continue
            if edge.type == want:
                if edge.color:
                    return edge.color
                return _EDGE_COLORS[typed_i % len(_EDGE_COLORS)]
            typed_i += 1
        return None

    @property
    def typed_edges(self) -> tuple[EdgeTerm, ...]:
        return tuple(e for e in self.edges if e.typed)


def config_path(workspace: Path) -> Path:
    return Path(workspace) / ".curator" / CONFIG_NAME


def load(workspace: Path) -> MetaOntology | None:
    """The ontology, or None when the file is absent.

    Raises MetaOntologyError when the file is present and broken.
    """
    path = config_path(workspace)
    if not path.is_file():
        return None
    return _parse(path)


def declared_types(ont: MetaOntology | None) -> frozenset[str]:
    if ont is None:
        return frozenset()
    return frozenset(n.type for n in ont.nodes)


def frontmatter_keys(ont: MetaOntology | None) -> frozenset[str]:
    """Frontmatter keys this file adds to the allowlist.

    Typed edge names (the lists on the source page) and declared
    schema keys. Empty when there is no file.
    """
    if ont is None:
        return frozenset()
    keys = {e.type for e in ont.typed_edges}
    keys.update(k.key for k in ont.schema_keys)
    return frozenset(keys)


def deterministic_color(type_name: str) -> str:
    """Stable colour for a declared type that did not set ``color``.

    The same function is mirrored in the viewer (``theme.js``) and in
    Knowledge Atlas so a custom type paints the same stroke everywhere.
    It is never white and never one of the built-in swatches.
    """
    name = (type_name or "").strip().lower()
    hue = _fnv1a(name) % 360
    for _ in range(12):
        color = _hsl_hex(hue, 0.55, 0.46)
        if color not in _RESERVED_COLORS:
            return color
        hue = (hue + 29) % 360
    return _hsl_hex(hue, 0.55, 0.46)


def _fnv1a(text: str) -> int:
    h = 2166136261
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _hsl_hex(hue: int, sat: float, light: float) -> str:
    c = (1 - abs(2 * light - 1)) * sat
    hp = (hue % 360) / 60
    x = c * (1 - abs(hp % 2 - 1))
    if hp < 1:
        r, g, b = c, x, 0.0
    elif hp < 2:
        r, g, b = x, c, 0.0
    elif hp < 3:
        r, g, b = 0.0, c, x
    elif hp < 4:
        r, g, b = 0.0, x, c
    elif hp < 5:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    m = light - c / 2

    def channel(v: float) -> int:
        return max(0, min(255, round((v + m) * 255)))

    return f"#{channel(r):02x}{channel(g):02x}{channel(b):02x}"


def palette_additions(ont: MetaOntology | None) -> dict[str, str]:
    """Colours to merge into the viewer palette. Empty when no file."""
    if ont is None:
        return {}
    out: dict[str, str] = {}
    for n in ont.nodes:
        if not n.color:
            continue
        out[n.type] = n.color
        if n.folder:
            out[n.folder] = n.color
    return out


def collect_typed_edges(wiki_dir: Path, ont: MetaOntology | None) -> list[dict[str, Any]]:
    """Page-to-page edges whose endpoints match the declaration.

    Each row has ``source`` and ``target`` wiki-relative paths (with
    ``.md``), plus ``id`` forms without the suffix, ``type``,
    ``typeLabel``, and ``color`` when the edge has a stroke.

    A target that does not resolve, or an endpoint whose type is not
    one the edge allows, is left out. With no typed edges the list
    is empty.
    """
    if ont is None or not ont.typed_edges:
        return []
    pages = _wiki_pages(Path(wiki_dir), ont)
    by_id = {p["id"].casefold(): p for p in pages}
    by_stem: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        by_stem.setdefault(Path(page["id"]).name.casefold(), []).append(page)
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for page in pages:
        for term in ont.typed_edges:
            if not _type_ok(page["type"], term.source):
                continue
            for raw in _target_list(page["fm"].get(term.type)):
                resolved = _resolve_target(raw, by_id, by_stem)
                if not isinstance(resolved, dict):
                    continue
                if not _type_ok(resolved["type"], term.target):
                    continue
                if resolved["rel"] == page["rel"]:
                    continue
                key = (page["rel"], resolved["rel"], term.type)
                if key in seen:
                    continue
                seen.add(key)
                row: dict[str, Any] = {
                    "source": page["rel"],
                    "target": resolved["rel"],
                    "source_id": page["id"],
                    "target_id": resolved["id"],
                    "type": term.type,
                    "typeLabel": term.label,
                }
                color = ont.edge_color(term.type)
                if color:
                    row["color"] = color
                found.append(row)
    found.sort(key=lambda r: (r["type"], r["source"], r["target"]))
    return found


def _wiki_pages(wiki_dir: Path, ont: MetaOntology) -> list[dict[str, Any]]:
    if not wiki_dir.is_dir():
        return []
    # Local import: naming is the frontmatter parser. This module does
    # not import it at load time so a missing PyYAML (only needed when
    # a file is present) cannot break graph.py's import.
    from naming import read_frontmatter

    extra = frontmatter_keys(ont)
    out: list[dict[str, Any]] = []
    for path in sorted(wiki_dir.rglob("*.md")):
        if path.name in _SKIP_FILES:
            continue
        try:
            rel_path = path.relative_to(wiki_dir)
        except ValueError:
            continue
        if any(part in _SKIP_DIR_PARTS or part.startswith(".") for part in rel_path.parts):
            continue
        # Same string graph.py stores on WikiPage.path.
        rel = str(rel_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _body = read_frontmatter(text, extra_keys=extra)
        folder = rel.split("/", 1)[0] if "/" in rel else ""
        out.append({
            "rel": rel,
            "id": rel[:-3] if rel.endswith(".md") else rel,
            "type": _page_type(fm, folder, ont),
            "fm": fm if isinstance(fm, dict) else {},
        })
    return out


def _page_type(fm: dict[str, Any], folder: str, ont: MetaOntology) -> str:
    raw = fm.get("type")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    for node in ont.nodes:
        if node.folder and node.folder == folder:
            return node.type
    return ""


def _type_ok(page_type: str, allowed: tuple[str, ...]) -> bool:
    if not page_type:
        return False
    if page_type in allowed:
        return True
    return _TYPE_ALIASES.get(page_type, page_type) in allowed


def _target_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _normalize_target(raw: str) -> str:
    text = raw.strip().strip("\"'")
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    text = text.split("|", 1)[0].strip()
    text = text.replace("\\", "/")
    if text.lower().startswith("wiki/"):
        text = text[5:]
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text.strip("/")


def _resolve_target(
    raw: str,
    by_id: dict[str, dict[str, Any]],
    by_stem: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    key = _normalize_target(raw)
    if not key:
        return None
    hit = by_id.get(key.casefold())
    if hit is not None:
        return hit
    if "/" not in key:
        hits = by_stem.get(key.casefold(), [])
        if len(hits) == 1:
            return hits[0]
    return None


def _parse(path: Path) -> MetaOntology:
    try:
        import yaml
    except ImportError as exc:
        raise MetaOntologyError(
            "PyYAML is required to read .curator/meta-ontology.yaml "
            "(uv pip install pyyaml)"
        ) from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetaOntologyError(f"{path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MetaOntologyError(f"{path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MetaOntologyError(f"{path.name}: the top level must be a mapping")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise MetaOntologyError(f"{path.name}: unknown keys {sorted(unknown)}")
    if raw.get("version", 1) != 1:
        raise MetaOntologyError(f"{path.name}: version must be 1")
    name = path.name
    nodes = tuple(_parse_nodes(raw.get("nodes") or [], name))
    edges = tuple(_parse_edges(raw.get("edges") or [], name))
    keys = tuple(_parse_schema_keys(raw.get("schema_keys") or [], name))
    if not nodes and not edges and not keys:
        raise MetaOntologyError(
            f"{name}: list at least one node, edge, or schema key"
        )
    return MetaOntology(nodes=nodes, edges=edges, schema_keys=keys, path=path)


def _parse_nodes(raw: Any, name: str) -> list[NodeTerm]:
    rows = _as_list(raw, f"{name}: nodes")
    out: list[NodeTerm] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        item = _as_map(row, f"{name}: nodes[{i}]")
        _reject_unknown(item, _NODE_KEYS, f"{name}: nodes[{i}]")
        type_name = _ident(item.get("type"), f"{name}: nodes[{i}].type", _TYPE_RE)
        if type_name in seen:
            raise MetaOntologyError(f"{name}: duplicate node type {type_name}")
        seen.add(type_name)
        label = _text(item.get("label"), f"{name}: nodes[{i}].label", required=False)
        description = _text(
            item.get("description"), f"{name}: nodes[{i}].description", required=True,
        )
        folder = _ident(
            item.get("folder"), f"{name}: nodes[{i}].folder", _TYPE_RE, required=False,
        )
        prefix = _ident(
            item.get("prefix"), f"{name}: nodes[{i}].prefix", _TYPE_RE, required=False,
        )
        color = _color(item.get("color"), f"{name}: nodes[{i}].color")
        claims = item.get("claims")
        if claims is not None and not isinstance(claims, bool):
            raise MetaOntologyError(f"{name}: nodes[{i}].claims must be true or false")
        out.append(NodeTerm(
            type=type_name or "",
            label=label or type_name or "",
            description=description or "",
            folder=folder,
            prefix=prefix,
            color=color,
            claims=claims if isinstance(claims, bool) else None,
        ))
    return out


def _parse_edges(raw: Any, name: str) -> list[EdgeTerm]:
    rows = _as_list(raw, f"{name}: edges")
    out: list[EdgeTerm] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        item = _as_map(row, f"{name}: edges[{i}]")
        _reject_unknown(item, _EDGE_KEYS, f"{name}: edges[{i}]")
        type_name = _ident(item.get("type"), f"{name}: edges[{i}].type", _TYPE_RE)
        if type_name in seen:
            raise MetaOntologyError(f"{name}: duplicate edge type {type_name}")
        seen.add(type_name)
        label = _text(item.get("label"), f"{name}: edges[{i}].label", required=False)
        description = _text(
            item.get("description"), f"{name}: edges[{i}].description", required=True,
        )
        source = _type_names(
            item.get("source"), f"{name}: edges[{i}].source", present="source" in item,
        )
        target = _type_names(
            item.get("target"), f"{name}: edges[{i}].target", present="target" in item,
        )
        if (source is None) != (target is None):
            raise MetaOntologyError(
                f"{name}: edges[{i}] source and target are set together"
            )
        color = _color(item.get("color"), f"{name}: edges[{i}].color")
        out.append(EdgeTerm(
            type=type_name or "",
            label=label or type_name or "",
            description=description or "",
            source=source or (),
            target=target or (),
            color=color,
        ))
    return out


def _parse_schema_keys(raw: Any, name: str) -> list[SchemaKey]:
    rows = _as_list(raw, f"{name}: schema_keys")
    out: list[SchemaKey] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        item = _as_map(row, f"{name}: schema_keys[{i}]")
        _reject_unknown(item, _SCHEMA_KEYS, f"{name}: schema_keys[{i}]")
        key = _ident(item.get("key"), f"{name}: schema_keys[{i}].key", _KEY_RE)
        if key in seen:
            raise MetaOntologyError(f"{name}: duplicate schema key {key}")
        seen.add(key)
        description = _text(
            item.get("description"),
            f"{name}: schema_keys[{i}].description",
            required=True,
        )
        applies = tuple(
            part.lower()
            for part in _str_list(
                item.get("applies_to"), f"{name}: schema_keys[{i}].applies_to",
            )
        )
        values = tuple(_str_list(
            item.get("values"), f"{name}: schema_keys[{i}].values",
        ))
        minimum = _number(item.get("minimum"), f"{name}: schema_keys[{i}].minimum")
        maximum = _number(item.get("maximum"), f"{name}: schema_keys[{i}].maximum")
        out.append(SchemaKey(
            key=key or "",
            description=description or "",
            applies_to=applies,
            values=values,
            minimum=minimum,
            maximum=maximum,
        ))
    return out


def _as_list(raw: Any, where: str) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MetaOntologyError(f"{where} must be a list")
    return raw


def _as_map(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MetaOntologyError(f"{where} must be a mapping")
    return raw


def _reject_unknown(item: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise MetaOntologyError(f"{where}: unknown keys {sorted(unknown)}")


def _ident(
    raw: Any, where: str, pattern: re.Pattern[str], *, required: bool = True,
) -> str | None:
    if raw is None or raw == "":
        if required:
            raise MetaOntologyError(f"{where} is required")
        return None
    text = str(raw).strip().lower()
    if not pattern.match(text):
        raise MetaOntologyError(f"{where} must match {pattern.pattern}")
    return text


def _text(raw: Any, where: str, *, required: bool) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise MetaOntologyError(f"{where} is required")
        return None
    if not isinstance(raw, str):
        raise MetaOntologyError(f"{where} must be a string")
    return raw.strip()


def _color(raw: Any, where: str) -> str | None:
    if raw is None:
        return None
    color = str(raw).strip()
    if not _COLOR_RE.match(color):
        raise MetaOntologyError(f"{where} must be #rrggbb")
    return color


def _str_list(raw: Any, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise MetaOntologyError(f"{where} must be a list of strings")
    return [x.strip() for x in raw]


def _type_names(raw: Any, where: str, *, present: bool) -> tuple[str, ...] | None:
    """Node-type list. None when the key was omitted."""
    if not present:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise MetaOntologyError(f"{where} must list at least one node type")
    out: list[str] = []
    for part in raw:
        ident = _ident(part, where, _TYPE_RE)
        if ident:
            out.append(ident)
    if not out:
        raise MetaOntologyError(f"{where} must list at least one node type")
    return tuple(out)


def _number(raw: Any, where: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise MetaOntologyError(f"{where} must be a number")
    return float(raw)
