# Viewers and optional companion tools

Three viewer options for the wiki, plus the optional semantic vault
search layer.

## Built-in graph viewer (default)

Run `bash <skill_path>/scripts/viewer.sh open` to build and serve a
graph-first static site on `http://localhost:8090`. Force-directed
D3 graph at the centre, type-grouped content browser on the left
with fuzzy search, click-to-open doc viewer modal with a 1-hop
subgraph navigator at the bottom for hop-by-hop exploration. Figure
pages render their PNG inline. Live physics knobs in a top-right
settings panel. Notes and todos pages are inline-editable from the
modal (padlock toggle), and a `+` button next to the search bar
uploads files straight into `vault/raw/` for the next ingest run.
No Node.js dependency — pure Python build + vanilla JS frontend
with vendored D3 + Fuse shipped inside the skill
(`template/wiki-view/static/vendor/`) and copied into the bundle
at build time; no network fetch. Each workspace's
bundle goes into `~/.cache/curiosity-engine/wiki-view/<workspace>/`;
the server rebuilds it after every inline edit, so refresh and the
change is visible.

## Obsidian (alternative — same underlying markdown)

`wiki/` is plain markdown with `[[wikilinks]]`. Open Obsidian →
**Open folder as vault** → pick `<your-workspace>/wiki`. Backlinks
and Obsidian's own graph view light up immediately, no plugins.
Figure asset PNGs live at `wiki/figures/_assets/` (inside the vault
scope, so inline image embeds render without reconfiguration). The
`_assets/` folder is gitignored; Obsidian's graph view by default
hides image nodes, but if you've turned "Show attachments" on you
can scope them out with a `-path:_assets` filter. Leave Claude Code
running in the workspace root; Obsidian picks up new pages as the
curator writes them. Treat Obsidian as a read-mostly view — manual
edits outside a `git -C wiki commit` won't be seen by the curator
until the next operation reads the page.

## VS Code + Foam (enterprise-friendly alternative)

If Obsidian isn't installable, open the workspace in VS Code and
add the **Foam** extension (free, open-source, typically on
enterprise marketplaces). Foam renders `[[wikilinks]]` as clickable
links, adds a backlinks panel, and provides a lightweight graph
view. Toggle `wiki_viewer_mode: "vscode"` in `.curator/config.json`
and re-run setup.sh; a one-time sweep converts figure-page image
embeds from Obsidian-transclusion syntax
(`![[figures/_assets/foo.png]]`) to standard markdown
(`![foo.png](_assets/foo.png)`) so VS Code's built-in preview
renders them inline. Switch back to `"obsidian"` and re-run
setup.sh to convert them back.

## Semantic vault search (optional)

For vaults above a few hundred sources where keyword search starts
missing fuzzy matches, an optional embedding index layered over
sqlite-vec gives the curator a semantic fallback. Setup prompts to
install `fastembed` + `sqlite-vec` (~115MB deps + model; ONNX
runtime, no PyTorch — the default model is `BAAI/bge-small-en-v1.5`;
workspaces with `sentence-transformers` already installed keep
working via the fallback backend); opt in only if you need it.
Embeddings augment FTS5, never replace — keyword stays primary.

A C compiler must be on PATH at install time — `pysqlite3` (pulled
in alongside sqlite-vec to give macOS system Python a sqlite build
with loadable extensions enabled) compiles from source. Install it
before opting in: `xcode-select --install` on macOS, `apt install
build-essential` on Debian/Ubuntu, `dnf groupinstall 'Development
Tools'` on Fedora/RHEL.
