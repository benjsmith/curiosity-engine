# Release checklist

Run through this before tagging a new release. Items marked **(security)** must be verified — they exist to keep the next Socket / Trust Hub scan clean.

## Pre-release

- [ ] **(security) Vendor bundle review.** Check that the in-tree vendor JS at `skills/curiosity-engine/template/wiki-view/static/vendor/` matches the latest patched releases of D3 and Fuse.js. Rationale: the bundles are committed in-repo to keep the viewer build offline-capable and to close the CDN supply-chain risk; the tradeoff is that we own the upgrade cadence. The bundles ship to every workspace bundle that calls `viewer.sh build`.

      Currently shipped (refresh this table on every bump):

      | File | Version | sha256 | Source |
      |------|---------|--------|--------|
      | `d3.min.js` | 7.9.0 | `f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539` | `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js` |
      | `fuse.min.js` | 7.0.0 | `e3621b53cb77b4ec306dec41ed95511e6dd80d17fae5a04f3e346d214b9f8f92` | `https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js` |

      To refresh:

      ```
      curl -fsSL -o /tmp/d3.min.js   https://cdn.jsdelivr.net/npm/d3@<v>/dist/d3.min.js
      curl -fsSL -o /tmp/fuse.min.js https://cdn.jsdelivr.net/npm/fuse.js@<v>/dist/fuse.min.js
      shasum -a 256 /tmp/d3.min.js /tmp/fuse.min.js
      mv /tmp/d3.min.js   skills/curiosity-engine/template/wiki-view/static/vendor/d3.min.js
      mv /tmp/fuse.min.js skills/curiosity-engine/template/wiki-view/static/vendor/fuse.min.js
      # Update the table above with the new versions + hashes.
      # Run viewer.sh build in a test workspace; click around to confirm
      # the graph renders and search works (Fuse.js is the search lib).
      ```

      Watch for: D3 7.x → 8.x is a breaking API change; viewer code may need updates. Fuse 7.x has been stable.

- [ ] **(security) Re-run the Socket / Trust Hub scan** if the release window introduced any new external network call, subprocess invocation, dynamic import, or compile/eval pattern. New surface = new finding to declare in `SECURITY.md`.

- [ ] **CHANGELOG.md updated** with a new dated section + version marker. Match the existing style (date, version, brief description, commit hashes for major changes).

- [ ] **Verify `setup.sh` migration pass on a real workspace.** `cd <workspace> && CURIOSITY_ENGINE_NONINTERACTIVE=1 bash <skill>/scripts/setup.sh` should run idempotently. If it produces unexpected wiki/ diffs, the migration is over-eager and needs a guard.

- [ ] **Smoke-test on a duplicate workspace** if any classifier, planner, or sweep op changed: clone an existing workspace, run the affected ops, confirm output is sensible. Don't ship a release without exercising on real wiki content.

## Tagging

Version format is CalVer `YYYY.MINOR.MICRO` — see the versioning policy below for what each segment means and how to pick the next one.

- [ ] Confirm the previous release so `MINOR` continues correctly. Filter to the current year, because the legacy `v0.*` tags sort *after* every `2026.*` tag (`v` follows digits), so a bare `| tail` shows stale tags forever:
      ```
      git tag --sort=v:refname -l '2026.*' | tail -3     # last few this year
      git tag --sort=-creatordate | head -5              # most recent, any scheme
      ```
- [ ] Tag at the head commit on `main`: `git tag -a 2026.N.0 -m "2026.N.0 — short summary"`. No `v` prefix.
- [ ] Push tag: `git push origin 2026.N.0`.
- [ ] Publish release on GitHub with notes drawn from the CHANGELOG entry.

**Bundling a late fix into an already-tagged release** (rather than cutting a new one): append a `**Bundled fix — …**` paragraph to that release's existing CHANGELOG section, move the tag (`git tag -d`, re-tag at head, `git push --force origin <tag>`), and sync the GitHub release body to match. Use this only for a small fix found immediately after tagging — anything a user might already have installed gets its own `MICRO`.

## Versioning policy — CalVer `YYYY.MINOR.MICRO`

Calendar versioning, JetBrains-style. **Adopted 2026-07-30**; tags up to and including `v0.10.0` are SemVer and stay as they are (see "Why the switch" below).

- **`YYYY`** — four-digit year of release. `2026.*`
- **`MINOR`** — sequential release within that year, starting at `1` and reset every January. Not a month.
- **`MICRO`** — fix release against that same `MINOR`, starting at `0`.

So `2026.1.0` → `2026.1.1` (a fix on it) → `2026.2.0` (the next release) → `2027.1.0`.

**Tags carry no `v` prefix** — `2026.1.0`, not `v2026.1.0`. The year makes the scheme self-evident and it matches how CalVer projects tag. Historical `v0.*` tags keep their prefix; don't rewrite them.

**Sorting.** Two separate traps, both verified against this repo's tags:

1. `MINOR` is deliberately not zero-padded (that's the JetBrains form), so a plain lexical sort puts `2026.10.0` *before* `2026.2.0`. Use `--sort=v:refname`, which compares numeric segments numerically and gets it right.
2. Even with `--sort=v:refname`, the whole `2026.*` block sorts **before** every legacy `v0.*` tag, because a digit precedes `v`. So `git tag --sort=v:refname | tail` shows `v0.10.0` indefinitely and never the newest release.

```
git tag --sort=v:refname -l '2026.*' | tail -3   # right: newest, current year
git tag --sort=-creatordate | head -5            # right: newest, scheme-agnostic
git tag --sort=v:refname | tail -5               # WRONG: stuck on the v0.* tags
git tag | tail -5                                # WRONG twice over
```

GitHub's release list is date-ordered, so it's unaffected. This doesn't go away with time — the `v0.*` tags sit at the end of the sorted list permanently — so keep filtering by the current year's glob (`-l '2027.*'` in 2027, and so on), or use the `creatordate` form, which needs no maintenance.

### Communicating compatibility

CalVer says when a release happened, not whether it breaks you. That signal moves into `CHANGELOG.md`, and it is now **mandatory rather than a nicety**:

- A release that needs a one-off command against existing workspaces carries a bolded **`Migration:`** line saying exactly what to run — as v0.9.5 did for `vault_index.py --rebuild`. If `setup.sh`'s migration pass handles it automatically (as it does for `backfill-kept-as`), say that instead, so nobody runs it by hand.
- A release that removes or renames a config key, sweep command, script flag, or frontmatter key carries a bolded **`Breaking:`** line naming the old and new form.
- `embedder.py`'s public surface (`load_embedder`, `predict_model_id`, and the `Embedder` API) was declared stable under the old policy as "breaking changes only on a major bump". There is no major number now, so the promise is restated as: **breaking changes to that surface require a `Breaking:` line and a release of their own** — never bundled into a release that also ships features.

There is no `MAJOR` escape hatch, which is the real cost of this scheme. A release that would have been `2.0.0` looks identical to a bugfix from the outside, so the changelog line is the only thing standing between a user and a surprise. Write it first, not last.

### Why the switch

The project sat at `0.x` for its whole life, and under SemVer `0.x` explicitly promises nothing — so every release was paying the cost of classifying patch-vs-minor while the version number carried no guarantee anyone could rely on. Meanwhile the thing users actually need to know is "how stale is my install?", which `0.10.0` answers not at all and `2026.1.0` answers at a glance. Releases here are also empirical and bursty (two shipped on 2026-07-29, both driven by defects a real curate run exposed) rather than planned around an API contract, which is the shape CalVer fits.

Nothing in the codebase parses or compares a version string — the version lives only in git tags, `CHANGELOG.md` headings, and GitHub release titles — so the switch required no code change.

## Post-release

- [ ] Refresh the global skill install on workstations that use it: `npx skills update -g curiosity-engine`. Existing workspaces pick up the new skill on the next CURATE run; if any new config keys landed, `setup.sh`'s additive merge brings them in.
