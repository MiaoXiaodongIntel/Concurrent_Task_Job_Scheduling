---
name: sync-and-commit
description: "CI/CD-style workflow skill. Use when you want to: summarize session changes, sync docs/tests/code with the latest changes, run all tests to confirm everything passes, then git commit. Triggers automatically after a feature is implemented and ready to ship."
user-invocable: true
---

# Skill: Sync and Commit

## Goal

Act as a CI/CD gate before committing.  Summarize what changed in this session, ensure documentation, tests, and source code are all consistent, all tests pass, then produce a clean git commit.

---

## Step 1 — Summarize Session Changes

Use **two complementary sources** and merge them into a single summary.  Neither source alone is sufficient.

### 1.1 — Session Conversation Review

Recall the current conversation history and extract every action taken in this session:

- Requirements or design decisions discussed.
- Files the user asked to create, modify, or delete.
- Features implemented, bugs fixed, or refactors performed.
- Any explicit decisions or trade-offs made.

This captures intent and context that may not yet be reflected in git (e.g., in-memory edits, recently saved but un-staged files, or conversational decisions with no file change yet).

### 1.2 — Git Change Detection

Run the following commands to enumerate all file-level changes visible to git:

```
git status --short
git diff --name-only HEAD
git diff --name-only --cached
```

For each changed file, read its diff to understand what was concretely added, removed, or updated:

```
git diff HEAD -- <file>
```

This captures concrete file mutations that may have happened outside the conversation (e.g., edits made in the editor without being mentioned in chat).

### 1.3 — Merge into Session Change Summary

Cross-reference both sources.  Items found in one but not the other are still included.  Produce a structured **Session Change Summary** covering:

- **Code changes** (`core/`, `web_gui/`, `lib/`, `tools/`): what logic was added or modified.
- **Documentation changes** (`doc/`): which design docs were updated and what changed.
- **Test changes** (`tests/`): which tests were added or modified.
- **Other** (config, tooling, skills, etc.): anything that doesn't fit the above.

Present this summary to the user as a brief bulleted list.  If both sources return nothing at all, ask the user to describe what was done in this session before continuing.

---

## Step 2 — Derive the Change Summary

From the Session Change Summary produced in Step 1, distill a single imperative sentence (≤72 chars) that describes the most significant change.

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
- The commit message is **exactly one line**: `CHANGE_SUMMARY` verbatim (imperative mood preferred, ≤72 chars).
- **No body**. Do not add bullet points, file lists, or any additional lines.
- Do **not** include timestamp, author, or branch name.

Run:

```
git add -A
git commit -m "<CHANGE_SUMMARY>"
```

---

## Checklist (self-verify before finishing)

- [ ] Session changes detected via git and summarized (code / doc / tests)
- [ ] CHANGE_SUMMARY distilled (imperative, ≤72 chars)
- [ ] doc/ gaps filled
- [ ] tests/ gaps filled
- [ ] core/ and web_gui/ gaps filled
- [ ] All tests pass (0 failures, 0 errors)
- [ ] Git commit made with correct message format
