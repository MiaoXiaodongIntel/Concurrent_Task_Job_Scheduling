---
name: design-flow-curation
description: "Use when curating this chat session into doc/design_flow.md, appending new entries while preserving structure, updating index, and applying filtering rules. Keep only Requirement Change and Design Modification. Exclude code generation and debug details. "
user-invocable: true
---

# Skill: Design Flow Curation

## Goal

Curate useful requirement and design evolution from the current session, then append valid entries to doc/design_flow.md while preserving the existing document structure.

## Target File

- doc/design_flow.md

## What This Skill Must Do

1. Read the current content of doc/design_flow.md.
2. Preserve the existing sections and ordering:
   - Metadata
   - Index
   - Change Log Rules
   - Entry sections
3. Append new entries only when new valid information appears in the current session.
4. Update Index to include newly appended entries.
5. Keep entry numbering continuous (Entry 06, Entry 07, ...).
6. Keep each entry format identical to current rules.

## Entry Format (Mandatory)

Each entry must contain exactly these blocks in this order:

1. Change summary
2. Entry type
3. Original design -> New design
4. Why improved

Template:

## Entry NN

- Change summary: <one concise sentence>
- Entry type: <Requirement Change | Design Modification>
- Original design -> New design:
  - Original: <before>
  - New: <after>
- Why improved:
  - <reason 1>
  - <reason 2>

## Inclusion Rules (Keep)

Only keep information that belongs to one of these categories:

1. Requirement Change
   - New requirement added.
   - Requirement scope/priority changed.
   - Acceptance criteria changed.
2. Design Modification
   - Architecture/model/process decisions changed.
   - Data model/state machine/interface design updated.
   - Design terminology/refactoring at conceptual level.

## Exclusion Rules (Drop)

Do not write entries for:

1. Code generation activity
   - Creating scripts/files/functions.
   - Refactor implementation details.
2. Debug activity
   - Reproduction steps.
   - Bug root-cause details.
   - Runtime error fix details.
3. Pure operational noise
   - Terminal setup commands.
   - Environment activation commands.
   - Timestamp-only updates.

## Writing Rules

1. Keep statements objective and review-friendly.
2. Use one entry for one coherent change.
3. Avoid duplicating an existing entry with equivalent meaning.
4. If a change touches both requirement and design, choose the dominant type.
5. Do not add Timestamp lines inside entries.
6. Keep language consistent with the document (English).

## Index Update Rules

1. Ensure Index includes all entries in order.
2. Index label format:
   - Entry NN - <short title>
3. Anchor format:
   - #entry-nn
4. If entries are renamed or re-numbered, synchronize all index rows.

## Quality Checklist

Before saving:

1. Only allowed Entry type values are used.
2. No excluded content appears in any entry.
3. Entry numbering is continuous with no gaps.
4. Index count equals actual entry count.
5. Existing approved entries are unchanged unless explicitly requested.

## Quick Execution Procedure

1. Collect new session events since last document update.
2. Filter events using Inclusion Rules and Exclusion Rules.
3. **Present Change Summary List for confirmation** — list only the `Change summary` line for each candidate entry, numbered sequentially. Ask the user to confirm or provide revision feedback. Do NOT proceed further until the user responds.
4. **Revision loop** — if the user provides feedback, revise the Change summary list accordingly and re-present it. Repeat until the user explicitly confirms the list is acceptable.
5. Convert each confirmed Change summary into the full required entry template.
6. Append entries to doc/design_flow.md.
7. Regenerate Index lines for all entries.
8. Re-read and validate with Quality Checklist.

## Change Summary Confirmation Format

When presenting the candidate list for user confirmation, use this format exactly:

---
Here are the proposed change summaries extracted from this session. Please confirm or provide revision feedback:

1. <Change summary sentence>
2. <Change summary sentence>
...

Reply "confirmed" if acceptable, or describe any adjustments needed.
---
