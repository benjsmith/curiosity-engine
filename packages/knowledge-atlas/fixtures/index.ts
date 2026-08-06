/**
 * Deterministic fixtures (PLAN §15). All generated from seeds at
 * import time — no wall clock, no Math.random — so identical across
 * runs, machines and sessions. Each fixture ships an answer key
 * (`expected`) used by discovery tests and harness tasks.
 *
 * 1. workspace-small — realistic CE workspace (~50 sources, ~400
 *    pages, all types), serialised as a genuine CEData payload so it
 *    doubles as the CE-adapter conformance fixture.
 * 2. ontology-tree — deep tree + sparse cross-links.
 * 3. dense-smallworld — high clustering, low diameter, generic hubs.
 * 4. mixed-multiscale — overlapping communities at three scales.
 * (5. the scaled million-node corpus is procedural: datasources/scaled.ts)
 */

import { GraphIndex } from "../src/core/graphindex.ts";
import { LocalSceneSource } from "../src/datasources/local.ts";
import { CuriosityDataSource, type CEData } from "../src/datasources/curiosity.ts";
import { childSeed, splitmix32, type Rng } from "../src/core/random.ts";
import type { AtlasDataSource, KnowledgeItem } from "../src/core/types.ts";

export type Fixture = {
  name: string;
  source: AtlasDataSource;
  defaultFocus: string;
  /** Planted answer keys for evaluation tasks. */
  expected: {
    locate?: string;
    bridge?: string;
    contrastPair?: [string, string];
    hiddenConnection?: [string, string];
    genericHub?: string;
  };
};

const pick = <T>(arr: readonly T[], rng: Rng): T => arr[Math.floor(rng() * arr.length)];

// ── 1. workspace-small (CEData payload) ─────────────────────────────

const CE_PALETTE: Record<string, string> = {
  project: "#4d1ae8", analysis: "#1d6996", concept: "#38a6a5",
  entity: "#0f8554", evidence: "#73af48", fact: "#edad08",
  figure: "#e17c05", table: "#cc503e", source: "#94346e",
  note: "#6f4070", "todo-list": "#9656a2", unclassified: "#ffffff",
  default: "#bbbbbb",
};

export function workspaceSmallData(seed = 42): CEData {
  const rng = splitmix32(childSeed(seed, "workspace-small"));
  type Page = { id: string; title: string; type: string; sources: string[]; links: string[] };
  const pages: Page[] = [];
  const sources: string[] = [];
  for (let i = 0; i < 50; i++) sources.push(`raw/paper-${String(i).padStart(2, "0")}.pdf.extracted.md`);

  // Two thematic clusters (ml, bio) + a bridge topic. Explicit naming
  // keeps the fixture legible in screenshots.
  const clusters = {
    ml: { concepts: [] as string[], srcs: sources.slice(0, 22) },
    bio: { concepts: [] as string[], srcs: sources.slice(25, 47) },
  };
  const add = (dir: string, stem: string, title: string, type: string, prefix: string, srcs: string[], links: string[] = []): string => {
    const id = `${dir}/${stem}`;
    pages.push({ id, title: `[${prefix}] ${title}`, type, sources: srcs, links });
    return id;
  };

  // Source pages (one per vault source).
  for (let i = 0; i < 50; i++) {
    add("sources", `paper-${String(i).padStart(2, "0")}`, `Paper ${i}`, "source", "src", [sources[i]]);
  }
  // Concepts per cluster.
  for (const [ck, names] of [
    ["ml", ["gradient descent", "attention", "transformers", "overfitting", "embeddings", "fine-tuning", "regularisation", "tokenisation", "distillation", "scaling laws", "curriculum learning", "sparse models"]],
    ["bio", ["protein structure", "gene expression", "enzyme kinetics", "cell signalling", "membrane transport", "metabolic pathways", "receptor binding", "molecular chaperones", "phosphorylation", "homeostasis", "apoptosis", "epigenetics"]],
  ] as const) {
    for (const name of names) {
      const c = clusters[ck];
      const id = add("concepts", name.replace(/\s+/g, "-"), name, "concept", "con",
        [pick(c.srcs, rng), pick(c.srcs, rng)]);
      c.concepts.push(id);
    }
  }
  // The generic hub: linked from nearly everything.
  const hub = add("concepts", "machine-learning", "machine learning", "concept", "con", [sources[0], sources[1]]);
  // The bridge: connects both clusters.
  add("concepts", "protein-folding-models", "protein folding models", "concept", "con",
    [clusters.ml.srcs[2], clusters.bio.srcs[2]],
    [clusters.ml.concepts[1], clusters.ml.concepts[2], clusters.bio.concepts[0], clusters.bio.concepts[7]]);

  // Entities, facts, evidence, analyses per cluster.
  const perCluster = (ck: "ml" | "bio", n: { ent: number; fact: number; evi: number; ana: number }) => {
    const c = clusters[ck];
    const entities: string[] = [];
    for (let i = 0; i < n.ent; i++) {
      entities.push(add("entities", `${ck}-entity-${i}`, `${ck} entity ${i}`, "entity", "ent",
        [pick(c.srcs, rng)], [pick(c.concepts, rng), hub]));
    }
    for (let i = 0; i < n.fact; i++) {
      add("facts", `${ck}-fact-${i}`, `${ck} fact ${i}`, "fact", "fact",
        [pick(c.srcs, rng)], [pick(c.concepts, rng), pick(entities, rng)]);
    }
    for (let i = 0; i < n.evi; i++) {
      add("evidence", `${ck}-evidence-${i}`, `${ck} evidence ${i}`, "evidence", "evi",
        [pick(c.srcs, rng), pick(c.srcs, rng)], [pick(c.concepts, rng)]);
    }
    for (let i = 0; i < n.ana; i++) {
      add("analyses", `${ck}-analysis-${i}`, `${ck} analysis ${i}`, "analysis", "ana",
        [pick(c.srcs, rng), pick(c.srcs, rng), pick(c.srcs, rng)],
        [pick(c.concepts, rng), pick(c.concepts, rng), hub]);
    }
    return entities;
  };
  perCluster("ml", { ent: 25, fact: 55, evi: 38, ana: 20 });
  perCluster("bio", { ent: 25, fact: 55, evi: 38, ana: 20 });

  // Planted contradiction: two analyses over the same sources, marked.
  const contraA = add("analyses", "scaling-helps", "scaling always helps", "analysis", "ana",
    [clusters.ml.srcs[5], clusters.ml.srcs[6]], [clusters.ml.concepts[9], hub]);
  const contraB = `analyses/scaling-hurts-downstream`;
  pages.push({
    id: contraB,
    title: "[ana] scaling hurts downstream tasks",
    type: "analysis",
    sources: [clusters.ml.srcs[5], clusters.ml.srcs[6]],
    links: [clusters.ml.concepts[9]],
  });

  // Planted hidden connection: same 3 sources, never linked.
  add("evidence", "ml-hidden-a", "attention entropy collapse", "evidence", "evi",
    [clusters.ml.srcs[10], clusters.ml.srcs[11], clusters.ml.srcs[12]], [clusters.ml.concepts[1]]);
  add("evidence", "ml-hidden-b", "loss spikes at scale", "evidence", "evi",
    [clusters.ml.srcs[10], clusters.ml.srcs[11], clusters.ml.srcs[12]], [clusters.ml.concepts[9]]);

  // Figures / tables / notes / todos / projects for type coverage.
  for (let i = 0; i < 12; i++) {
    add("figures", `fig-${i}`, `figure ${i}`, "figure", "fig", [pick(sources, rng)], [pick(clusters.ml.concepts, rng)]);
  }
  for (let i = 0; i < 6; i++) {
    add("tables", `tab-${i}`, `extracted table ${i}`, i % 2 ? "extracted-table" : "summary-table", "tab", [pick(sources, rng)], []);
  }
  for (let i = 0; i < 15; i++) {
    add("notes", `note-${i}`, `note ${i}`, "note", "note", [], [pick([...clusters.ml.concepts, ...clusters.bio.concepts], rng)]);
  }
  add("todos", "todo-list", "reading queue", "todo-list", "todo", [], []);
  add("projects", "ml-survey", "ML survey", "project", "proj", [], clusters.ml.concepts.slice(0, 4));
  add("projects", "bio-review", "bio review", "project", "proj", [], clusters.bio.concepts.slice(0, 4));

  // Everything links to the hub with probability 0.35 (generic-hub trap).
  for (const p of pages) {
    if (p.id !== hub && !p.id.startsWith("sources/") && rng() < 0.35) p.links.push(hub);
  }

  // Assemble the CEData payload.
  const degree = new Map<string, number>();
  const edges: CEData["edges"] = [];
  const idSet = new Set(pages.map((p) => p.id));
  for (const p of pages) {
    for (const l of p.links) {
      if (!idSet.has(l) || l === p.id) continue;
      edges.push({ source: p.id, target: l, type: "wikilink" });
      degree.set(p.id, (degree.get(p.id) ?? 0) + 1);
      degree.set(l, (degree.get(l) ?? 0) + 1);
    }
  }
  const cePages: CEData["pages"] = {};
  for (const p of pages) {
    cePages[p.id] = {
      id: p.id,
      title: p.title,
      type: p.type === "extracted-table" || p.type === "summary-table" ? "table" : p.type,
      path: `${p.id}.md`,
      properties: { sources: p.sources, created: "2026-01-01" },
      body_html: `<p>${p.title}</p>`,
    };
  }
  // Contradiction marker rides in properties (fixture-planted signal).
  cePages[contraB].properties.contradicts = contraA;
  cePages[contraA].properties.contradicts = contraB;

  return {
    workspace: "fixture-workspace-small",
    generated_at: "2026-01-01T00:00:00+00:00",
    palette: CE_PALETTE,
    nodes: pages.map((p) => ({
      id: p.id,
      path: `${p.id}.md`,
      type: p.type,
      title: p.title,
      degree: degree.get(p.id) ?? 0,
    })),
    edges,
    pages: cePages,
  };
}

export function workspaceSmall(seed = 42): Fixture {
  const data = workspaceSmallData(seed);
  return {
    name: "workspace-small",
    source: new CuriosityDataSource(data, { seed }),
    defaultFocus: "concepts/attention",
    expected: {
      locate: "concepts/protein-structure",
      bridge: "concepts/protein-folding-models",
      contrastPair: ["analyses/scaling-helps", "analyses/scaling-hurts-downstream"],
      hiddenConnection: ["evidence/ml-hidden-a", "evidence/ml-hidden-b"],
      genericHub: "concepts/machine-learning",
    },
  };
}

// ── helper for raw-graph fixtures ───────────────────────────────────

function item(id: string, type: string, title: string, sources: string[] = []): KnowledgeItem {
  return { id, type, title, meta: { sources } };
}

// ── 2. ontology-tree ────────────────────────────────────────────────

export function ontologyTree(seed = 42): Fixture {
  const rng = splitmix32(childSeed(seed, "ontology-tree"));
  const g = new GraphIndex();
  const types = ["project", "concept", "entity", "fact", "evidence"];
  const ids: string[][] = [[], [], [], [], []];
  const build = (parent: string | null, depth: number, path: string) => {
    const id = `tree/${path}`;
    g.addItem(item(id, types[depth], `node ${path}`, [`src/${path.split(".")[0]}.md`]));
    ids[depth].push(id);
    if (parent) g.addEdge(parent, id, "wikilink");
    if (depth < 4) {
      for (let i = 0; i < 3; i++) build(id, depth + 1, `${path}.${i}`);
    }
  };
  build(null, 0, "root");
  // Sparse cross-links (5% of leaves link a random non-sibling leaf).
  const leaves = ids[4];
  for (const leaf of leaves) {
    if (rng() < 0.05) g.addEdge(leaf, pick(leaves, rng), "wikilink");
  }
  return {
    name: "ontology-tree",
    source: new LocalSceneSource(g, { seed }),
    defaultFocus: "tree/root.0",
    expected: { locate: "tree/root.2.2.2.2" },
  };
}

// ── 3. dense-smallworld ─────────────────────────────────────────────

export function denseSmallWorld(seed = 42): Fixture {
  const rng = splitmix32(childSeed(seed, "dense-smallworld"));
  const g = new GraphIndex();
  const N = 300;
  const K = 4; // ring neighbours each side
  const types = ["concept", "entity", "fact", "evidence", "analysis"];
  for (let i = 0; i < N; i++) {
    g.addItem(item(`sw/n${i}`, types[i % types.length], `smallworld ${i}`, [`src/s${i % 40}.md`]));
  }
  for (let i = 0; i < N; i++) {
    for (let k = 1; k <= K; k++) {
      const j = (i + k) % N;
      // Watts–Strogatz rewiring, p = 0.1.
      const target = rng() < 0.1 ? Math.floor(rng() * N) : j;
      if (target !== i) g.addEdge(`sw/n${i}`, `sw/n${target}`, "wikilink");
    }
  }
  // Generic hubs: 4 nodes wired to ~100 others each.
  for (let h = 0; h < 4; h++) {
    const hub = `sw/hub${h}`;
    g.addItem(item(hub, "concept", `hub concept ${h}`, []));
    for (let i = 0; i < 100; i++) g.addEdge(hub, `sw/n${(h * 71 + i * 3) % N}`, "wikilink");
  }
  return {
    name: "dense-smallworld",
    source: new LocalSceneSource(g, { seed }),
    defaultFocus: "sw/n0",
    expected: { genericHub: "sw/hub0" },
  };
}

// ── 4. mixed-multiscale ─────────────────────────────────────────────

export function mixedMultiscale(seed = 42): Fixture {
  const rng = splitmix32(childSeed(seed, "mixed-multiscale"));
  const g = new GraphIndex();
  const types = ["concept", "entity", "fact", "evidence"];
  // 4 super-communities × 5 sub-communities × 20 nodes.
  for (let s = 0; s < 4; s++) {
    for (let c = 0; c < 5; c++) {
      for (let n = 0; n < 20; n++) {
        const id = `ms/s${s}c${c}n${n}`;
        g.addItem(item(id, types[n % types.length], `m${s}.${c}.${n}`, [`src/ms-${s}-${c}.md`]));
      }
    }
  }
  const nodeId = (s: number, c: number, n: number) => `ms/s${s}c${c}n${n}`;
  for (let s = 0; s < 4; s++) {
    for (let c = 0; c < 5; c++) {
      for (let n = 0; n < 20; n++) {
        // Dense within sub-community.
        for (let k = 0; k < 3; k++) {
          const m = Math.floor(rng() * 20);
          if (m !== n) g.addEdge(nodeId(s, c, n), nodeId(s, c, m), "wikilink");
        }
        // Sparse within super-community.
        if (rng() < 0.15) {
          g.addEdge(nodeId(s, c, n), nodeId(s, Math.floor(rng() * 5), Math.floor(rng() * 20)), "wikilink");
        }
        // Very sparse across super-communities (overlap membership).
        if (rng() < 0.03) {
          g.addEdge(nodeId(s, c, n), nodeId(Math.floor(rng() * 4), Math.floor(rng() * 5), Math.floor(rng() * 20)), "wikilink");
        }
      }
    }
  }
  return {
    name: "mixed-multiscale",
    source: new LocalSceneSource(g, { seed }),
    defaultFocus: "ms/s0c0n0",
    expected: {},
  };
}

export function allFixtures(seed = 42): Fixture[] {
  return [workspaceSmall(seed), ontologyTree(seed), denseSmallWorld(seed), mixedMultiscale(seed)];
}
