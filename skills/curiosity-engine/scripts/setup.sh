#!/usr/bin/env bash
set -e

# ---------------------------------------------------------------------------
# Argument parsing.
#
# Default behaviour (no flags): bootstrap or refresh a curiosity-engine
# workspace at cwd — this is the existing flow and is unchanged.
#
# Code-repo mode flags route this invocation to a different branch that
# registers a code repo against an existing workspace, without ever
# creating vault/, wiki/, or .curator/ inside the code repo. See
# docs/code-knowledge.md for the full design.
# ---------------------------------------------------------------------------
REGISTER_CODE_REPO=0
REGISTER_PROJECT_DIR=0
CE_WORKSPACE_PATH=""
CE_PROJECT=""
INGEST_PATHS=""
INGEST_EXTENSIONS=""
INIT_WORKSPACE=0
IN_REPO=0
ASSUME_YES=0
INSTALL_DRAINER=0
INITIAL_SCAN=1

while [ $# -gt 0 ]; do
    case "$1" in
        --register-code-repo)
            REGISTER_CODE_REPO=1; shift ;;
        --register-project-dir)
            REGISTER_PROJECT_DIR=1; shift ;;
        --ingest-paths)
            INGEST_PATHS="$2"; shift 2 ;;
        --ingest-paths=*)
            INGEST_PATHS="${1#*=}"; shift ;;
        --ingest-extensions)
            INGEST_EXTENSIONS="$2"; shift 2 ;;
        --ingest-extensions=*)
            INGEST_EXTENSIONS="${1#*=}"; shift ;;
        --no-initial-scan)
            INITIAL_SCAN=0; shift ;;
        --ce-workspace-path)
            CE_WORKSPACE_PATH="$2"; shift 2 ;;
        --ce-workspace-path=*)
            CE_WORKSPACE_PATH="${1#*=}"; shift ;;
        --ce-project)
            CE_PROJECT="$2"; shift 2 ;;
        --ce-project=*)
            CE_PROJECT="${1#*=}"; shift ;;
        --init-workspace)
            INIT_WORKSPACE=1; shift ;;
        --in-repo)
            IN_REPO=1; shift ;;
        --yes|-y)
            ASSUME_YES=1; shift ;;
        --install-drainer)
            INSTALL_DRAINER=1; shift ;;
        --help|-h)
            cat <<'HELP'
Usage: setup.sh [flags]

Default flow (no flags): bootstrap or refresh a curiosity-engine workspace
at cwd. This is the existing behaviour and is unchanged for existing CE
users.

Code-repo mode (register a code repo against an existing workspace):
  --register-code-repo          Enter code-repo mode. Writes
                                .curiosity/config.toml; never creates
                                vault/wiki/.curator inside the code repo.
  --ce-workspace-path <path>    Workspace path to register against.
                                Defaults to $CURIOSITY_WORKSPACE or
                                ~/Documents/curiosity-workspace.
  --ce-project <name>           Project tag applied to writes from this
                                repo. Defaults to the repo's basename.
  --init-workspace              Bootstrap the workspace at the resolved
                                path if it doesn't exist.
  --in-repo                     Force workspace mode in a code repo
                                (legacy / OSS / monorepo layout).
  --yes, -y                     Accept default-Y prompts non-interactively.
  --install-drainer             Install the session-drainer service
                                (deferred — accepted but no-op).

Project-directory mode (register a non-code documents directory against
a workspace; originals stay in-place, only .extracted.md goes to vault):
  --register-project-dir        Enter project-directory mode. Writes
                                .curiosity/config.toml with
                                project_kind=documents.
  --ingest-paths <list>         Comma-separated relative paths to scan
                                (default: ".").
  --ingest-extensions <list>    Comma-separated file extensions to
                                include (default: .pdf,.md,.txt,.docx,
                                .pptx,.csv,.xlsx,.html,.rst).
  --no-initial-scan             Skip the initial scan at end of setup.

See docs/code-knowledge.md for the full design.
HELP
            exit 0
            ;;
        *)
            echo "ERROR: unknown flag: $1" >&2
            echo "Run 'setup.sh --help' for usage." >&2
            exit 2
            ;;
    esac
done

echo "=== Curiosity Engine Setup ==="

# Interactive-mode predicate. The historical check was a bare
# `[ -t 0 ] && [ -t 1 ]`, but that misfires under coding-agent CLIs that
# allocate a PTY for the subprocess without any way to forward user
# keystrokes (GitHub Copilot Chat in VS Code is the prominent case):
# every `read -r reply` then blocks indefinitely. Callers — most
# importantly update.sh's migration pass — set
# CURIOSITY_ENGINE_NONINTERACTIVE=1 to force the non-TTY branch
# regardless of what isatty(3) reports.
_is_interactive() {
    [ "${CURIOSITY_ENGINE_NONINTERACTIVE:-0}" != "1" ] && [ -t 0 ] && [ -t 1 ]
}

# Pre-flight checks. Fail fast with clear messages instead of failing
# cryptically deep in the script. The three hard requirements: git (the
# wiki IS a git repo), python3 ≥ 3.9 (scripts use `from __future__ import
# annotations` + newer typing forms), and a working shell (already here
# since we're running).
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not found on PATH. The wiki is a git repository — install git first:"
    echo "  macOS:  brew install git  (or xcode-select --install)"
    echo "  Linux:  apt install git / dnf install git / pacman -S git"
    echo "  Windows: install Git for Windows, then run this under Git Bash or WSL"
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH. Install Python 3.9 or newer first."
    exit 1
fi
_py_major=$(python3 -c "import sys; print(sys.version_info.major)")
_py_minor=$(python3 -c "import sys; print(sys.version_info.minor)")
_py_version="${_py_major}.${_py_minor}"
if [ "$_py_major" -lt 3 ] || { [ "$_py_major" -eq 3 ] && [ "$_py_minor" -lt 9 ]; }; then
    echo "ERROR: Python $_py_version found; curiosity-engine needs Python 3.9 or newer."
    echo "       Upgrade Python (pyenv, asdf, or your distro package manager) and rerun."
    exit 1
fi

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
#
# Portability note. Claude Code's skill loader substitutes `<skill_path>`
# at invocation time; setup.sh can always derive its own scripts dir from
# $0. Orchestration prompts that run under other coding-agent CLIs may
# need CURIOSITY_ENGINE_SCRIPTS_DIR exported in the environment to stand
# in for `<skill_path>/scripts`. That export is a runtime concern (used
# by the orchestrator), not a setup-time one — setup.sh itself doesn't
# depend on it.
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
# npx-skills lays the install out as .agents/skills/<name> (physical)
# with ~/.claude/skills/<name> as a symlink to it. If setup.sh was
# invoked via the physical path (e.g. `bash ~/.agents/skills/...`),
# SCRIPT_DIR_LOGICAL and SCRIPT_DIR_PHYSICAL resolve to the same path
# and the allowlist only gets the physical form — but Claude Code at
# runtime invokes scripts via the .claude/skills/ logical path, so
# prefix matching fails and users hit approval prompts. Probe for the
# sibling form directly and include whichever exists.
#
# Use variables for the patterns: bash parameter substitution treats
# `\/` in the replacement string as a literal `\/` (preserving the
# backslash), which would give `/Users/foo\/.claude\/...` and break
# the `-d` check. Variable interpolation sidesteps the escape problem.
_agents_seg=".agents/skills"
_claude_seg=".claude/skills"
for _alt in "${SKILL_ROOT_PHYSICAL/$_agents_seg/$_claude_seg}" \
            "${SKILL_ROOT_PHYSICAL/$_claude_seg/$_agents_seg}"; do
    if [ "$_alt" != "$SKILL_ROOT_PHYSICAL" ] && [ -d "$_alt" ]; then
        case " ${SKILL_ROOTS[*]} " in
            *" $_alt "*) ;;
            *) SKILL_ROOTS+=("$_alt") ;;
        esac
    fi
done

# Ensure `uv` is available. The skill's canonical Python invocation is
# `uv run python3 ...`, which auto-discovers the workspace `.venv`.
# Without uv the allowlist won't match and every python command
# triggers approval.
#
# Earlier versions of this script auto-installed uv via
# `curl -LsSf https://astral.sh/uv/install.sh | sh` — the classic
# pipe-to-shell pattern that makes any audit reviewer wince and that
# gives Astral implicit RCE on first install. We don't do that
# anymore. If uv isn't present, the script prints platform-specific
# install commands and exits — the user runs them and reruns setup.
# This keeps third-party install scripts under explicit user control.
if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<'EOF'

ERROR: uv not found on PATH. curiosity-engine needs uv to manage its
       workspace .venv. Install it explicitly (we don't auto-pipe
       installer scripts), then re-run setup.sh:

  macOS (Homebrew):     brew install uv
  macOS / Linux (curl): curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
                        # inspect /tmp/uv-install.sh, then:
                        sh /tmp/uv-install.sh
  Linux (apt):          (uv isn't in apt yet — use the curl path)
  pip:                  pip install --user uv
  More options:         https://docs.astral.sh/uv/getting-started/installation/

EOF
    exit 1
fi

# ---------------------------------------------------------------------------
# Mode detection + code-repo branch.
#
# Existing CE users see no change: a directory with workspace markers is
# always treated as a workspace (existing flow, below). The code-repo
# branch only fires when (a) cwd is NOT a workspace and (b) either
# --register-code-repo was passed, or cwd is auto-detected as a code repo
# and the user confirms.
#
# --in-repo forces workspace mode in a code repo (legacy / OSS / monorepo).
# ---------------------------------------------------------------------------

CODE_REPO_PY="$SCRIPT_DIR/code_repo.py"

# Resolve mode. python3 is stdlib-only here — code_repo.py has no uv
# dependency.
_is_workspace=0
_is_code_repo=0
if python3 "$CODE_REPO_PY" is-workspace . >/dev/null 2>&1; then
    _is_workspace=1
fi
if python3 "$CODE_REPO_PY" detect . >/dev/null 2>&1; then
    _is_code_repo=1
fi

_mode="workspace"   # default
if [ "$_is_workspace" = "1" ]; then
    # Workspace cwd wins, but if the user passed an explicit --register-*
    # flag they almost certainly meant something else — error rather
    # than silently refresh.
    if [ "$REGISTER_PROJECT_DIR" = "1" ] || [ "$REGISTER_CODE_REPO" = "1" ]; then
        echo "  ERROR: cwd is already a CE workspace ($(pwd))." >&2
        echo "  --register-* flags operate on other directories to be" >&2
        echo "  registered AGAINST a workspace, not on the workspace" >&2
        echo "  itself. Run from the directory you want to register." >&2
        exit 1
    fi
    _mode="workspace"
elif [ "$IN_REPO" = "1" ]; then
    _mode="workspace"
elif [ "$REGISTER_PROJECT_DIR" = "1" ]; then
    _mode="project-dir"
elif [ "$REGISTER_CODE_REPO" = "1" ]; then
    _mode="code-repo"
elif [ "$_is_code_repo" = "1" ]; then
    # Auto-detected code repo with no workspace markers and no explicit
    # flag. Prompt in interactive mode; refuse with guidance otherwise.
    if _is_interactive; then
        echo ""
        echo "  Detected a code repository (\`.git/\` plus a source-marker file)."
        echo "  Curiosity Engine in code-repo mode never creates vault/, wiki/,"
        echo "  or .curator/ inside this repo — it registers the repo against"
        echo "  an existing workspace via .curiosity/config.toml."
        echo ""
        printf "  Register this code repo against a CE workspace? [Y/n/i (in-repo)] "
        read -r _reply_mode || _reply_mode="y"
        case "$_reply_mode" in
            ""|y|Y|yes|YES) _mode="code-repo" ;;
            i|I|in-repo)    _mode="workspace" ; IN_REPO=1 ;;
            *)
                echo "  Aborted. Re-run with --register-code-repo or --in-repo to choose."
                exit 0
                ;;
        esac
    else
        cat <<EOF >&2

ERROR: cwd is a code repository but no mode flag was given.

Pass one of:
  --register-code-repo [--ce-workspace-path PATH] [--yes]
                                  register against an existing workspace
                                  (writes .curiosity/config.toml; never
                                  creates vault/wiki/.curator inside this repo)
  --in-repo                       legacy: create a workspace inside this repo

See \`setup.sh --help\` or docs/code-knowledge.md for the full design.
EOF
        exit 2
    fi
fi

if [ "$_mode" = "code-repo" ]; then
    # ----- code-repo flow (Phase 1: pointer file only) ------------------
    #
    # Subsequent phases extend this branch with allowlist injection
    # (Phase 3), local hook + Action template (Phase 2), and session-brief
    # auto-generation (Phase 3.5).

    REPO_ROOT="$(pwd)"
    REPO_BASENAME="$(basename "$REPO_ROOT")"

    # Resolve target workspace path. Order:
    #   1. --ce-workspace-path
    #   2. $CURIOSITY_WORKSPACE
    #   3. xdg / ~/Documents / ~ default via code_repo.py
    if [ -n "$CE_WORKSPACE_PATH" ]; then
        TARGET_WS="$CE_WORKSPACE_PATH"
    elif [ -n "${CURIOSITY_WORKSPACE:-}" ]; then
        TARGET_WS="$CURIOSITY_WORKSPACE"
    else
        TARGET_WS="$(python3 "$CODE_REPO_PY" default-workspace-root)"
    fi
    # Expand ~ for our own checks.
    TARGET_WS_EXPANDED="${TARGET_WS/#\~/$HOME}"

    # Resolve project tag.
    if [ -z "$CE_PROJECT" ]; then
        CE_PROJECT="$REPO_BASENAME"
    fi

    echo ""
    echo "  Code-repo mode."
    echo "    Code repo:        $REPO_ROOT"
    echo "    Workspace target: $TARGET_WS_EXPANDED"
    echo "    Project tag:      $CE_PROJECT"
    echo ""

    # Workspace-existence check + confirm-and-reuse.
    if python3 "$CODE_REPO_PY" is-workspace "$TARGET_WS_EXPANDED" >/dev/null 2>&1; then
        # Existing CE workspace. Confirm reuse unless --yes or
        # --ce-workspace-path was passed (both are explicit signals).
        if [ "$ASSUME_YES" != "1" ] && [ -z "$CE_WORKSPACE_PATH" ] \
           && [ -z "${CURIOSITY_WORKSPACE:-}" ] && _is_interactive; then
            printf "  Workspace at %s already exists as a CE workspace.\n" "$TARGET_WS_EXPANDED"
            printf "  Use it for this code repo? [Y/n] "
            read -r _reply_use || _reply_use="y"
            case "$_reply_use" in
                ""|y|Y|yes|YES) ;;
                *)
                    printf "  Enter alternative workspace path: "
                    read -r TARGET_WS || TARGET_WS=""
                    if [ -z "$TARGET_WS" ]; then
                        echo "  Aborted." >&2
                        exit 1
                    fi
                    TARGET_WS_EXPANDED="${TARGET_WS/#\~/$HOME}"
                    if ! python3 "$CODE_REPO_PY" is-workspace "$TARGET_WS_EXPANDED" >/dev/null 2>&1; then
                        if [ "$INIT_WORKSPACE" != "1" ]; then
                            echo "  $TARGET_WS_EXPANDED is not a CE workspace." >&2
                            echo "  Re-run with --init-workspace to bootstrap it, or" >&2
                            echo "  point at a different existing workspace path." >&2
                            exit 1
                        fi
                    fi
                    ;;
            esac
        fi
    elif [ -d "$TARGET_WS_EXPANDED" ]; then
        # Path exists but isn't a CE workspace — likely a name collision
        # with an unrelated directory. Refuse to silently adopt it.
        echo "  $TARGET_WS_EXPANDED exists but is not a CE workspace." >&2
        if [ "$INIT_WORKSPACE" = "1" ]; then
            echo "  --init-workspace passed; bootstrap is not yet implemented in" >&2
            echo "  this Phase 1 build. Bootstrap the workspace by running:" >&2
            echo "    cd $TARGET_WS_EXPANDED && bash $0" >&2
            echo "  then re-run setup.sh --register-code-repo from this code repo." >&2
            exit 1
        fi
        if _is_interactive && [ "$ASSUME_YES" != "1" ]; then
            printf "  Bootstrap a CE workspace there? [y/N] "
            read -r _reply_boot || _reply_boot="n"
            case "$_reply_boot" in
                y|Y|yes|YES)
                    echo "  Bootstrap not yet implemented in Phase 1." >&2
                    echo "  Run: cd $TARGET_WS_EXPANDED && bash $0" >&2
                    echo "  then re-run --register-code-repo from this repo." >&2
                    exit 1
                    ;;
                *)
                    echo "  Aborted." >&2
                    exit 1
                    ;;
            esac
        else
            echo "  Re-run with --init-workspace to bootstrap (Phase 2+) or" >&2
            echo "  --ce-workspace-path PATH to point at a different existing workspace." >&2
            exit 1
        fi
    else
        # Path doesn't exist. Bootstrap if requested.
        if [ "$INIT_WORKSPACE" = "1" ]; then
            echo "  Bootstrapping workspace at $TARGET_WS_EXPANDED ..."
            mkdir -p "$TARGET_WS_EXPANDED"
            (cd "$TARGET_WS_EXPANDED" && bash "$0")
            echo "  Workspace bootstrapped. Continuing code-repo registration."
        else
            echo "  No CE workspace found at $TARGET_WS_EXPANDED." >&2
            echo "  Re-run with --init-workspace to bootstrap one, or" >&2
            echo "  --ce-workspace-path PATH to point at an existing workspace." >&2
            exit 1
        fi
    fi

    # Write the pointer file. JSON → write_pointer in code_repo.py.
    POINTER_DIR="$REPO_ROOT/.curiosity"
    POINTER_FILE="$POINTER_DIR/config.toml"
    mkdir -p "$POINTER_DIR"

    # Compose the pointer-file dict as JSON via heredoc, pipe to
    # code_repo.py write-config. Defaults match the example template.
    python3 "$CODE_REPO_PY" write-config "$POINTER_FILE" <<JSON
{
  "workspace": "$TARGET_WS",
  "project": "$CE_PROJECT",
  "code_citation_root": "$CE_PROJECT",
  "ingest": {
    "enabled": true,
    "paths": ["docs/adr/", "CHANGELOG.md", "README.md"],
    "pr_capture": true,
    "commit_capture": true,
    "transcript_capture": true
  },
  "brief": {
    "auto": true,
    "regenerate_on_pull": false
  }
}
JSON
    echo "  Wrote $POINTER_FILE"

    # Add .curiosity/config.toml to git's index if the repo allows.
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        # Stage but don't commit — let the engineer review and commit
        # with their own message.
        git add .curiosity/config.toml 2>/dev/null || true
        echo "  Staged .curiosity/config.toml for commit."
    fi

    # ---- Phase 3: Allowlist injection (per-machine, gitignored) ----------
    #
    # Write .claude/settings.local.json with the same canonical bash
    # surface the workspace's settings.json carries, but rooted at the
    # absolute workspace path so prefix matching pre-approves operations
    # the agent issues from this code-repo cwd.
    #
    # Backup-and-overwrite if a prior file exists (matches the existing
    # refresh_template_md pattern). Per-machine file: not committed.
    mkdir -p "$REPO_ROOT/.claude"
    SETTINGS_LOCAL="$REPO_ROOT/.claude/settings.local.json"
    if [ -s "$SETTINGS_LOCAL" ]; then
        _backup="${SETTINGS_LOCAL}.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$SETTINGS_LOCAL" "$_backup"
        echo "  Existing $SETTINGS_LOCAL backed up to $_backup"
    fi

    # Header + git entries scoped to the absolute workspace path.
    cat > "$SETTINGS_LOCAL" <<EOF
{
  "permissions": {
    "allow": [
      "Bash(git -C $TARGET_WS_EXPANDED/wiki add:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki commit:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki status:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki log:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki diff:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki revert:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki checkout:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki rev-parse:*)",
      "Bash(git -C $TARGET_WS_EXPANDED/wiki show:*)",
EOF
    # Skill-script entries — same set as workspace mode, both physical
    # and logical install roots when they differ (symlinked install).
    for root in "${SKILL_ROOTS[@]}"; do
        cat >> "$SETTINGS_LOCAL" <<EOF
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
      "Bash(uv run python3 $root/scripts/figures.py:*)",
      "Bash(uv run python3 $root/scripts/restyle.py:*)",
      "Bash(uv run python3 $root/scripts/scan.py:*)",
      "Bash(uv run python3 $root/scripts/naming.py:*)",
      "Bash(uv run python3 $root/scripts/projects.py:*)",
      "Bash(uv run python3 $root/scripts/identifier_resolve.py review:*)",
      "Bash(uv run python3 $root/scripts/identifier_resolve.py status:*)",
      "Bash(uv run python3 $root/scripts/activity_log.py:*)",
      "Bash(uv run python3 $root/scripts/planner.py:*)",
      "Bash(uv run python3 $root/scripts/wiki_render.py:*)",
      "Bash(uv run python3 $root/scripts/viewer_server.py:*)",
      "Bash(uv run python3 $root/scripts/okf_export.py:*)",
      "Bash(uv run python3 $root/scripts/bootstrap.py:*)",
      "Bash(uv run python3 $root/scripts/code_repo.py:*)",
      "Bash(python3 $root/scripts/code_repo.py:*)",
      "Bash(uv run python3 $root/scripts/code_capture.py:*)",
      "Bash(uv run python3 $root/scripts/session_drainer.py:*)",
      "Bash(uv run python3 $root/scripts/session_brief.py:*)",
      "Bash(uv run python3 $root/scripts/curate_launch.py:*)",
      "Bash(uv run python3 $root/scripts/curate_status.py:*)",
      "Bash(bash $root/scripts/evolve_guard.sh:*)",
      "Bash(bash $root/scripts/viewer.sh:*)",
      "Bash(bash $root/scripts/update.sh:*)",
EOF
    done
    # Workspace-rooted Edit/Write (absolute paths) + read access to skill.
    cat >> "$SETTINGS_LOCAL" <<EOF
      "Edit($TARGET_WS_EXPANDED/wiki/**)",
      "Write($TARGET_WS_EXPANDED/wiki/**)",
      "Edit($TARGET_WS_EXPANDED/.curator/**)",
      "Write($TARGET_WS_EXPANDED/.curator/**)",
      "Edit($TARGET_WS_EXPANDED/vault/**)",
      "Write($TARGET_WS_EXPANDED/vault/**)",
      "Read($TARGET_WS_EXPANDED/**)",
      "Write(/tmp/**)",
      "Edit(/tmp/**)",
EOF
    for root in "${SKILL_ROOTS[@]}"; do
        printf '      "Read(%s/**)",\n' "$root" >> "$SETTINGS_LOCAL"
    done
    cat >> "$SETTINGS_LOCAL" <<EOF
      "Bash(date:*)",
      "Bash(printenv CURATOR_PRESET:*)"
    ]
  }
}
EOF
    echo "  Wrote $SETTINGS_LOCAL (workspace-rooted allowlist)"

    # Ensure per-machine curiosity-engine files are gitignored. The
    # pointer file (.curiosity/config.toml) is the only thing in
    # .curiosity/ that should commit; everything else (session brief,
    # allowlist-install markers) is per-machine state.
    _gi="$REPO_ROOT/.gitignore"
    _gi_added=0
    _ensure_gi() {
        local pattern="$1"
        local pattern_re="$2"
        if [ ! -f "$_gi" ] || ! grep -qE "$pattern_re" "$_gi" 2>/dev/null; then
            if [ ! -f "$_gi" ]; then
                printf '# curiosity-engine: per-machine state\n%s\n' "$pattern" > "$_gi"
            else
                printf '%s\n' "$pattern" >> "$_gi"
            fi
            _gi_added=$((_gi_added + 1))
        fi
    }
    _ensure_gi ".claude/settings.local.json" '^\.claude/settings\.local\.json$'
    _ensure_gi ".curiosity/session-brief.md" '^\.curiosity/session-brief\.md$'
    _ensure_gi ".curiosity/.allowlist-installed-*" '^\.curiosity/\.allowlist-installed-\*$'
    if [ "$_gi_added" -gt 0 ]; then
        echo "  Updated .gitignore ($_gi_added curiosity-engine entries)"
    fi

    # Drop a marker so the non-Claude-Code allowlist auto-installer
    # (described in SKILL.md § Bash discipline) doesn't re-prompt for
    # Claude Code on subsequent sessions in this code repo.
    touch "$REPO_ROOT/.curiosity/.allowlist-installed-claude"

    # ---- Slash commands -------------------------------------------------
    #
    # Copy the slash-command templates into the code repo's
    # .claude/commands/ so /note, /decision, /gotcha, /constraint,
    # /distill register in Claude Code sessions opened here. Engineer
    # decides whether to commit these (gitignore by default — they're
    # regenerable from the skill template, like settings.local.json).
    mkdir -p "$REPO_ROOT/.claude/commands"
    if [ -d "$TEMPLATE_DIR/claude-commands" ]; then
        for _cmd in "$TEMPLATE_DIR/claude-commands"/*.md; do
            [ -f "$_cmd" ] || continue
            _cmd_name="$(basename "$_cmd")"
            cp "$_cmd" "$REPO_ROOT/.claude/commands/$_cmd_name"
        done
        echo "  Installed slash commands into .claude/commands/"
    fi
    # Slash commands gitignored by default (regenerable from the skill
    # template; engineer can remove the entry from .gitignore to commit
    # them and share with their team).
    _ensure_gi ".claude/commands/" '^\.claude/commands/?$'

    # ---- Phase 2: Capture hook (per-machine git hook) -------------------
    HOOK_TEMPLATE="$TEMPLATE_DIR/coderepo-hooks/post-merge"
    HOOK_DEST="$REPO_ROOT/.git/hooks/post-merge"
    if [ -f "$HOOK_TEMPLATE" ] && [ -d "$REPO_ROOT/.git/hooks" ]; then
        if [ -f "$HOOK_DEST" ] && ! grep -q "code_capture.py" "$HOOK_DEST" 2>/dev/null; then
            # User-customised hook present; chain via .d directory pattern
            # would be cleanest but git hooks don't support .d natively.
            # Back up theirs with timestamp; advise manual merge.
            _hook_bak="${HOOK_DEST}.bak.$(date +%Y%m%d-%H%M%S)"
            cp "$HOOK_DEST" "$_hook_bak"
            echo "  Existing post-merge hook backed up to $_hook_bak"
            echo "  Review and merge its logic into the new hook manually."
        fi
        # Substitute __CE_SCRIPTS_DIR__ → absolute scripts/ path so the
        # hook resolves regardless of where the skill is installed (the
        # engineer's machine path may differ from any CE_SCRIPTS_DIR env
        # var).
        sed "s|__CE_SCRIPTS_DIR__|$SCRIPT_DIR|g" "$HOOK_TEMPLATE" > "$HOOK_DEST"
        chmod +x "$HOOK_DEST"
        echo "  Installed .git/hooks/post-merge"
    fi

    # Phase 1 + 3 + 2 done. Subsequent phases (3.5 session brief,
    # 4 detached /curate) extend this further.
    if [ "$INSTALL_DRAINER" = "1" ]; then
        echo ""
        echo "  Note: --install-drainer (launchd / systemd unit for the"
        echo "  session drainer) is accepted but not yet implemented in"
        echo "  this build. Until it ships, the drainer runs on demand"
        echo "  via /distill or:"
        echo "    uv run python3 $SCRIPT_DIR/session_drainer.py --workspace $TARGET_WS_EXPANDED"
    fi

    echo ""
    echo "  Code repo registered."
    echo "    Pointer file:        .curiosity/config.toml (committed)"
    echo "    Allowlist:           .claude/settings.local.json (per-machine)"
    echo "    Slash commands:      .claude/commands/*.md (regenerable)"
    echo "    Capture hook:        .git/hooks/post-merge (per-machine)"
    echo ""
    echo "  On your next \`git pull\`, commits + (PR via gh) + changelog"
    echo "  will capture into the workspace. Full design: docs/code-knowledge.md"
    exit 0
fi

if [ "$_mode" = "project-dir" ]; then
    # ----- project-directory flow (v0.4.0) -------------------------------
    #
    # Same pointer-file shape as code-repo mode, with project_kind=
    # documents. The scanner (scripts/scan.py) walks the configured
    # paths, applies the extension whitelist + exclude globs, and
    # invokes local_ingest.py --source-path-only to produce .extracted.md
    # in the workspace's vault — originals stay where the user keeps
    # them.

    PROJECT_DIR_ROOT="$(pwd)"
    PROJECT_DIR_BASENAME="$(basename "$PROJECT_DIR_ROOT")"

    # Resolve target workspace path (same logic as code-repo flow).
    if [ -n "$CE_WORKSPACE_PATH" ]; then
        TARGET_WS="$CE_WORKSPACE_PATH"
    elif [ -n "${CURIOSITY_WORKSPACE:-}" ]; then
        TARGET_WS="$CURIOSITY_WORKSPACE"
    else
        TARGET_WS="$(python3 "$CODE_REPO_PY" default-workspace-root)"
    fi
    TARGET_WS_EXPANDED="${TARGET_WS/#\~/$HOME}"

    if [ -z "$CE_PROJECT" ]; then
        CE_PROJECT="$PROJECT_DIR_BASENAME"
    fi

    # Refuse to register the workspace itself, a parent of it, or any
    # path that contains the workspace — registering these would have
    # scan.py walk into the vault and re-ingest its own outputs.
    case "$TARGET_WS_EXPANDED" in
        "$PROJECT_DIR_ROOT"|"$PROJECT_DIR_ROOT"/*)
            echo "  ERROR: workspace ($TARGET_WS_EXPANDED) is at or under" >&2
            echo "  the directory you're registering ($PROJECT_DIR_ROOT)." >&2
            echo "  Pick a workspace path that lives elsewhere." >&2
            exit 1
            ;;
    esac
    case "$PROJECT_DIR_ROOT" in
        "$TARGET_WS_EXPANDED"|"$TARGET_WS_EXPANDED"/*)
            echo "  ERROR: cannot register a directory at or inside the" >&2
            echo "  workspace ($TARGET_WS_EXPANDED) — would create an" >&2
            echo "  ingestion loop." >&2
            exit 1
            ;;
    esac

    echo ""
    echo "  Project-directory mode."
    echo "    Project dir:      $PROJECT_DIR_ROOT"
    echo "    Workspace target: $TARGET_WS_EXPANDED"
    echo "    Project tag:      $CE_PROJECT"

    # Workspace existence — same checks as code-repo flow.
    if ! python3 "$CODE_REPO_PY" is-workspace "$TARGET_WS_EXPANDED" >/dev/null 2>&1; then
        if [ -d "$TARGET_WS_EXPANDED" ] && [ "$INIT_WORKSPACE" != "1" ]; then
            echo "  ERROR: $TARGET_WS_EXPANDED exists but is not a CE workspace." >&2
            echo "  Re-run with --init-workspace, or point at an existing workspace." >&2
            exit 1
        fi
        if [ "$INIT_WORKSPACE" = "1" ]; then
            echo "  Bootstrapping workspace at $TARGET_WS_EXPANDED ..."
            mkdir -p "$TARGET_WS_EXPANDED"
            (cd "$TARGET_WS_EXPANDED" && bash "$0")
        else
            echo "  ERROR: no CE workspace at $TARGET_WS_EXPANDED." >&2
            echo "  Re-run with --init-workspace or --ce-workspace-path PATH." >&2
            exit 1
        fi
    fi

    # Defaults for ingest config.
    if [ -z "$INGEST_PATHS" ]; then
        INGEST_PATHS="."
    fi
    if [ -z "$INGEST_EXTENSIONS" ]; then
        INGEST_EXTENSIONS=".pdf,.md,.txt,.docx,.pptx,.csv,.xlsx,.html,.rst"
    fi
    # Convert comma lists → JSON arrays for write-config.
    _to_json_array() {
        local IFS=','
        local out="["
        local first=1
        for item in $1; do
            item="${item## }"; item="${item%% }"
            if [ -z "$item" ]; then continue; fi
            if [ "$first" = "1" ]; then first=0; else out="$out, "; fi
            out="$out\"$item\""
        done
        echo "$out]"
    }
    _paths_json=$(_to_json_array "$INGEST_PATHS")
    _exts_json=$(_to_json_array "$INGEST_EXTENSIONS")

    POINTER_DIR="$PROJECT_DIR_ROOT/.curiosity"
    POINTER_FILE="$POINTER_DIR/config.toml"
    mkdir -p "$POINTER_DIR"
    python3 "$CODE_REPO_PY" write-config "$POINTER_FILE" <<JSON
{
  "workspace": "$TARGET_WS",
  "project": "$CE_PROJECT",
  "project_kind": "documents",
  "ingest": {
    "enabled": true,
    "paths": $_paths_json,
    "extensions": $_exts_json,
    "exclude": [],
    "follow_symlinks": false
  }
}
JSON
    echo "  Wrote $POINTER_FILE"

    # Validate paths up-front. If anything is unsafe, refuse to proceed.
    if ! python3 "$CODE_REPO_PY" validate-paths "$POINTER_FILE" >/dev/null 2>&1; then
        echo "" >&2
        echo "  ERROR: validation of [ingest] paths failed. Details:" >&2
        python3 "$CODE_REPO_PY" validate-paths "$POINTER_FILE" >&2 || true
        echo "  Fix .curiosity/config.toml and re-run." >&2
        exit 1
    fi

    # Register with the workspace's project-dir registry. This is what
    # `scan.py all` reads to enumerate.
    python3 "$CODE_REPO_PY" register-project-dir "$TARGET_WS_EXPANDED" \
        --path "$PROJECT_DIR_ROOT" --project "$CE_PROJECT" >/dev/null
    echo "  Registered with workspace project-dir registry."

    # Optional initial scan. Skip with --no-initial-scan or in non-
    # interactive mode without --yes.
    _do_scan=0
    if [ "$INITIAL_SCAN" = "1" ]; then
        if [ "$ASSUME_YES" = "1" ]; then
            _do_scan=1
        elif _is_interactive; then
            printf "  Run an initial scan now? [Y/n] "
            read -r _reply_scan || _reply_scan="y"
            case "$_reply_scan" in
                ""|y|Y|yes|YES) _do_scan=1 ;;
            esac
        fi
    fi
    if [ "$_do_scan" = "1" ]; then
        echo "  Scanning ..."
        (cd "$TARGET_WS_EXPANDED" && uv run python3 \
            "$SCRIPT_DIR/scan.py" one --workspace "$TARGET_WS_EXPANDED" \
            --pointer "$POINTER_FILE") || true
    fi

    echo ""
    echo "  Project directory registered."
    echo "    Pointer file:        .curiosity/config.toml"
    echo "    Workspace:           $TARGET_WS_EXPANDED"
    echo "    Project tag:         $CE_PROJECT"
    echo ""
    echo "  Auto-scan runs at start of CURATE, on viewer rebuild, and on"
    echo "  skill update. Manual rescan:"
    echo "    uv run python3 $SCRIPT_DIR/scan.py one \\"
    echo "      --workspace $TARGET_WS_EXPANDED \\"
    echo "      --pointer $POINTER_FILE"
    exit 0
fi

# ---------------------------------------------------------------------------
# Workspace flow (existing — unchanged below this line for existing users).
# ---------------------------------------------------------------------------

# Workspace-local uv cache (sandbox-safe by default). Coding-agent CLIs
# with strict filesystem sandboxes (Codex CLI is the prominent case) deny
# reads outside the workspace, including uv's default cache at
# ~/.cache/uv/. Every `uv run` then trips an escalation prompt because uv
# touches its cache on every invocation. Fix it at the source: tell uv to
# keep its cache inside the workspace via uv.toml. uv auto-discovers the
# file from cwd; no env vars, no per-host config.
#
# The cache itself is seeded by APFS / reflink clone of the existing
# global cache when possible — instant, and shares storage with the
# original until divergence (so N workspaces don't pay N × full-cache on
# disk). On filesystems without reflink support (older Linux, Windows,
# cross-volume installs) we fall back to a real recursive copy (one-time
# disk cost) or an empty directory (uv populates from network lazily).
mkdir -p .curator
if [ ! -f uv.toml ]; then
    cat > uv.toml <<'EOF'
# Workspace-local uv cache. Keeps uv's reads/writes inside the workspace
# so coding-agent CLIs with strict filesystem sandboxes (Codex CLI, etc.)
# don't escalate on every `uv run`. Harmless under Claude Code — uv
# auto-discovers this file from cwd. Written by curiosity-engine setup.sh;
# safe to delete if you want uv to use its global cache instead.
cache-dir = ".curator/uv-cache"
EOF
    echo "  Wrote uv.toml (cache-dir = .curator/uv-cache)"
fi
if [ ! -d .curator/uv-cache ]; then
    _src_cache="${UV_CACHE_DIR:-$HOME/.cache/uv}"
    if [ -d "$_src_cache" ]; then
        # Try BSD clone (`cp -c`, macOS APFS) first, then GNU reflink
        # (`cp --reflink=auto`, Linux btrfs/XFS), then a plain recursive
        # copy. Each shell only understands one of the first two flags;
        # the unknown-flag case fails immediately and falls through.
        if cp -c -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            echo "  Cloned $_src_cache → .curator/uv-cache (APFS clone — ~zero extra disk)"
        elif cp --reflink=auto -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            echo "  Cloned $_src_cache → .curator/uv-cache (reflink — ~zero extra disk)"
        elif cp -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            _cache_size=$(du -sh .curator/uv-cache 2>/dev/null | cut -f1)
            echo "  Copied $_src_cache → .curator/uv-cache (no reflink support; ${_cache_size:-unknown size} on disk)"
        else
            mkdir -p .curator/uv-cache
            echo "  Created empty .curator/uv-cache (clone/copy failed; uv will populate from network)"
        fi
    else
        mkdir -p .curator/uv-cache
        echo "  Created empty .curator/uv-cache (no source cache at $_src_cache; uv will populate from network on first run)"
    fi
fi
# Keep uv-cache out of any outer git repo wrapping the workspace. The
# wiki repo lives at wiki/ and is unaffected; this guards the case where
# the workspace itself is also under version control.
if [ ! -f .curator/.gitignore ] || ! grep -qE "^/?uv-cache(/|$)" .curator/.gitignore 2>/dev/null; then
    if [ ! -f .curator/.gitignore ]; then
        printf '# Workspace-local uv cache (seeded by setup.sh — regenerable)\nuv-cache/\n' > .curator/.gitignore
    else
        printf '\n# Workspace-local uv cache (seeded by setup.sh — regenerable)\nuv-cache/\n' >> .curator/.gitignore
    fi
fi

# Detect .venv drift. When the user upgrades system Python, the existing
# .venv is still bound to the old interpreter — if that interpreter is
# gone, the venv is silently broken; if it's still there, rerunning
# setup.sh would otherwise no-op (the `if [ ! -d .venv ]` guard below
# skips recreation). Catch both cases. Silent when no drift.
_rebuild_venv=0
_drift_reason=""
if [ -d .venv ]; then
    if ! .venv/bin/python3 --version >/dev/null 2>&1; then
        _rebuild_venv=1
        _drift_reason="old interpreter missing (Python likely upgraded since last setup)"
    elif [ -f .venv/pyvenv.cfg ]; then
        _venv_py=$(grep -E "^version[[:space:]]*=" .venv/pyvenv.cfg | head -1 | sed 's/^version[[:space:]]*=[[:space:]]*//' | tr -d ' ')
        _venv_mm=$(echo "$_venv_py" | cut -d. -f1,2)
        _cur_mm="${_py_major}.${_py_minor}"
        if [ -n "$_venv_mm" ] && [ "$_venv_mm" != "$_cur_mm" ]; then
            _drift_reason=".venv is on Python $_venv_py; current python3 is $_py_version"
            if _is_interactive; then
                echo ""
                echo "  $_drift_reason"
                printf "  Rebuild .venv on Python $_py_version? [y/N] "
                read -r _reply_rebuild || _reply_rebuild="n"
                case "$_reply_rebuild" in
                    y|Y|yes|YES) _rebuild_venv=1 ;;
                esac
            else
                # Non-interactive: surface the drift but keep the existing
                # venv. Rebuilding without confirmation risks surprising
                # users who deliberately pinned the venv to a different
                # Python. To rebuild: `rm -rf .venv && ./setup.sh`.
                echo ""
                echo "  NOTE: $_drift_reason"
                echo "        Keeping existing venv (non-interactive)."
                echo "        Rebuild manually: rm -rf .venv && rerun setup.sh"
            fi
        fi
    fi
fi
if [ "$_rebuild_venv" = "1" ]; then
    echo "  Removing old .venv and rebuilding on Python $_py_version ($_drift_reason) ..."
    rm -rf .venv
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
# pypdfium2 + Pillow: pypdfium2 renders PDF pages as bitmaps for
# figures.py extract/regen; Pillow is its standard companion for
# PIL-format output. Installed unconditionally — both are small
# (~5 MB combined) and required for any figure page whose origin
# is `extracted`.
if ! uv run --no-project python3 -c "import pypdfium2" >/dev/null 2>&1; then
    echo "  Installing pypdfium2 (PDF page rendering) into .venv ..."
    uv pip install pypdfium2
fi
if ! uv run --no-project python3 -c "import PIL" >/dev/null 2>&1; then
    echo "  Installing Pillow (PNG encoding for pypdfium2) into .venv ..."
    uv pip install Pillow
fi
# pdfplumber: layered on top of pypdf in local_ingest.py to recover
# bordered tables as GFM under `## Extracted tables`. Bordered tables
# (chemistry buffers, gene-expression grids, benchmark scores) recover
# well; borderless / multi-line-cell layouts fall through to the
# multimodal-upgrade flag. ~10 MB; only PDF-related.
if ! uv run --no-project python3 -c "import pdfplumber" >/dev/null 2>&1; then
    echo "  Installing pdfplumber (PDF table extraction) into .venv ..."
    uv pip install pdfplumber
fi
# openpyxl: stdlib-equivalent for the spreadsheet world. local_ingest.py
# uses it to convert .xlsx workbooks to per-sheet GFM tables. Pure
# Python (~5 MB).
if ! uv run --no-project python3 -c "import openpyxl" >/dev/null 2>&1; then
    echo "  Installing openpyxl (XLSX extraction) into .venv ..."
    uv pip install openpyxl
fi
# python-pptx: needed for slide-table extraction in local_ingest.py.
# Reads .pptx natively without LibreOffice; ~3 MB.
if ! uv run --no-project python3 -c "import pptx" >/dev/null 2>&1; then
    echo "  Installing python-pptx (PPTX extraction) into .venv ..."
    uv pip install python-pptx
fi

# Working directory layout:
#   vault/                 raw sources
#   wiki/                  content-only, git-tracked
#     sources/ entities/ concepts/ analyses/ evidence/ facts/
#   .curator/              curator state, NOT tracked by wiki's git
#   CLAUDE.md              workspace instructions (mirrors SKILL.md)
#   AGENTS.md              Copilot /create-agent scoped rules (not CURATE)
#   .claude/settings.json  auto-allow permissions
mkdir -p vault/raw wiki/{sources,entities,concepts,analyses,evidence,facts,tables,figures,notes,todos,projects}
touch vault/.gitkeep vault/raw/.gitkeep
for d in sources entities concepts analyses evidence facts tables figures notes todos projects; do
    touch "wiki/$d/.gitkeep"
done
mkdir -p .curator
mkdir -p .claude/commands

# Notes/todos staging pages. The todos class-table schema lives on the
# concept hub `wiki/todos.md` (seeded a few lines below from
# template/todos-overview.md) — there's no separate entity page. Skip
# if already present so user edits are preserved.
_seed_notes_or_todos_stub() {
    local path="$1"; local title="$2"; local type="$3"; local hub="$4"; local intro="$5"
    if [ ! -f "$path" ]; then
        cat > "$path" <<EOF
---
title: "$title"
type: $type
created: $(date +%Y-%m-%d)
updated: $(date +%Y-%m-%d)
---

Part of [[$hub]].

$intro

## active

EOF
    fi
}
_seed_notes_or_todos_stub wiki/notes/new.md '[note] new (default /note landing; curator drains)' note notes \
    'Default landing for `/note` without a topic cue. Drop free-form bullets here — the curator drains them into topic files (`notes/<topic>.md`) on the next sweep, routed by the first `[[wikilink]]` in the bullet, by an explicit `topic: <slug>` tag, or to [[for-attention]] if neither.'
_seed_notes_or_todos_stub wiki/notes/for-attention.md '[note] for-attention (notes awaiting user topic)' note notes \
    'Notes the auto-router could not classify (no `[[wikilink]]`, no `topic:` tag). Add a `[[wikilink]]` or a `topic: <slug>` to a bullet to route it on the next sweep, or wait for the curator to infer the topic during a CURATE run.'

# Landing + hub pages. `[ ! -s ]` covers both absent AND zero-byte
# (an Obsidian click-artefact or a pre-hub-era empty stub) so the
# template gets installed in either case. User edits are preserved
# because a non-empty file is never overwritten.
if [ ! -s wiki/index.md ] && [ -f "$TEMPLATE_DIR/wiki-index.md" ]; then
    cp "$TEMPLATE_DIR/wiki-index.md" wiki/index.md
    echo "  Seeded wiki/index.md (landing page)"
fi
# Hub pages for the notes / todos surfaces. Bucket stubs carry a
# `Part of [[notes|todos]].` wikilink that targets these pages, which
# keeps them connected in Obsidian's graph view instead of floating
# as an isolated cluster of empty nodes.
if [ ! -s wiki/notes.md ] && [ -f "$TEMPLATE_DIR/notes-overview.md" ]; then
    cp "$TEMPLATE_DIR/notes-overview.md" wiki/notes.md
    echo "  Seeded wiki/notes.md (notes surface overview)"
fi
if [ ! -s wiki/todos.md ] && [ -f "$TEMPLATE_DIR/todos-overview.md" ]; then
    cp "$TEMPLATE_DIR/todos-overview.md" wiki/todos.md
    echo "  Seeded wiki/todos.md (todos surface overview)"
fi
_seed_notes_or_todos_stub wiki/todos/day.md '[todo] day-priority' todo-list todos \
    'Todos for today or the next few days. Add a `- [ ]` line below; tick the box to mark it done — the curator will move completed items to this year archive on the next sweep.'
_seed_notes_or_todos_stub wiki/todos/month.md '[todo] month-priority' todo-list todos \
    'Todos for the coming month. Add directly here, or add to [[unfiled]] with a `priority: month` tag and the curator will move it on the next sweep.'
_seed_notes_or_todos_stub wiki/todos/year.md '[todo] year-priority' todo-list todos \
    'Todos for this year — the catch-all bucket and the default destination for `/todo` when no temporal cue is given. Add directly, or add to [[unfiled]] without a priority tag and the curator will land them here.'
_seed_notes_or_todos_stub wiki/todos/unfiled.md '[todo] unfiled (priority pending)' todo-list todos \
    'New todos that have not yet been filed. Add a `- [ ]` line below; include an optional `priority: day`, `priority: month`, or `priority: year` tag and the curator will move it to the matching bucket on the next sweep. No tag → defaults to year.'

# Copy slash commands into the workspace's .claude/commands/ directory.
# These register /day, /month, /year, /todo, /note for Claude Code
# sessions opened in this workspace. Non-Claude-Code CLIs (Codex,
# Copilot Chat, Gemini CLI) will ignore the directory; users fall back
# to natural-language invocation which the agent handles the same way.
if [ -d "$TEMPLATE_DIR/claude-commands" ]; then
    for _cmd in "$TEMPLATE_DIR/claude-commands"/*.md; do
        [ -f "$_cmd" ] || continue
        _cmd_name="$(basename "$_cmd")"
        if [ ! -f ".claude/commands/$_cmd_name" ]; then
            cp "$_cmd" ".claude/commands/$_cmd_name"
        fi
    done
fi

# Figure asset PNGs live inside the wiki at wiki/figures/_assets/ so
# they're inside the Obsidian vault scope (clean inline rendering)
# and inside the static viewer's bundle path so its <img> tags resolve.
# The folder is gitignored in the wiki repo because the binaries are
# regenerable from vault PDFs via figures.py regen — committing them
# would bloat the repo for no portability gain. The `_` prefix is a
# widely-recognised "supporting files, not content" convention that
# also makes it easy for users to hide the folder from Obsidian's
# graph view with a `-path:_assets` filter.
mkdir -p wiki/figures/_assets
_wiki_gitignore="wiki/.gitignore"
_gitignore_line="/figures/_assets/"
if [ ! -f "$_wiki_gitignore" ] || ! grep -qE "^/?figures/_assets(/|$)" "$_wiki_gitignore" 2>/dev/null; then
    if [ ! -f "$_wiki_gitignore" ]; then
        printf '# Figure asset PNGs — regenerated from vault PDFs by figures.py\n%s\n' "$_gitignore_line" > "$_wiki_gitignore"
    else
        printf '\n# Figure asset PNGs — regenerated from vault PDFs by figures.py\n%s\n' "$_gitignore_line" >> "$_wiki_gitignore"
    fi
    echo "  Added $_gitignore_line to wiki/.gitignore"
fi

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
    if _is_interactive; then
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

# config.json: copy if missing; otherwise merge any keys the template
# has added since the user's config was last written. Additive only —
# never overwrites a value the user has tuned, and descends into nested
# dicts (e.g. the `compression` block) so added sub-keys land too.
#
# Includes a one-shot migration: if the existing config still uses the
# legacy top-level `worker_model` / `reviewer_model` shape (pre-preset
# era), infer which vendor the values belong to, lift them into a
# matching preset block, set `active_preset`, and drop the top-level
# keys. The standard additive merge then fills in any other seeded
# presets (claude/codex/gemini) the user doesn't already have.
if [ ! -f ".curator/config.json" ]; then
    cp "$TEMPLATE_DIR/config.json" ".curator/config.json"
    echo "  Created .curator/config.json"
else
    uv run --no-project python3 - "$TEMPLATE_DIR/config.json" .curator/config.json <<'PY'
import json, sys
from pathlib import Path
template = json.load(open(sys.argv[1]))
existing_path = Path(sys.argv[2])
existing = json.load(open(existing_path))

def infer_preset_name(worker, reviewer):
    s = (str(worker or "") + " " + str(reviewer or "")).lower()
    if "claude" in s or "anthropic" in s: return "claude"
    if "gpt-" in s or s.startswith("o1") or " o1" in s: return "codex"
    if "gemini" in s: return "gemini"
    if "ollama/" in s or "llama" in s or "qwen" in s: return "ollama"
    return "custom"

migrated = False
if "presets" not in existing and ("worker_model" in existing or "reviewer_model" in existing):
    worker = existing.pop("worker_model", None)
    reviewer = existing.pop("reviewer_model", None)
    name = infer_preset_name(worker, reviewer)
    block = {}
    if worker is not None:   block["worker_model"]   = worker
    if reviewer is not None: block["reviewer_model"] = reviewer
    existing["active_preset"] = name
    existing["presets"] = {name: block}
    migrated = True

added = []
def merge(tmpl, cur, prefix=""):
    for k, v in tmpl.items():
        qname = f"{prefix}{k}"
        if k not in cur:
            cur[k] = v
            added.append(qname)
        elif isinstance(v, dict) and isinstance(cur[k], dict):
            merge(v, cur[k], qname + ".")
merge(template, existing)

if migrated or added:
    existing_path.write_text(json.dumps(existing, indent=2) + "\n")
    if migrated:
        print(f"  Migrated legacy worker_model/reviewer_model into presets.{existing['active_preset']}")
    if added:
        print(f"  Merged {len(added)} new key(s) from template: {', '.join(added)}")
PY
fi
# Drop the config.example.json alongside so users can see cross-vendor
# variants (Anthropic default, Gemini, OpenAI, Ollama fully-local, mixed).
# Always refresh — it's a reference file, never user-tuned.
if [ -f "$TEMPLATE_DIR/config.example.json" ]; then
    cp "$TEMPLATE_DIR/config.example.json" ".curator/config.example.json"
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
refresh_template_md "$TEMPLATE_DIR/AGENTS.md" "AGENTS.md"

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
        "scripts/figures.py"                 # post-figures-feature allowlist
        "Write(/tmp/"                        # post-curate-scratch allowlist
        "scripts/update.sh"                  # post-in-session-update allowlist
        "scripts/naming.py"                  # post-naming-allowlist-gap fix
        "scripts/viewer.sh"                  # post-custom-viewer allowlist
        "scripts/viewer_server.py"           # post-edit-mode allowlist
        "printenv CURATOR_PRESET"            # post-preset-config allowlist
        "scripts/projects.py"                # post-multi-project allowlist
        "scripts/activity_log.py"            # post-activity-log allowlist
        "scripts/planner.py"                 # post-recency-planner allowlist
        "scripts/identifier_resolve.py"      # post-resolver-split allowlist
        "identifier_resolve.py review"       # post-allowlist-narrowing canary;
                                              # forces regen on workspaces that
                                              # had the broad `identifier_resolve.py:*`
                                              # rule from v0.1.2 — narrowing it
                                              # to review/status only requires
                                              # the file be rewritten
        "scripts/restyle.py"                 # v0.3.0 — RESTYLE wave
                                              # orchestrator
        "scripts/scan.py"                    # v0.4.0 — project-dir
                                              # scanner
        "scripts/okf_export.py"              # v0.9.0 — Open Knowledge
                                              # Format export
        "scripts/bootstrap.py"               # v0.9.2 — high-volume
                                              # bootstrap densify
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
    # Indicator of the pre-fix allowlist generator that had two bugs:
    # (a) literal `$root` in the Read entry (variable never expanded),
    # and (b) `\/` escape behaviour in path substitution that hid the
    # sibling .claude/skills/ ↔ .agents/skills/ form. Both emit together.
    if [ -z "$missing_canary" ] && grep -qF 'Read($root/**)' .claude/settings.json; then
        missing_canary='Read($root/**) (stale: broken variable-expansion in pre-fix allowlist generator)'
    fi
    # Quartz was removed in favour of the curiosity-engine-native viewer;
    # workspaces still listing scripts/quartz.sh in their allowlist need
    # regen so that stale entry is dropped.
    if [ -z "$missing_canary" ] && grep -qF 'scripts/quartz.sh' .claude/settings.json; then
        missing_canary='scripts/quartz.sh (stale: Quartz removed, viewer.sh replaces it)'
    fi
    if [ -n "$missing_canary" ]; then
        echo "  Existing .claude/settings.json is missing canonical allowlist"
        echo "  entry matching: $missing_canary"
        if _is_interactive; then
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
      "Bash(uv run python3 $root/scripts/figures.py:*)",
      "Bash(uv run python3 $root/scripts/restyle.py:*)",
      "Bash(uv run python3 $root/scripts/scan.py:*)",
      "Bash(uv run python3 $root/scripts/naming.py:*)",
      "Bash(uv run python3 $root/scripts/projects.py:*)",
      "Bash(uv run python3 $root/scripts/identifier_resolve.py review:*)",
      "Bash(uv run python3 $root/scripts/identifier_resolve.py status:*)",
      "Bash(uv run python3 $root/scripts/activity_log.py:*)",
      "Bash(uv run python3 $root/scripts/planner.py:*)",
      "Bash(uv run python3 $root/scripts/wiki_render.py:*)",
      "Bash(uv run python3 $root/scripts/viewer_server.py:*)",
      "Bash(uv run python3 $root/scripts/okf_export.py:*)",
      "Bash(uv run python3 $root/scripts/bootstrap.py:*)",
      "Bash(bash $root/scripts/evolve_guard.sh:*)",
      "Bash(bash $root/scripts/viewer.sh:*)",
      "Bash(bash $root/scripts/update.sh:*)",
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
      "Write(/tmp/**)",
      "Edit(/tmp/**)",
EOF
    # Skill-script read access — the orchestrator occasionally re-reads
    # a hash-guarded script to confirm flag syntax. Safe to allow:
    # scripts are read-only (hash-guarded) and contain no secrets. One
    # entry per available skill-root path (physical + logical if they
    # differ, per the dual-path allowlist logic above).
    for root in "${SKILL_ROOTS[@]}"; do
        printf '      "Read(%s/**)",\n' "$root" >> .claude/settings.json
    done
    cat >> .claude/settings.json <<EOF
      "Bash(date:*)",
      "Bash(printenv CURATOR_PRESET:*)"
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

# Optional: semantic search (fastembed + sqlite-vec). fastembed runs the
# embedding model on ONNX — no PyTorch, ~50MB of deps + ~65MB of model
# weights (BAAI/bge-small-en-v1.5) — and powers hybrid FTS5 + cosine
# vault search, graph.py retrieve's semantic seeding, and the provisional
# embedding-neighbor edge tier. Most small vaults (<500 sources) don't
# need this — FTS5 keyword search covers the common case. Opt in when
# you start hitting paraphrased queries that miss with keyword alone.
# (Workspaces that already have sentence-transformers installed keep
# working — embedder.py falls back to it, preserving their MiniLM
# vector space.)
if _is_interactive; then
    echo ""
    printf "Install semantic search (fastembed + sqlite-vec, ~115MB)? [y/N] "
    read -r reply_embed || reply_embed="n"
    case "$reply_embed" in
        y|Y|yes|YES)
            # pysqlite3 is needed because macOS system Python's sqlite3 is
            # typically compiled without --enable-loadable-sqlite-extensions,
            # which breaks sqlite-vec. pysqlite3 is a drop-in replacement
            # built from source with extensions enabled. No-op on Linux
            # distros that already have extensions — but the build needs
            # a C compiler. Warn early so the failure (if it happens) has
            # a clear cause in the user's terminal scrollback.
            _has_cc=0
            for _c in cc gcc clang; do
                command -v "$_c" >/dev/null 2>&1 && _has_cc=1 && break
            done
            if [ "$_has_cc" -eq 0 ]; then
                echo ""
                echo "  WARN: no C compiler (cc/gcc/clang) found on PATH."
                echo "        pysqlite3 likely needs to build from source and will fail."
                echo "        Install build tools first:"
                echo "          macOS:  xcode-select --install"
                echo "          Debian/Ubuntu:  apt install build-essential"
                echo "          Fedora/RHEL:    dnf groupinstall 'Development Tools'"
                echo "        Proceeding anyway — the error below will be the compiler's."
                echo ""
            fi
            echo "  Installing fastembed + sqlite-vec (+ pysqlite3) into .venv ..."
            if uv pip install fastembed sqlite-vec pysqlite3; then
                # Flip embedding_enabled to true in config.json so vault_index
                # will compute embeddings on next ingest / --rebuild. Only
                # default the model when the key is absent — a workspace
                # with an existing embedding_model keeps its vector space.
                uv run --no-project python3 -c "
import json
from pathlib import Path
p = Path('.curator/config.json')
cfg = json.loads(p.read_text())
cfg['embedding_enabled'] = True
cfg.setdefault('embedding_model', 'BAAI/bge-small-en-v1.5')
p.write_text(json.dumps(cfg, indent=2))
"
                echo "  Enabled embedding_enabled=true in .curator/config.json"
                echo "  To embed the existing vault:"
                echo "    uv run python3 $SCRIPT_DIR/vault_index.py --rebuild"
            else
                echo "  Install failed. Enable later:"
                echo "    uv pip install fastembed sqlite-vec"
            fi
            ;;
        *)
            echo "  Skipping semantic search. Enable later:"
            echo "    uv pip install fastembed sqlite-vec"
            echo "    (then set embedding_enabled=true in .curator/config.json)"
            ;;
    esac
fi

# A workspace whose config ALREADY declares embeddings on needs the deps
# regardless of how setup was invoked. This is the shipped-workspace case:
# clone or unpack a workspace with `embedding_enabled: true`, run setup
# non-interactively, and without this the prompt above never fires — the
# first vault_index/graph call then hard-fails on a missing sqlite-vec,
# with a config that says embeddings are on. The config is the statement
# of intent; setup satisfies it rather than leaving it contradicted.
if [ -f ".curator/config.json" ]; then
    _wants_embed=$(uv run --no-project python3 -c "
import json
try:
    print('yes' if json.load(open('.curator/config.json')).get('embedding_enabled') else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")
    if [ "$_wants_embed" = "yes" ] && \
       ! uv run python3 -c "import sqlite_vec" >/dev/null 2>&1; then
        echo ""
        echo "  config.json sets embedding_enabled=true but the embedding"
        echo "  deps are missing — installing them so the workspace works ..."
        if uv pip install fastembed sqlite-vec pysqlite3 >/dev/null 2>&1; then
            echo "  Installed fastembed + sqlite-vec (+ pysqlite3)."
        else
            echo "  WARNING: install failed. Either run"
            echo "    uv pip install fastembed sqlite-vec"
            echo "  or set embedding_enabled=false in .curator/config.json —"
            echo "  otherwise vault_index.py and graph.py will fail."
        fi
    fi
fi

# Optional: install curiosity-merge for cross-wiki operations
# (merge, unmerge, subgraph-export, discover-bridges). Most users
# don't need this — only install when you want to combine wikis,
# share sub-wikis via GitHub, or absorb someone else's published
# wiki. Trust model is different (external data ingestion) so it's
# a deliberate opt-in. Public sub-wikis are tagged with the
# `curiosity-wiki` GitHub topic; search topic:curiosity-wiki to
# find ones you can clone, fork, or merge.
if _is_interactive; then
    echo ""
    printf "Install curiosity-merge for cross-wiki ops (merge, unmerge, subgraph-export, discover-bridges)? [y/N] "
    read -r reply_merge || reply_merge="n"
    case "$reply_merge" in
        y|Y|yes|YES)
            if command -v npx >/dev/null 2>&1; then
                echo "  Installing benjsmith/curiosity-merge via npx skills (global, symlinks) ..."
                npx skills add -g -y benjsmith/curiosity-merge \
                    || echo "  (install failed — re-run later: npx skills add -g -y benjsmith/curiosity-merge)"
            else
                echo "  npx not found. Install later: npx skills add -g -y benjsmith/curiosity-merge"
            fi
            ;;
        *)
            echo "  Skipping curiosity-merge. Install anytime: npx skills add -g -y benjsmith/curiosity-merge"
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
        echo "  Running behavioral-migration pass (resync-stems, backfill-kept-as, fix-index, graph rebuild) ..."
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-frontmatter-quotes wiki >/dev/null
        # Add or correct the canonical [con]/[ent]/[tbl]/... bracket
        # prefix on every page title, picking the value from
        # naming.TYPE_PREFIX. Catches summary-table pages a worker built
        # without the [tbl] tag and legacy pages with `[concept]` /
        # `[entity]` (full-word) prefixes from earlier skill versions.
        # Idempotent no-op once every title is canonical.
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-title-prefixes wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" dedupe-self-citations wiki >/dev/null
        # Sweep up zero-byte .md files at wiki/ root — almost always
        # Obsidian click-artefacts from unresolved wikilinks (e.g. a
        # literal `[[wikilinks]]` placeholder in a template rendering
        # as a clickable link). Seeded hub pages (index/notes/todos)
        # are populated above, so any remaining top-level empty file
        # is genuinely orphaned. Idempotent no-op when clean.
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-orphan-root-files wiki >/dev/null 2>&1 || true
        # One-shot migration for workspaces whose figure assets still
        # live under workspace/assets/figures/. Moves them into
        # wiki/figures/_assets/, rewrites embed paths to match the
        # configured viewer mode, removes the old empty dirs, adds
        # the new gitignore line. Idempotent no-op once applied.
        uv run python3 "$SCRIPT_DIR/sweep.py" migrate-asset-location wiki >/dev/null 2>&1 || true
        # Retrofit source-stub wikilinks into figure pages that were
        # created before the mechanical-wikilink rule was wired in.
        # Idempotent no-op once all figure pages carry a wikilink.
        uv run python3 "$SCRIPT_DIR/sweep.py" backfill-figure-sourcelinks wiki >/dev/null 2>&1 || true
        # Retrofit `Part of [[notes|todos]].` hub wikilinks into
        # bucket pages seeded before the hub convention existed, so
        # they show as connected in Obsidian's graph view. Idempotent.
        uv run python3 "$SCRIPT_DIR/sweep.py" backfill-bucket-hubs wiki >/dev/null 2>&1 || true
        # Undo sync-todos pollution on hub pages + completion archives
        # from the window where sync-todos parsed inside fenced code
        # blocks. Matches the literal `(todo:T<id>)` template marker to
        # find the bad lines; orphan sqlite rows are purged too.
        # Idempotent no-op once clean.
        uv run python3 "$SCRIPT_DIR/sweep.py" purge-template-todo-artefacts wiki >/dev/null 2>&1 || true
        # Earlier skill versions seeded the todos class-table on
        # wiki/entities/todos.md alongside the wiki/todos.md hub —
        # consolidate to a single source of truth on the hub.
        # Idempotent no-op once the entity page is gone.
        uv run python3 "$SCRIPT_DIR/sweep.py" consolidate-todos-page wiki >/dev/null 2>&1 || true
        # One-shot migration for vault files ingested before the
        # local_ingest suffix-doubling fix (foo.pdf.pdf → foo.pdf).
        # Idempotent no-op once applied.
        uv run python3 "$SCRIPT_DIR/sweep.py" normalize-vault-suffixes wiki >/dev/null 2>&1 || true
        # Sync the canonical todos class-table schema (idempotent — creates
        # the table on first run, re-hashes on schema change) and drain any
        # user-authored todos / notes into their structured homes. The
        # schema lives on wiki/todos.md (concept hub). Earlier skill
        # versions seeded a separate wiki/entities/todos.md; if both
        # files coexist in an existing workspace, consolidate-todos-page
        # below merges and removes the stale entity copy.
        if [ -f wiki/todos.md ]; then
            uv run python3 "$SCRIPT_DIR/tables.py" sync wiki/todos.md >/dev/null 2>&1 || true
        fi
        uv run python3 "$SCRIPT_DIR/sweep.py" sync-todos wiki >/dev/null 2>&1 || true
        uv run python3 "$SCRIPT_DIR/sweep.py" sync-notes wiki >/dev/null 2>&1 || true
        # Align figure-page image-embed syntax with the configured viewer.
        # Default obsidian; user switches to "vscode" for VS Code preview
        # compatibility. Idempotent when already in target form.
        _viewer_mode=$(uv run --no-project python3 -c "
import json, sys
try:
    print(json.load(open('.curator/config.json')).get('wiki_viewer_mode', 'obsidian'))
except Exception:
    print('obsidian')
" 2>/dev/null || echo "obsidian")
        uv run python3 "$SCRIPT_DIR/sweep.py" convert-image-embeds wiki --target "$_viewer_mode" >/dev/null 2>&1 || true
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-stems wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-prefixes wiki >/dev/null
        # Extractions ingested in place from inside vault/ predating the
        # kept_as fix carry no kept_as, which locked them out of the
        # multimodal and figure queues. Idempotent; skips extractions that
        # already have it and anything whose original is outside the vault.
        uv run python3 "$SCRIPT_DIR/sweep.py" backfill-kept-as wiki >/dev/null 2>&1 || true
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-index wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/graph.py" rebuild wiki >/dev/null
        # Regenerate any figure assets missing from assets/figures/ (first
        # clone, or the folder was cleaned). Deterministic from vault
        # sources; created-origin figures cannot be auto-regenerated and
        # are surfaced by figures.py check for human review.
        uv run python3 "$SCRIPT_DIR/figures.py" regen wiki >/dev/null 2>&1 || true
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
