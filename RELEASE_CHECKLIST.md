# Release checklist

Run through this before tagging a new release. Items marked **(security)** must be verified — they exist to keep the next Socket / Trust Hub scan clean.

## Pre-release

- [ ] **(security) Vendor bundle review.** Check that the in-tree vendor JS at `template/wiki-view/static/vendor/` matches the latest patched releases of D3 and Fuse.js. Rationale: the bundles are committed in-repo to keep the viewer build offline-capable and to close the CDN supply-chain risk; the tradeoff is that we own the upgrade cadence. The bundles ship to every workspace bundle that calls `viewer.sh build`.

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
      mv /tmp/d3.min.js   template/wiki-view/static/vendor/d3.min.js
      mv /tmp/fuse.min.js template/wiki-view/static/vendor/fuse.min.js
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

- **Patch (`vX.Y.Z`)**: bug fixes only, no new commands, no new config keys, no API changes.
- **Minor (`vX.Y.0`)**: backward-compatible features; new commands, new config keys with defaults preserving existing behaviour.
- **Major (`vX.0.0`)**: breaking changes (renamed commands, removed config keys, schema changes). The skill is at `0.x.y` while pre-1.0 — no `1.0.0` stability promises yet.

## Post-release

- [ ] Refresh the global skill install on workstations that use it: `npx skills update -g curiosity-engine`. Existing workspaces pick up the new skill on the next CURATE run; if any new config keys landed, `setup.sh`'s additive merge brings them in.
