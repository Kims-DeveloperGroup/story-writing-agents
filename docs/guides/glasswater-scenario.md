# The Glasswater scenario

> **Availability:** this scenario uses only implemented CLI commands. It does
> not rely on interactive chat, conversation selection, transcripts, or search.

Key terms: **core documents**—durable story rules and facts; **prompt run**—a
debate that first asks an agent to draft; **draft run**—a debate that audits
supplied text; **continuity ruling**—the user's decision on a story conflict.

This compact end-to-end example starts an original project, *The Bell Beneath
Glasswater*. Commands use explicit IDs and paths so the run is reproducible.

## Set up the project

```bash
story init ./glasswater-workspace
story session start \
  --workspace ./glasswater-workspace \
  --session glasswater_novel
```

Edit the four files under `glasswater-workspace/core_documents/` with these
facts:

- `global_outline.md`: diver Mara Seln finds a sealed memory-bearing bell in
  Lume's flooded archive; it never speaks and communicates through salt patterns
  and nearby metal vibrations.
- `part_outlines.md`: Chapter 1 finds the bell; Chapter 2 takes a rubbing to
  clockmaker Tovin Vale; a council-vessel mark links to Mara's missing brother.
- `character_sheets.md`: Mara is precise and will risk her job, not another
  diver's life; Tovin understands harbor acoustics but avoids deep water.
- `narrative_rules.md`: close third past from Mara, understated maritime mood,
  physical revelation, and a concrete discovery at each chapter end.

Set English for this session:

```bash
story directive \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  '[SYS: LANG=EN]'
```

## Draft and inspect Chapter 1

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --prompt 'Write Chapter 1. Mara finds the bell during an unauthorized flooded-archive inspection. Follow every core document and end with the first unexplained salt pattern.' \
  --debate-id glasswater_ch01_v1 \
  --output bell-beneath-glasswater/chapter-01.md

story inspect \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --debate glasswater_ch01_v1
```

For consensus, expect `status: consensus`, a final candidate, three approvals
on the final revision, and the manuscript path
`manuscript/bell-beneath-glasswater/chapter-01.md`. The durable public
blackboard is `discussion_logs/glasswater_novel/glasswater_ch01_v1.jsonl`.

## Audit a human edit

Copy the approved file, edit the copy, then submit it as a complete draft under
new provenance:

```bash
mkdir -p ./glasswater-workspace/working
cp ./glasswater-workspace/manuscript/bell-beneath-glasswater/chapter-01.md \
  ./glasswater-workspace/working/chapter-01-user-edit.md
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --draft-file ./glasswater-workspace/working/chapter-01-user-edit.md \
  --debate-id glasswater_ch01_user_edit \
  --output bell-beneath-glasswater/chapter-01-user-edit.md
```

## Resolve a deliberate contradiction

Requesting an audible warning conflicts with the bell rule. Use zero
replacement rounds to expose the ruling point, inspect it, then start a new ID:

```bash
story run --workspace ./glasswater-workspace --session glasswater_novel \
  --max-debate-rounds 0 --debate-id glasswater_ch03_conflict \
  --output bell-beneath-glasswater/chapter-03-conflict.md \
  'Write Chapter 3 with the submerged bell speaking aloud.'
story inspect --workspace ./glasswater-workspace --session glasswater_novel \
  --debate glasswater_ch03_conflict
story run --workspace ./glasswater-workspace --session glasswater_novel \
  --debate-id glasswater_ch03_ruling \
  --output bell-beneath-glasswater/chapter-03-ruling.md \
  'User ruling: the bell never speaks. Show its warning as salt and vibration.'
story session end --workspace ./glasswater-workspace --session glasswater_novel
```

Later manuscripts are not automatic context. Put durable approved facts back
into core documents and recap needed chapter events in later prompts.
