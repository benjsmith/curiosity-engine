#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Resolve paths. SCRIPT_DIR is the installed skill's scripts/ directory;
# TEMPLATE_DIR is its sibling template/ — the single source of truth for
# the wiki and curator skeleton copied into each new workspace.
#
# Path discipline: the skill is typically installed at
# ~/.claude/skills/<name> which is a symlink to ~/.agents/skills/<name>
# (or wherever npx-skills dropped the real tree). Claude Code's
# `<skill_path>` substitution is NOT stable across sessions — sometimes
# it resolves the symlink (physical path), sometimes it doesn't
# (logical path). If the allowlist only has one form, the other trips
# an approval prompt. So we compute both and emit allowlist entries for
# each when they differ; for direct-clone installs they're equal and
# de-dupe naturally.
_src_dir="$(dirname "$0")"
# Two independent derivations: `cd` without -P preserves the symlink
# (logical); `cd` followed by `pwd -P` canonicalizes (physical). Deriving
# one from the other would collapse both to the same value, so each path
# starts from the original $0 source dir.
SCRIPT_DIR_LOGICAL="$(cd "$_src_dir" && pwd)"
SCRIPT_DIR_PHYSICAL="$(cd "$_src_dir" && pwd -P)"
SKILL_ROOT_LOGICAL="$(dirname "$SCRIPT_DIR_LOGICAL")"
SKILL_ROOT_PHYSICAL="$(dirname "$SCRIPT_DIR_PHYSICAL")"
SCRIPT_DIR="$SCRIPT_DIR_PHYSICAL"    # internal file ops — unambiguous
SKILL_ROOT="$SKILL_ROOT_PHYSICAL"
TEMPLATE_DIR="$SKILL_ROOT/template"
SKILL_ROOTS=("$SKILL_ROOT_PHYSICAL")
if [ "$SKILL_ROOT_LOGICAL" != "$SKILL_ROOT_PHYSICAL" ]; then
    SKILL_ROOTS+=("$SKILL_ROOT_LOGICAL")
fi

# Ensure `uv` is available. The skill's canonical Python invocation is
# `uv run python3 ...`, which auto-discovers the workspace `.venv`. Without
# uv the allowlist won't match and every python command triggers approval.
if ! command -v uv >/dev/null 2>&1; then
    if [ -t 0 ] && [ -t 1 ]; then
        printf "uv not found. Install uv from astral.sh? [Y/n] "
        read -r reply_uv || reply_uv="y"
    else
        reply_uv="y"
    fi
    case "$reply_uv" in
        ""|y|Y|yes|YES)
            echo "  Installing uv ..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # shellcheck disable=SC1091
            [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
            export PATH="$HOME/.local/bin:$PATH"
            ;;
        *)
            echo "  Cannot proceed without uv. Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
            ;;
    esac
fi

# Create workspace venv + install kuzu. `uv run` from the workspace root
# auto-discovers `./.venv` — no activation needed. kuzu backs graph.py,
# lint_scores.py, and epoch_summary.py.
if [ ! -d .venv ]; then
    echo "  Creating workspace .venv via uv ..."
    uv venv
fi
if ! uv run --no-project python3 -c "import kuzu" >/dev/null 2>&1; then
    echo "  Installing kuzu into .venv ..."
    uv pip install kuzu
fi
# pypdf: small, pure-Python PDF text extraction. Used by local_ingest.py
# as the fast-tier PDF path. Installed unconditionally — it's lightweight
# (~2 MB) and PDFs are a mainline ingest format.
if ! uv run --no-project python3 -c "import pypdf" >/dev/null 2>&1; then
    echo "  Installing pypdf (PDF text extraction) into .venv ..."
    uv pip install pypdf
fi
# PyYAML: used by tables.py to parse class-entity table schemas from
# entity-page frontmatter. Lightweight (pure-Python, ~300KB) and the
# pinnacle of standard in the Python world.
if ! uv run --no-project python3 -c "import yaml" >/dev/null 2>&1; then
    echo "  Installing PyYAML (class-table schema parser) into .venv ..."
    uv pip install pyyaml
fi

# Working directory layout:
#   vault/                 raw sources
#   wiki/                  content-only, git-tracked
#     sources/ entities/ concepts/ analyses/ evidence/ facts/
#   .curator/              curator state, NOT tracked by wiki's git
#   CLAUDE.md              workspace instructions (mirrors SKILL.md)
#   .claude/settings.json  auto-allow permissions
mkdir -p vault/raw wiki/{sources,entities,concepts,analyses,evidence,facts}
touch vault/.gitkeep vault/raw/.gitkeep
for d in sources entities concepts analyses evidence facts; do
    touch "wiki/$d/.gitkeep"
done
mkdir -p .curator

# Refresh markdown templates that drift as the skill evolves. The skill
# periodically adds new operations, prompt spec updates, or allowlist-
# breaking command changes; workspaces that don't pick those up show up
# as agent-side approval prompts and stale instructions. On every run:
#   * absent      → install fresh (initial setup case)
#   * identical   → leave alone
#   * different   → back up with timestamp, install fresh, optionally
#                    union-merge the backup back in so workspace additions
#                    are preserved
#
# config.json is handled separately (copy-if-missing) because its values
# are user-tuned (worker_model, parallel_workers, saturation thresholds)
# and a refresh would blow those away.
refresh_template_md() {
    local src="$1"
    local dst="$2"
    if [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        echo "  Created $dst"
        return
    fi
    if cmp -s "$src" "$dst"; then
        return
    fi
    local ts backup
    ts=$(date +%Y%m%d-%H%M%S)
    backup="${dst}.bak.${ts}"
    cp "$dst" "$backup"
    echo ""
    echo "  $dst differs from the skill template."
    echo "  Backed up to: $backup"
    local reply_merge="n"
    if [ -t 0 ] && [ -t 1 ]; then
        printf "  Auto-merge workspace edits with the refreshed template (union merge via git merge-file)? [y/N] "
        read -r reply_merge || reply_merge="n"
    fi
    cp "$src" "$dst"
    case "$reply_merge" in
        y|Y|yes|YES)
            if git merge-file --union "$dst" /dev/null "$backup" >/dev/null 2>&1; then
                echo "  Union-merged. Review $dst for duplicated sections from overlapping edits."
            else
                echo "  Union merge failed; left fresh template in place. Manually diff against $backup if needed."
            fi
            ;;
        *)
            echo "  Fresh template installed. Manually merge from $backup if you had local edits."
            ;;
    esac
}

refresh_template_md "$TEMPLATE_DIR/schema.md" ".curator/schema.md"
refresh_template_md "$TEMPLATE_DIR/prompts.md" ".curator/prompts.md"

# config.json: copy if missing; leave user-tuned values alone otherwise.
# Schema additions (new config keys) print a warning rather than overwriting.
if [ ! -f ".curator/config.json" ]; then
    cp "$TEMPLATE_DIR/config.json" ".curator/config.json"
    echo "  Created .curator/config.json"
elif ! cmp -s "$TEMPLATE_DIR/config.json" ".curator/config.json"; then
    echo ""
    echo "  NOTE: .curator/config.json differs from the skill template."
    echo "  Not auto-refreshing (preserves your worker_model/parallel_workers/"
    echo "  saturation-threshold tuning). Diff against $TEMPLATE_DIR/config.json"
    echo "  to pick up any new keys."
fi

# Initialize auto-generated curator state
if [ ! -f .curator/log.md ]; then
    printf '# Log\n' > .curator/log.md
    echo "  Created .curator/log.md"
fi
if [ ! -f .curator/index.md ]; then
    printf '# Index\n\nNo pages yet.\n' > .curator/index.md
    echo "  Created .curator/index.md"
fi

# No workspace sweep.py copy anymore — sweep.py is hash-guarded by
# evolve_guard.sh alongside every other skill script. The agent cannot
# edit it at runtime. If a previous install left a workspace copy or the
# skill-path marker, remove them (they will otherwise mask the fresh
# guarded version in any call that still points at .curator/sweep.py).
for stale in .curator/sweep.py .curator/sweep.py.bak .curator/.skill_path; do
    [ -e "$stale" ] && rm -f "$stale" && echo "  Removed stale $stale"
done

refresh_template_md "$TEMPLATE_DIR/CLAUDE.md" "CLAUDE.md"

# Generate Claude Code settings inline. Auto-allows:
#   - git commands scoped via `git -C wiki <cmd>` AND `git -C */wiki <cmd>`
#   - `uv run python3` invocations of skill scripts at this exact absolute path
#   - bash evolve_guard.sh
#   - date
# The `uv run` prefix picks up the workspace `.venv` so kuzu etc. resolve.
regenerate_settings=0
if [ ! -s .claude/settings.json ]; then
    regenerate_settings=1
elif ! uv run --no-project python3 -c "import json, sys; json.load(open('.claude/settings.json'))" >/dev/null 2>&1; then
    regenerate_settings=1
else
    # Canary-based drift detection: each skill update that adds new
    # canonical allowlist entries (new scripts, new Edit/Write scopes,
    # etc.) lists one recent entry in CANARY_ENTRIES. If any are missing
    # from the existing settings.json, the file is stale — offer to
    # regenerate (with backup). The last canary always covers the most
    # recent addition, so a single missing check catches workspaces
    # multiple versions behind.
    CANARY_ENTRIES=(
        "uv run python3"                     # pre-uv switch
        "Edit(./wiki/"                       # path-scoped Edit/Write
        "$SKILL_ROOT_LOGICAL/scripts/"       # logical skill path — catches
                                              # pre-dual-path settings that
                                              # only had the physical path
        "Edit(./vault/"                      # post-multimodal-upgrade write
                                              # path for .extracted.md
    )
    missing_canary=""
    for c in "${CANARY_ENTRIES[@]}"; do
        if ! grep -qF "$c" .claude/settings.json; then
            missing_canary="$c"
            break
        fi
    done
    # Anti-canary: stale entries from prior skill versions that should be
    # regenerated away even though the required canaries are all present.
    if [ -z "$missing_canary" ] && grep -qF ".curator/sweep.py:*" .claude/settings.json; then
        missing_canary=".curator/sweep.py (stale: workspace-sweep allowlist from pre-hash-guard era)"
    fi
    if [ -n "$missing_canary" ]; then
        echo "  Existing .claude/settings.json is missing canonical allowlist"
        echo "  entry matching: $missing_canary"
        if [ -t 0 ] && [ -t 1 ]; then
            printf "  Regenerate it now? (backs up old file to .claude/settings.json.bak) [Y/n] "
            read -r reply_regen || reply_regen="y"
        else
            reply_regen="y"
        fi
        case "$reply_regen" in
            ""|y|Y|yes|YES)
                cp .claude/settings.json .claude/settings.json.bak
                echo "  Backed up to .claude/settings.json.bak"
                regenerate_settings=1
                ;;
            *)
                echo "  Leaving settings.json alone. Expect approval prompts for any"
                echo "  commands or tools that have been added since install."
                ;;
        esac
    fi
fi

if [ "$regenerate_settings" = "1" ]; then
    mkdir -p .claude
    # Header + git entries + workspace sweep (path-independent).
    cat > .claude/settings.json <<EOF
{
  "permissions": {
    "allow": [
      "Bash(git -C wiki add:*)",
      "Bash(git -C wiki commit:*)",
      "Bash(git -C wiki status:*)",
      "Bash(git -C wiki log:*)",
      "Bash(git -C wiki diff:*)",
      "Bash(git -C wiki revert:*)",
      "Bash(git -C wiki checkout:*)",
      "Bash(git -C wiki rev-parse:*)",
      "Bash(git -C wiki show:*)",
      "Bash(git -C */wiki add:*)",
      "Bash(git -C */wiki commit:*)",
      "Bash(git -C */wiki status:*)",
      "Bash(git -C */wiki log:*)",
      "Bash(git -C */wiki diff:*)",
      "Bash(git -C */wiki revert:*)",
      "Bash(git -C */wiki checkout:*)",
      "Bash(git -C */wiki rev-parse:*)",
      "Bash(git -C */wiki show:*)",
EOF
    # One block of skill-script entries per skill root (logical +
    # physical when they differ under a symlinked install).
    for root in "${SKILL_ROOTS[@]}"; do
        cat >> .claude/settings.json <<EOF
      "Bash(uv run python3 $root/scripts/lint_scores.py:*)",
      "Bash(uv run python3 $root/scripts/vault_search.py:*)",
      "Bash(uv run python3 $root/scripts/vault_index.py:*)",
      "Bash(uv run python3 $root/scripts/local_ingest.py:*)",
      "Bash(uv run python3 $root/scripts/scrub_check.py:*)",
      "Bash(uv run python3 $root/scripts/score_diff.py:*)",
      "Bash(uv run python3 $root/scripts/sweep.py:*)",
      "Bash(uv run python3 $root/scripts/epoch_summary.py:*)",
      "Bash(uv run python3 $root/scripts/graph.py:*)",
      "Bash(uv run python3 $root/scripts/tables.py:*)",
      "Bash(bash $root/scripts/evolve_guard.sh:*)",
EOF
    done
    # Footer: workspace-scoped Edit/Write + misc.
    cat >> .claude/settings.json <<EOF
      "Edit(./wiki/**)",
      "Write(./wiki/**)",
      "Edit(./.curator/**)",
      "Write(./.curator/**)",
      "Edit(./vault/**)",
      "Write(./vault/**)",
      "Bash(date:*)"
    ]
  }
}
EOF
    if [ "${#SKILL_ROOTS[@]}" -gt 1 ]; then
        echo "  Created .claude/settings.json (dual-path allowlist for symlinked skill install)"
    else
        echo "  Created .claude/settings.json (auto-allow git -C wiki + uv run python3 skill scripts + scoped Edit/Write)"
    fi
fi

# Clean up leftover parallel-session state from an earlier skill version.
# If the workspace was set up when spawn.py / claims.py existed, these
# paths may still be present and will otherwise look like active state to
# a human inspecting `.curator/`. Harmless to remove — no recovery value.
for stale in .curator/.spawned .curator/.claims .curator/.claims.lock \
             .curator/.current-batch; do
    [ -e "$stale" ] && rm -f "$stale" && echo "  Removed stale $stale"
done
if [ -d .curator/sessions ]; then
    rm -rf .curator/sessions && echo "  Removed stale .curator/sessions/"
fi
# And the slash command registered by the parallel-sessions era.
if [ -f .claude/commands/curate.md ]; then
    rm -f .claude/commands/curate.md && echo "  Removed stale .claude/commands/curate.md"
fi

# Initialize wiki as its own git repo (content-only; .curator/ is outside)
if [ ! -d wiki/.git ]; then
    (cd wiki && git init -q && git add -A && git commit -q -m "init: curiosity engine wiki")
    echo "  Initialized wiki git repo"
fi

# Optional: install the caveman compression skill.
# Caveman strips predictable grammar tokens (articles, filler adverbs, etc.)
# so the curator burns less context. Used at read-time (ultra: ~30-40% fewer
# input tokens) and write-time (ultra for most pages, lite for analyses).
if [ -t 0 ] && [ -t 1 ]; then
    echo ""
    printf "Install caveman skill to save tokens by using terse telegraphic language for reads and writes? [Y/i/n] "
    read -r reply || reply="y"
    case "$reply" in
        ""|y|Y|yes|YES)
            if command -v npx >/dev/null 2>&1; then
                echo "  Installing JuliusBrussee/caveman via npx skills (global, symlinks) ..."
                npx skills add -g -y JuliusBrussee/caveman || echo "  (install failed — re-run: npx skills add -g -y JuliusBrussee/caveman)"
                echo "  Levels configured in .curator/config.json:"
                echo "    read=ultra, write_analysis=lite, write_other=ultra"
            else
                echo "  npx not found. Install later: npx skills add -g -y JuliusBrussee/caveman"
            fi
            ;;
        i|I)
            if command -v npx >/dev/null 2>&1; then
                echo "  Running interactive install: npx skills add JuliusBrussee/caveman"
                echo "  (all CLI options will be shown to you)"
                npx skills add JuliusBrussee/caveman </dev/tty || echo "  (install failed — re-run: npx skills add JuliusBrussee/caveman)"
            else
                echo "  npx not found. Install later: npx skills add JuliusBrussee/caveman"
            fi
            ;;
        *)
            echo "  Skipping caveman. Curator works without it (see SKILL.md)."
            ;;
    esac
fi

# Optional: semantic vault search (sentence-transformers + sqlite-vec).
# Adds ~200MB of model weights and enables hybrid FTS5 + cosine search.
# Most small vaults (<500 sources) don't need this — FTS5 keyword
# search covers the common case. Opt in when you start hitting
# paraphrased queries that miss with keyword alone.
if [ -t 0 ] && [ -t 1 ]; then
    echo ""
    printf "Install semantic vault search (sentence-transformers + sqlite-vec, ~200MB)? [y/N] "
    read -r reply_embed || reply_embed="n"
    case "$reply_embed" in
        y|Y|yes|YES)
            echo "  Installing sentence-transformers + sqlite-vec (+ pysqlite3) into .venv ..."
            # pysqlite3 is needed because macOS system Python's sqlite3 is
            # typically compiled without --enable-loadable-sqlite-extensions,
            # which breaks sqlite-vec. pysqlite3 is a drop-in replacement
            # built from source with extensions enabled. No-op on Linux
            # distros that already have extensions.
            if uv pip install sentence-transformers sqlite-vec pysqlite3; then
                # Flip embedding_enabled to true in config.json so vault_index
                # will compute embeddings on next ingest / --rebuild.
                uv run --no-project python3 -c "
import json
from pathlib import Path
p = Path('.curator/config.json')
cfg = json.loads(p.read_text())
cfg['embedding_enabled'] = True
cfg.setdefault('embedding_model', 'sentence-transformers/all-MiniLM-L6-v2')
p.write_text(json.dumps(cfg, indent=2))
"
                echo "  Enabled embedding_enabled=true in .curator/config.json"
                echo "  To embed the existing vault:"
                echo "    uv run python3 $SCRIPT_DIR/vault_index.py --rebuild"
            else
                echo "  Install failed. Enable later:"
                echo "    uv pip install sentence-transformers sqlite-vec"
            fi
            ;;
        *)
            echo "  Skipping semantic search. Enable later:"
            echo "    uv pip install sentence-transformers sqlite-vec"
            echo "    (then set embedding_enabled=true in .curator/config.json)"
            ;;
    esac
fi

# Initialize vault FTS5 index
uv run python3 "$SCRIPT_DIR/vault_index.py" --init

# Behavioral-migration pass. Each sweep resync-* subcommand is idempotent:
# it re-derives the correct state from the canonical source (naming.py,
# prompts.md, etc.) and only writes when it finds drift. After a skill
# update that changes such a source, this pass propagates the change
# across the existing workspace — renaming stubs, rewriting wikilinks,
# etc. No-op when everything is already in sync.
#
# Guarded by a clean-git check on the wiki repo: if the user has
# uncommitted changes we refuse to touch the wiki, print a note, and
# let them decide. Rationale: a migration may rename 100+ files and
# rewrite wikilinks across every page — the user wants that as a single
# reviewable commit, not tangled with in-progress edits.
if [ -d wiki/.git ]; then
    if [ -n "$(git -C wiki status --porcelain)" ]; then
        echo ""
        echo "  Wiki has uncommitted changes; skipping behavioral-migration pass."
        echo "  Commit or stash your wiki edits and rerun setup.sh to apply."
    else
        echo ""
        echo "  Running behavioral-migration pass (resync-stems, fix-index, graph rebuild) ..."
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-stems wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-index wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/graph.py" rebuild wiki >/dev/null
        echo "  Migration pass complete. Review with: git -C wiki diff --stat"
        # If resync renamed anything, there are now unstaged changes — we
        # intentionally leave them unstaged so the user inspects + commits
        # with a message of their choosing.
    fi
fi

echo ""
echo "Ready. Open Claude Code here and try:"
echo '  > add ~/some-paper.pdf to the vault'
echo '  > what do I know about X?'
echo '  > curate for an hour'
