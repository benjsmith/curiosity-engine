# Curiosity Engine integration

The integration already ships in this repo, flag-gated and off by
default — this note documents how it works and how to try it.

## Try it

```sh
cd packages/knowledge-atlas && pnpm install && pnpm run build
cp dist/knowledge-atlas.iife.js \
   ../../skills/curiosity-engine/template/wiki-view/static/vendor/knowledge-atlas.js
# (already done in-tree; repeat only after engine changes — and refresh
#  the sha256 row in RELEASE_CHECKLIST.md)

cd <your-workspace>
bash <skill_path>/scripts/viewer.sh open
# then visit  http://localhost:8090/?viewer=atlas
# or persist: localStorage['curiosity-engine.viewer'] = 'atlas'
```

## How it works

Three files in `template/wiki-view/`:

- `static/vendor/knowledge-atlas.js` — the vendored IIFE bundle
  (engine + Canvas renderer + CE adapter, 56 KB min / 20 KB gzip,
  zero dependencies), exposing `window.KnowledgeAtlas.mount`.
- `static/atlas.js` — the glue: checks the flag, mounts the atlas
  into `#graph` on the already-fetched `data.json`, and returns a
  `Graph`-compatible facade (`focus`, `clearFocus`, …).
- `static/main.js` — picks the facade when the flag is on; sidebar,
  modal, 1-hop subgraph navigator, editing and hash routing are
  untouched. Item-open events route through `#page=<id>`, so the
  modal opens exactly as with the classic viewer.

The adapter consumes today's `data.json` unchanged (it also
normalises the payload's known sharp edges — see
`src/datasources/curiosity.ts`). When `wiki_render.py` later exports
Cites/ProvisionalLink edges (`--atlas-edges`, PLAN §14.3), the
adapter picks them up automatically and stops deriving its own
co-citation relations.

This path is proven by `e2e/embedding.spec.ts`, which loads the
vendored bundle + glue against a real payload in a bare page.
