# Release checklist

Run through this before tagging a new release. Items marked **(security)** must be verified — they exist to keep the next Socket / Trust Hub scan clean.

## Pre-release

- [ ] **(security) Vendor bundle review.** Check that the in-tree vendor JS at `skills/curiosity-engine/template/wiki-view/static/vendor/` matches the latest patched releases of D3 and Fuse.js. Rationale: the bundles are committed in-repo to keep the viewer build offline-capable and to close the CDN supply-chain risk; the tradeoff is that we own the upgrade cadence. The bundles ship to every workspace bundle that calls `viewer.sh build`.

      Currently shipped (refresh this table on every bump):

      | File | Version | sha256 | Source |
      |------|---------|--------|--------|
      | `d3.min.js` | 7.9.0 | `f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539` | `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js` |
      | `fuse.min.js` | 7.0.0 | `e3621b53cb77b4ec306dec41ed95511e6dd80d17fae5a04f3e346d214b9f8f92` | `https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js` |
      | `knowledge-atlas.js` | 0.1.0 (in-repo) | `f640076c3bb3f39dbd1642fec2acc08b523fd7166c7a3e03bb9c53eaf7764208` | built in-repo: `cd packages/knowledge-atlas && pnpm run build`, then copy `dist/knowledge-atlas.iife.js` here (no CDN — first-party code; refresh the hash on every rebuild) |

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

- [ ] Tag at the head commit on `main`: `git tag -a vX.Y.Z -m "vX.Y.Z — short summary"`.
- [ ] Push tag: `git push origin vX.Y.Z`.
- [ ] Publish release on GitHub with notes drawn from the CHANGELOG entry.

## Versioning policy

Semantic versioning, and **since `v1.0.0` the promises are real** — under `0.x` SemVer explicitly guarantees nothing, so the classification cost bought no signal. Post-1.0 the major number is the one thing a user can act on without reading the changelog.

- **Patch (`vX.Y.Z`)**: bug fixes only. No new commands, no new config keys, no behaviour change to a documented contract.
- **Minor (`vX.Y.0`)**: backward-compatible additions — new commands, new config keys whose defaults preserve existing behaviour, new frontmatter keys, new optional flags.
- **Major (`vX.0.0`)**: anything a user must react to. Concretely, for this project:
  - a renamed or removed sweep/graph/planner command, script flag, or config key;
  - a frontmatter key whose meaning changes (not one that's merely added);
  - a change to `embedder.py`'s public surface (`load_embedder`, `predict_model_id`, the `Embedder` API), which is a declared-stable library surface other tools vendor;
  - a migration that `setup.sh`'s additive merge cannot perform silently and correctly.

**Migrations do not by themselves set the version — what happens if you skip one does.** Every migration carries a bolded **`Migration:`** line in its CHANGELOG entry saying exactly what to run; the version digit is decided separately:

- **Skipping it leaves the workspace correct, just without the improvement** → patch is fine. `vault_index.py --rebuild` to pick up a text-normalisation fix is this shape: the old index keeps working exactly as before, and the rebuild is how you *gain* the fix.
- **Skipping it leaves the workspace wrong, or the release changes behaviour a user depends on** → minor at least, and major if a documented contract moved.
- **`setup.sh` performs it automatically** → does not influence the version at all; say so in the entry so nobody runs it by hand.

The distinction matters because the version number's job is to signal *compatibility*, not to advertise that an optional improvement exists. An earlier draft of this policy said any hand-run migration was "a minor at least", which would have forced a minor bump on a pure bug-fix release whose migration was entirely optional.

### The 0.x history

Everything through `v0.10.0` predates the 1.0 promise and was versioned on the same shape without the guarantee. Those tags stay as they are.

**A brief CalVer experiment (`YYYY.MINOR.MICRO`, JetBrains-style) was considered and reverted before any release was tagged under it.** The argument for it was that `0.x` carried no compatibility signal anyway, so a date at least tells you how stale your install is. The argument against won: this project has a real compatibility surface — config keys, frontmatter keys, command names, a declared-stable library API, and an `update.sh` migration pass that occasionally does destructive work — and CalVer has no way to say "this one can break you". Cutting `v1.0.0` gets that signal back rather than giving it up. No CalVer tag was ever published, so nothing downstream saw it.

## Post-release

- [ ] Refresh the global skill install on workstations that use it: `npx skills update -g curiosity-engine`. Existing workspaces pick up the new skill on the next CURATE run; if any new config keys landed, `setup.sh`'s additive merge brings them in.
