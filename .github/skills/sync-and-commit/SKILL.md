---
name: sync-and-commit
description: "CI/CD-style workflow skill. Use when you want to: curate design flow, sync docs/tests/code with the latest changes, run all tests to confirm everything passes, then git commit. Triggers automatically after a feature is implemented and ready to ship."
user-invocable: true
---

# Skill: Sync and Commit

## Goal

Act as a CI/CD gate before committing.  Given the current change summary, ensure documentation, tests, and source code are all consistent, all tests pass, then produce a clean git commit.

---

## Step 1 — Curate Design Flow

Invoke the `design-flow-curation` skill:

> Read the instructions from `.github/skills/design-flow-curation/SKILL.md` and execute them in full.

This step may prompt the user for confirmation before writing.  Wait for confirmation and proceed only after it is given.

---

## Step 2 — Derive the Change Summary

After the design-flow curation is done, extract the **Change summary** sentence(s) from the entries just appended to `doc/design_flow.md`.  If no new entries were appended, ask the user for a one-line change summary before continuing.

Store this as `CHANGE_SUMMARY` — it will be used for the commit message in Step 5.

---

## Step 3 — Sync Check and Gap Fill

Using `CHANGE_SUMMARY` as the reference, audit all four artifact categories below.  For each gap found, **fill it in** (do not merely report it).

### 3.1 — Documentation (`doc/`)

- Identify which design doc(s) are relevant to the change (use `doc/design_*.md` filenames and their section headings as a guide).
- Check whether the affected sections accurately reflect the new behaviour described in `CHANGE_SUMMARY`.
- If a section is missing, outdated, or contradicts the new design, update it now.
- If the change introduces a brand-new concept with no existing doc section, add one.

### 3.2 — Tests (`tests/`)

- Consult `tests/TESTING.md` (Feature-to-Test Mapping table) to identify the test file(s) that cover the changed area.
- Read those test files and verify they include test cases for:
  - The new/changed behaviour.
  - Edge cases implied by the change (regression guard).
- If a test case is missing, add it following the layering rules in `tests/TESTING.md`:
  - Pure logic → `tests/unit/`
  - Full-stack / HTTP observable → `tests/e2e/`
- Update `tests/TESTING.md` Feature-to-Test Mapping table if a new test file or row is needed.

### 3.3 — Source Code (`core/`, `web_gui/`)

- Read the relevant source files to check whether the implementation matches the updated documentation and `CHANGE_SUMMARY`.
- If there are unimplemented stubs, missing branches, or code that still reflects old behaviour, implement or correct them now.
- Do **not** refactor unrelated code.

### 3.4 — Sync Confirmation

After filling all gaps, briefly list what was updated in each category (doc / tests / code).  If a category needed no changes, say "no changes needed".

---

## Step 4 — Run All Tests

Run the full test suite using the project's virtual environment:

```
.venv\Scripts\python.exe -m pytest tests/ -v
```

- If all tests pass, proceed to Step 5.
- If any test **fails**:
  1. Read the failure output carefully.
  2. Fix the root cause (in source code or tests — whichever is wrong).
  3. Re-run the suite.
  4. Repeat until **zero failures** and **zero errors**.
- Do **not** skip or xfail a test just to make the suite green; fix the underlying problem.

---

## Step 5 — Git Commit

Stage all modified/added files and commit with a message derived from `CHANGE_SUMMARY`.

### Commit message format

```
<CHANGE_SUMMARY>
```

Rules:
- First line = `CHANGE_SUMMARY` verbatim (imperative mood preferred, ≤72 chars).
- Body lines use the bullet format above; keep each line factual and short.
- Do **not** describe implementation details beyond the file names.
- Do **not** include timestamp, author, or branch name in the message body.

Run:

```
git add -A
git commit -m "<first line>" -m "<body>"
```

Or use a temp file if the body is multi-line:

```
git commit -F <(echo -e "<full message>")
```

---

## Checklist (self-verify before finishing)

- [ ] design-flow-curation skill executed and user confirmed
- [ ] CHANGE_SUMMARY extracted
- [ ] doc/ gaps filled
- [ ] tests/ gaps filled
- [ ] core/ and web_gui/ gaps filled
- [ ] All tests pass (0 failures, 0 errors)
- [ ] Git commit made with correct message format
