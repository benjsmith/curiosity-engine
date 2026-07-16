#!/usr/bin/env python3
"""entity_gate.py — deterministic entity-resolution / abstention gate.

Answers one question before the LLM does: does the entity name in a
synthesis query actually exist in this workspace? Without this gate the
decision is left to the model noticing a name mismatch in retrieved
context — unreliable, and it degrades exactly when it matters most: a
query for a non-existent look-alike ("Project Onyxx" when only "Project
Onyx" exists) retrieves the real entity's documents by lexical/embedding
proximity, and the richer that wrong-entity context, the more confidently
the model answers with the wrong entity's facts (false-bridging).

The gate is deterministic, local, and cheap: no LLM call, no network.
`graph.py retrieve` runs it before assembling retrieval context and
returns no context at all when every mention abstains; `query_router.py
classify` embeds the same verdict on synthesis routes.

Resolution surface (the curated identity layer)
-----------------------------------------------
- frontmatter `title` (type tag stripped) and filename stem of every
  wiki page — each curated page is a curated name;
- `aliases` frontmatter (bracket-list of curated synonyms/codenames);
- `same_as` ids, both the full `authority:id` pair and the bare id;
- `iri` frontmatter and the `entities` table in `.curator/identifiers.db`
  (trailing IRI slug, merged `same_as` map);
- wikilink pipe-aliases `[[target|display]]` — a display name a curator
  chose for a target page is a curated alias of that target.

A *resolve* is an exact or known-alias match after normalisation (plus a
whole-word containment match when it is unambiguous — "Onyx" resolving
to the only page whose name contains the word "onyx"). Fuzzy proximity
to a differently-named entity is NEVER a resolve.

Mention extraction is capitalisation-first (quoted spans + capitalised
runs), then an identity-aware n-gram pass over the case-folded query so
all-lowercase questions ("what is project onyxx?") still surface both
known names and high-similarity look-alikes. Without that pass the gate
would silently no-op on chat-style casing and re-open false-bridging.

Per-mention verdicts
--------------------
    resolved    exact / known-alias match — answer normally, prefer the
                matched page's curated context.
    uncurated   no curated match, but the name appears verbatim in the
                vault or a wiki body — answer only from material naming
                it verbatim; never merge with a similarly-named entity.
    abstain     no curated match and no verbatim occurrence anywhere —
                the entity does not exist in this workspace. Say so.
                `look_alike` names the nearest curated near-miss (a
                DIFFERENT entity) to offer back by name only.

Subcommands
-----------
    entity_gate.py gate "<question>" [--wiki wiki]
        Extract entity mentions from a natural-language question and
        resolve each. Prints the gate verdict JSON.

    entity_gate.py resolve "<name>" [--wiki wiki]
        Resolve one literal name (no mention extraction).

No network. Reads only wiki markdown, vault/vault.db (read-only) and
.curator/identifiers.db (read-only); never creates either database.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

# Match vault_index/vault_search: pysqlite3 is a drop-in when the system
# sqlite3 lacks features; plain FTS5 reads work on either.
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import SKIP_FILES, read_frontmatter  # noqa: E402


# A near-miss at or above this similarity (difflib ratio over normalised
# names) is reported as the abstention's look_alike candidate. It never
# resolves anything — it only names the false-bridge the gate refused.
LOOKALIKE_THRESHOLD = 0.80

_TYPE_TAG_RE = re.compile(r"^\[[a-z-]+\]\s*")
_PIPE_ALIAS_RE = re.compile(r"\[\[([^\]|#]+)\|([^\]]+)\]\]")
_POSSESSIVE_RE = re.compile(r"[’']s\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TOKEN_RE = re.compile(r"[\w’'.-]+")

# Words that never head an entity mention on their own. Capitalised
# question/verb/date words are the main source of extraction noise; the
# gate must never abstain a real question over one of them.
_STOPWORDS = frozenset("""
a an the and or but nor of for to from in on at by with about into over
under between across per via
what which who whom whose where when why how whats
is are was were am be been being do does did done has have had having
can could should would will shall may might must
tell give show list find search compare explain describe summarize
summarise synthesize synthesise know knows recap overview
i we you they he she it me us them my our your their his her its
this that these those there here
if then than as vs versus not no any all some every each both
also just please everything anything something nothing
january february march april may june july august september october
november december monday tuesday wednesday thursday friday saturday
sunday q1 q2 q3 q4
""".split())

# Lowercase words allowed INSIDE a capitalised run ("Bank of America").
# Deliberately excludes "and"/"or" so coordinated mentions split.
_CONNECTORS = frozenset({"of", "the", "de", "la", "le", "von", "van",
                         "der", "den", "al", "el"})

_MAX_MENTIONS = 8


def _norm(text: str) -> str:
    """Matching key: casefold, drop possessive 's, non-alnum runs → one
    space. Stems normalise identically ("project-onyx" → "project onyx")."""
    t = _POSSESSIVE_RE.sub("", (text or "").casefold())
    return _NON_ALNUM_RE.sub(" ", t).strip()


# ---- Mention extraction ----

def extract_mentions(query: str) -> list:
    """Entity-name mentions in a question: quoted spans, plus runs of
    capitalised tokens (lowercase connectors allowed inside a run).
    Deterministic; leading/trailing stopwords and pure-number runs are
    dropped so date/question noise cannot trigger the gate."""
    mentions: list = []
    seen: set = set()

    def add(candidate: str) -> None:
        candidate = candidate.strip().strip("\"'").strip()
        # A grammatical possessive at the end isn't part of the name
        # ("Project Onyxx's launch" → "Project Onyxx"); _norm strips it
        # for matching anyway, this keeps the displayed mention clean.
        candidate = re.sub(r"[’']s$", "", candidate, flags=re.IGNORECASE)
        key = _norm(candidate)
        if not key or key in seen:
            return
        words = key.split()
        if all(w in _STOPWORDS for w in words):
            return
        if all(re.fullmatch(r"[0-9]+", w) for w in words):
            return
        seen.add(key)
        mentions.append(candidate)

    for span in re.findall(r'"([^"]{2,80})"', query):
        add(span)

    run: list = []
    pending: list = []  # connector awaiting a following capitalised token

    def flush() -> None:
        nonlocal run, pending
        while run and run[0].casefold() in _STOPWORDS:
            run.pop(0)
        while run and (run[-1].casefold() in _STOPWORDS
                       or run[-1].casefold() in _CONNECTORS):
            run.pop()
        if run and (len(run) > 1 or len(run[0]) >= 2):
            add(" ".join(run))
        run, pending = [], []

    for raw in _TOKEN_RE.findall(query):
        tok = raw.strip(".,;:!?’'-")
        if not tok:
            flush()
            continue
        if tok[0].isupper() or tok[0].isdigit():
            if pending and run:
                run.extend(pending)
            pending = []
            run.append(tok)
            # Sentence/list punctuation on the raw token ends the run —
            # "…about Project Onyx. Marlin swam…" must not merge.
            if raw[-1] in ".,;:!?":
                flush()
        elif run and not pending and tok.casefold() in _CONNECTORS:
            pending = [tok]
        else:
            pending = []
            flush()
    flush()
    return mentions[:_MAX_MENTIONS]


_MAX_NGRAM = 6


def _is_word_subphrase(inner: str, outer: str) -> bool:
    """True when inner is a strict whole-word subphrase of outer (both
    already normalised)."""
    if not inner or not outer or inner == outer:
        return False
    return bool(re.search(
        r"(?<![a-z0-9])" + re.escape(inner) + r"(?![a-z0-9])", outer))


def _augment_mentions_from_identity(query: str, mentions: list,
                                    index: dict) -> list:
    """Second-pass mention finder for case-folded queries.

    The capitalisation heuristic misses "what is project onyxx?" entirely.
    Walk longest-first n-grams of the tokenised query; keep a window when
    it exact-resolves against the identity index OR is a high-similarity
    look-alike of a curated name (the false-bridge case). Longer matches
    replace any shorter subphrase already accepted.
    """
    if not index:
        return mentions

    tokens: list = []
    for raw in _TOKEN_RE.findall(query):
        tok = raw.strip(".,;:!?’'-")
        if tok:
            # Possessive on a token is not part of the name.
            tok = re.sub(r"[’']s$", "", tok, flags=re.IGNORECASE)
            if tok:
                tokens.append(tok)
    if not tokens:
        return mentions

    accepted = list(mentions)
    seen = {_norm(m) for m in accepted}
    max_n = min(_MAX_NGRAM, len(tokens))

    for n in range(max_n, 0, -1):
        for i in range(0, len(tokens) - n + 1):
            window = list(tokens[i:i + n])
            while window and window[0].casefold() in _STOPWORDS:
                window.pop(0)
            while window and (window[-1].casefold() in _STOPWORDS
                              or window[-1].casefold() in _CONNECTORS):
                window.pop()
            if not window:
                continue
            if all(t.casefold() in _STOPWORDS for t in window):
                continue
            if all(re.fullmatch(r"[0-9]+", t) for t in window):
                continue
            # Single-character tokens are never entity names; length-2
            # only if it exact-resolves (e.g. a curated acronym).
            if len(window) == 1 and len(window[0]) < 2:
                continue

            span = " ".join(window)
            key = _norm(span)
            if not key or key in seen:
                continue
            # Prefer the longer mention already kept.
            if any(_is_word_subphrase(key, sk) for sk in seen):
                continue

            # Exact / mention-in-entity only — never "entity name sits
            # inside this long window", which would swallow look-alikes
            # next to a real name in coordinated questions.
            hit = resolve_name(span, index,
                               allow_query_contains_entity=False)
            keep = bool(hit)
            if not keep:
                # Look-alike n-grams: require multi-token or ≥3 chars so
                # short query noise can't fire the gate alone.
                if len(window) == 1 and len(key) < 3:
                    continue
                look = _best_lookalike(key, index)
                keep = bool(look)
            if not keep:
                continue

            # Drop shorter mentions this one supersedes.
            accepted = [m for m in accepted
                        if not _is_word_subphrase(_norm(m), key)]
            accepted.append(span)
            seen = {_norm(m) for m in accepted}

    return accepted[:_MAX_MENTIONS]


# ---- Identity index (the curated resolution surface) ----

def _iter_wiki_pages(wiki_dir: Path) -> list:
    return [f for f in sorted(wiki_dir.rglob("*.md"))
            if f.name not in SKIP_FILES and "_suspect" not in f.parts]


def _display_title(fm: dict, stem: str) -> str:
    title = str(fm.get("title", stem.replace("-", " ")))
    return _TYPE_TAG_RE.sub("", title).strip() or stem.replace("-", " ")


def _same_as_keys(items) -> list:
    """Frontmatter bracket-list (['auth:id', ...]) or entities-table dict
    ({auth: id}) → matchable keys: the full pair and the bare id."""
    keys = []
    if isinstance(items, dict):
        items = [f"{k}:{v}" for k, v in sorted(items.items())]
    for item in items if isinstance(items, list) else []:
        item = str(item).strip().strip("\"'")
        if not item:
            continue
        keys.append(item)
        _auth, _, ident = item.partition(":")
        if ident.strip():
            keys.append(ident.strip())
    return keys


def build_identity_index(wiki_dir: Path):
    """Returns (index, norm_bodies).

    index: normalised name → {"page": rel, "title": display, "via": how}.
    First writer wins; sources are added strongest-first (page names and
    frontmatter, then the IRI registry, then pipe-aliases) so a weaker
    alias can never shadow a curated title.

    norm_bodies: [(rel, normalised body)] for verbatim-presence checks.
    """
    index: dict = {}
    norm_bodies: list = []
    stem_to_rel: dict = {}
    titles: dict = {}
    pipe_aliases: list = []  # (target_norm, display)

    def put(key: str, rel: str, title: str, via: str) -> None:
        key = _norm(key)
        if key and key not in index:
            index[key] = {"page": rel, "title": title, "via": via}

    pages = _iter_wiki_pages(wiki_dir) if wiki_dir.is_dir() else []
    for page in pages:
        rel = str(page.relative_to(wiki_dir))
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = read_frontmatter(text)
        title = _display_title(fm, page.stem)
        titles[rel] = title
        stem_to_rel[_norm(page.stem)] = rel
        norm_bodies.append((rel, _norm(body)))

        put(title, rel, title, "title")
        put(page.stem, rel, title, "stem")
        aliases = fm.get("aliases")
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases or []:
            put(str(alias).strip().strip("\"'"), rel, title, "alias")
        for key in _same_as_keys(fm.get("same_as")):
            put(key, rel, title, "same_as")
        iri = str(fm.get("iri", "")).strip()
        if iri:
            put(iri.rpartition(":")[2], rel, title, "iri")
        for target, display in _PIPE_ALIAS_RE.findall(body):
            pipe_aliases.append((_norm(target), display.strip()))

    for row in _entities_rows(wiki_dir.parent):
        iri, page_path, same_as = row
        rel = page_path if page_path in titles else None
        title = titles.get(rel) or _norm(iri.rpartition(":")[2]).title()
        slug = iri.rpartition(":")[2]
        put(slug, rel or "", title, "iri")
        for key in _same_as_keys(same_as):
            put(key, rel or "", title, "same_as")

    for target_norm, display in pipe_aliases:
        rel = stem_to_rel.get(target_norm)
        if rel:
            put(display, rel, titles[rel], "pipe-alias")

    return index, norm_bodies


def _entities_rows(workspace: Path) -> list:
    """(iri, page_path, same_as dict) rows from the U1 IRI registry.
    Read-only; returns [] when the db doesn't exist rather than creating
    it (identifier_cache._connect would)."""
    db = workspace / ".curator" / "identifiers.db"
    if not db.exists():
        return []
    out = []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT iri, page_path, same_as_json FROM entities").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    for iri, page_path, same_as_json in rows:
        try:
            same_as = json.loads(same_as_json) if same_as_json else {}
        except (json.JSONDecodeError, TypeError):
            same_as = {}
        out.append((str(iri or ""), str(page_path or ""), same_as))
    return out


# ---- Resolution ----

def resolve_name(name: str, index: dict, *,
                 allow_query_contains_entity: bool = True):
    """Exact / known-alias match, else unambiguous whole-word containment.

    Default containment is bidirectional: "Onyx" resolves to the only page
    whose name contains that word, and a long phrase that embeds a curated
    name can also resolve. Callers scanning arbitrary query n-grams MUST
    pass allow_query_contains_entity=False so a window like "project onyx
    and project onyxx relate" does not collapse to Project Onyx and erase
    the look-alike mention. Fuzzy similarity never resolves.
    """
    key = _norm(name)
    if not key:
        return None
    hit = index.get(key)
    if hit:
        return {**hit, "matched_key": key}

    word_re = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
    contains: dict = {}
    for k in sorted(index):
        if word_re.search(k):
            # mention is a whole word inside a curated name ("onyx" ⊆
            # "project onyx") — safe for n-gram and free-text mentions.
            entry = index[k]
            contains.setdefault(entry["page"] or k, (k, entry))
        elif (allow_query_contains_entity and len(key) > len(k)
              and re.search(
                  r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", key)):
            # curated name is a whole word inside the mention span — only
            # for explicit (usually capitalised) mentions, never n-grams.
            entry = index[k]
            contains.setdefault(entry["page"] or k, (k, entry))
    if len(contains) == 1:
        k, entry = next(iter(contains.values()))
        return {**entry, "matched_key": k, "via": entry["via"] + "+word-match"}
    return None


def _best_lookalike(name_norm: str, index: dict):
    """Nearest curated key by difflib ratio, if it clears the threshold.
    Reported only — a look-alike is by definition a DIFFERENT entity."""
    best_key, best_entry, best_ratio = None, None, 0.0
    for key in sorted(index):
        sm = difflib.SequenceMatcher(None, name_norm, key)
        if sm.real_quick_ratio() <= best_ratio or sm.quick_ratio() <= best_ratio:
            continue
        ratio = sm.ratio()
        if ratio > best_ratio:
            best_key, best_entry, best_ratio = key, index[key], ratio
    if best_key is None or best_ratio < LOOKALIKE_THRESHOLD:
        return None
    return {"name": best_entry["title"], "page": best_entry["page"],
            "matched_key": best_key, "similarity": round(best_ratio, 3)}


# ---- Verbatim presence (raw-material fallback for uncurated names) ----

def _vault_mentions(workspace: Path, name_norm: str) -> int:
    """Sources whose body names the mention verbatim: FTS5 phrase
    prefilter, then a word-boundary check on the normalised body (porter
    stemming makes MATCH alone slightly fuzzy)."""
    db = workspace / "vault" / "vault.db"
    tokens = name_norm.split()
    if not db.exists() or not tokens:
        return 0
    phrase = '"' + " ".join(tokens) + '"'
    word_re = re.compile(
        r"(?<![a-z0-9])" + re.escape(name_norm) + r"(?![a-z0-9])")
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT body FROM sources WHERE sources MATCH ? LIMIT 200",
                (phrase,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
    return sum(1 for (body,) in rows if word_re.search(_norm(body or "")))


def _wiki_mentions(norm_bodies: list, name_norm: str) -> int:
    word_re = re.compile(
        r"(?<![a-z0-9])" + re.escape(name_norm) + r"(?![a-z0-9])")
    return sum(1 for _rel, body in norm_bodies if word_re.search(body))


def _mention_in_text(text: str, mention: str) -> bool:
    """True when normalised `mention` occurs as a whole-word phrase in text."""
    nm = _norm(mention)
    if not nm:
        return False
    return bool(re.search(
        r"(?<![a-z0-9])" + re.escape(nm) + r"(?![a-z0-9])",
        _norm(text or "")))


def text_has_any_mention(text: str, mentions) -> bool:
    """Whether text verbatim-names any mention (str or gate mention dict)."""
    for m in mentions or []:
        name = m if isinstance(m, str) else m.get("mention", "")
        if name and _mention_in_text(text, name):
            return True
    return False


def pure_uncurated(gate: dict) -> bool:
    """True when every extracted mention is vault/wiki-body-only (no resolve,
    no abstain). Option-C retrieve filter applies only in this case."""
    ms = gate.get("mentions") or []
    return bool(ms) and all(m.get("status") == "uncurated" for m in ms)


def mention_phrases(gate: dict) -> list:
    return [m["mention"] for m in (gate.get("mentions") or [])
            if m.get("mention")]


def wiki_page_has_mention(wiki_dir: Path, rel: str, phrases: list) -> bool:
    fp = wiki_dir / rel
    if not fp.is_file():
        return False
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text_has_any_mention(text, phrases)


def vault_hits_for_mentions(workspace: Path, phrases: list,
                            limit: int = 10) -> list:
    """Vault sources that name any phrase verbatim. Used when pure-uncurated
    retrieve must not rely on proximity seeds for its only evidence."""
    db = workspace / "vault" / "vault.db"
    if not db.exists() or not phrases:
        return []
    seen_paths: set = set()
    out: list = []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=ON")
            for phrase in phrases:
                nm = _norm(phrase)
                tokens = nm.split()
                if not tokens:
                    continue
                fts = '"' + " ".join(tokens) + '"'
                try:
                    rows = conn.execute(
                        "SELECT path, title, source_path, date, body "
                        "FROM sources WHERE sources MATCH ? LIMIT 50",
                        (fts,)).fetchall()
                except sqlite3.Error:
                    continue
                word_re = re.compile(
                    r"(?<![a-z0-9])" + re.escape(nm) + r"(?![a-z0-9])")
                for path, title, source_path, date, body in rows:
                    if path in seen_paths:
                        continue
                    if not word_re.search(_norm(body or "")):
                        continue
                    seen_paths.add(path)
                    out.append({
                        "path": path,
                        "title": title or "",
                        "source_path": source_path or "",
                        "date": date or "",
                        "snippet": (body or "")[:240],
                        "rank": 0.0,
                        "verbatim_mention": phrase,
                    })
                    if len(out) >= limit:
                        return out
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    return out


def vault_record_has_mention(workspace: Path, record: dict,
                             phrases: list) -> bool:
    """True if a vault_search hit names any phrase (body, else on-disk file)."""
    if not record or not phrases:
        return False
    for key in ("text", "snippet", "body", "title"):
        if record.get(key) and text_has_any_mention(str(record[key]), phrases):
            return True
    path = record.get("path") or ""
    if not path:
        return False
    # Prefer indexed body (complete) over snippet (truncated, may miss).
    db = workspace / "vault" / "vault.db"
    if db.exists():
        try:
            conn = sqlite3.connect(str(db), timeout=5)
            try:
                conn.execute("PRAGMA query_only=ON")
                row = conn.execute(
                    "SELECT body FROM sources WHERE path = ? LIMIT 1",
                    (path,)).fetchone()
            finally:
                conn.close()
            if row and text_has_any_mention(row[0] or "", phrases):
                return True
        except sqlite3.Error:
            pass
    for candidate in (workspace / "vault" / path,
                      workspace / path):
        if candidate.is_file():
            try:
                return text_has_any_mention(
                    candidate.read_text(encoding="utf-8", errors="replace"),
                    phrases)
            except OSError:
                return False
    return False


# ---- The gate ----

def gate_query(wiki_dir: Path, query: str) -> dict:
    """Full gate verdict for a natural-language question.

    action: "proceed" (no mentions, or every mention resolved/uncurated),
    "abstain" (every mention abstains — callers must return no retrieval
    context), or "partial" (some mentions abstain — answer only the
    resolved/uncurated ones, abstain the rest by name).
    """
    index, norm_bodies = build_identity_index(wiki_dir)
    mentions = _augment_mentions_from_identity(
        query, extract_mentions(query), index)
    if not mentions:
        return {"mentions": [], "action": "proceed"}

    workspace = wiki_dir.parent
    out: list = []
    abstained: list = []
    for mention in mentions:
        hit = resolve_name(mention, index)
        if hit:
            out.append({"mention": mention, "status": "resolved",
                        "page": hit["page"], "title": hit["title"],
                        "via": hit["via"]})
            continue
        nm = _norm(mention)
        vault_n = _vault_mentions(workspace, nm)
        wiki_n = _wiki_mentions(norm_bodies, nm)
        look = _best_lookalike(nm, index)
        if vault_n or wiki_n:
            rec = {"mention": mention, "status": "uncurated",
                   "vault_mentions": vault_n, "wiki_mentions": wiki_n,
                   "note": (f"'{mention}' has no curated page; answer only "
                            "from material naming it verbatim")}
            if look:
                rec["look_alike"] = look
                rec["note"] += (f" — and never from '{look['name']}', a "
                                "similarly-named but different entity")
            out.append(rec)
            continue
        abstained.append(mention)
        rec = {"mention": mention, "status": "abstain",
               "vault_mentions": 0, "wiki_mentions": 0,
               "note": f"no entity named '{mention}' in this workspace"}
        if look:
            rec["look_alike"] = look
            rec["note"] += (f"; nearest curated name is '{look['name']}' — "
                            "a DIFFERENT entity whose facts must not be "
                            f"attributed to '{mention}'")
        out.append(rec)

    if abstained and len(abstained) == len(mentions):
        action = "abstain"
    elif abstained:
        action = "partial"
    else:
        action = "proceed"
    verdict = {"mentions": out, "action": action}
    if abstained:
        verdict["abstained_mentions"] = abstained
        verdict["directive"] = (
            "State that no entity named "
            + " / ".join(f"'{m}'" for m in abstained)
            + " exists in this workspace. Offer any look_alike candidate "
              "by name only, as a different entity ('did you mean ...?'). "
              "Do NOT answer with facts retrieved for a similarly-named "
              "entity.")
    return verdict


# ---- CLI ----

def cmd_gate(query: str, wiki_dir: Path) -> int:
    print(json.dumps({"query": query, **gate_query(wiki_dir, query)},
                     indent=2))
    return 0


def cmd_resolve(name: str, wiki_dir: Path) -> int:
    index, norm_bodies = build_identity_index(wiki_dir)
    hit = resolve_name(name, index)
    out = {"name": name, "resolved": bool(hit)}
    if hit:
        out.update(hit)
    else:
        nm = _norm(name)
        out["vault_mentions"] = _vault_mentions(wiki_dir.parent, nm)
        out["wiki_mentions"] = _wiki_mentions(norm_bodies, nm)
        look = _best_lookalike(nm, index)
        if look:
            out["look_alike"] = look
    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_g = sub.add_parser("gate", help="gate a natural-language question")
    p_g.add_argument("query")
    p_g.add_argument("--wiki", default="wiki")

    p_r = sub.add_parser("resolve", help="resolve one literal entity name")
    p_r.add_argument("name")
    p_r.add_argument("--wiki", default="wiki")

    args = ap.parse_args()
    wiki_dir = Path(args.wiki).resolve()
    if args.cmd == "gate":
        return cmd_gate(args.query, wiki_dir)
    if args.cmd == "resolve":
        return cmd_resolve(args.name, wiki_dir)
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
