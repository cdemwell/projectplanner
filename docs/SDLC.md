# Lightweight, AI-Orchestrated SDLC

> A coherent plan for how software gets built in this repository: a hub-and-spoke
> lifecycle in which a large **orchestrator** model plans and gates the work while
> smaller **subagent** models do the concrete implementation in isolation.
>
> This document is the human-reviewed synthesis of Epic 13 ("Lightweight SDLC:
> AI-Orchestrated Development") and its twelve stories. It is the plan the
> implementation follows. Section headers cite the planner story (e.g. **S55**)
> they are drawn from so the plan stays traceable to the tracker.

---

## 1. What this is

A **lightweight software-development lifecycle** — "lightweight" because it is a
small, pragmatic pipeline, not a heavyweight enterprise process. A single large
model (the **orchestrator**, playing the role of tech lead) manages a pipeline of
smaller models (the **subagents**) that do the concrete implementation work. The
orchestrator decomposes high-level requests into atomic, independently-buildable
units of work, dispatches each to an ephemeral subagent, reviews the result
against hard quality gates, and merges only known-good work into the main line.
(**Ep 13**)

### Why hub-and-spoke rather than one big agent

- **Reduced hallucination + token cost.** Each subagent receives only the files
  and instructions needed for its single task, not the whole codebase.
- **Parallelism.** Independent stories proceed simultaneously in isolated
  worktrees without colliding.
- **A checkpoint where quality is actually enforced.** A dedicated review step
  between "develop" and "merge" catches what a lone agent would silently ship.
- **Accountability.** Every unit of work is owned by exactly one subagent, and
  every decision is recorded as an auditable artifact/comment.

The orchestrator is deliberately kept out of the implementation hot path: it
holds the source of truth and makes the quality-gating decisions, but does not
write the bulk of the code itself. (**Ep 13**, **St 63**)

---

## 2. Roles

| Role | Size | Responsibility | Scope of action |
|------|------|----------------|-----------------|
| **Orchestrator (tech lead)** | large model | Plan, decompose, dispatch, review, merge, retrospect | Everything except writing the bulk of the code |
| **Subagent (worker)** | smaller model | Implement exactly one story | Its own story, in its own isolated worktree |
| **QA/test subagent** | smaller model | Enforce TDD; write & run the test suite | Tests / verification (**St 60**) |
| **Documentation subagent** | smaller model | Update README, CONTEXT.md, changelog, ADRs | Docs (**St 61**) |
| **Adversarial reviewer** | separate model/persona | Try to break the change; find spec deviations | Review gate (**St 58**) |
| **Human (user)** | — | Strategic veto; high-risk sign-off | Impact map, strategy, high-risk merges (**St 66**) |

"Agents execute strategy; they do not determine it." (**St 66**)

---

## 3. The eight-phase pipeline

Each phase consumes the previous phase's artifact and produces its own. The loop
closes when Phase 8 feeds back into Phase 1. Workflow-state transitions gate each
handoff so a story cannot advance until its criteria are met. (**St 63**, **St 58**)

```
  Phase 1  →  Phase 2  →  Phase 3  →  Phase 4  →  Phase 5  →  Phase 6  →  Phase 7
  Plan        Assign      Implement    Review      Merge       Verify      Ship
     ↑                                                                      │
     └──────────────────── Phase 8 Retrospective ──────────────────────────┘
```

### Phase 1 — Planning & Story Creation (**St 55**)
The orchestrator scans the codebase (`CLAUDE.md`, `CONTEXT.md`, ADRs, existing
patterns) and builds/refreshes a **project-context map**. It decomposes a
high-level request into **atomic user stories** — each a self-contained unit of
work with a clear **definition of done** that a subagent can execute with minimal
context.

In the planner, each story is created under Epic 13 with:
- a concrete name;
- a 2–3 sentence description;
- **3–5 scoped tasks**;
- an **acceptance-criteria comment**.

Dependencies between stories are recorded as **story links** (`blocks` /
`relates_to`) and ordered via `position`.

**Shared acceptance bar:** every story in an epic carries the same concrete,
reviewable bar (keyboard-first, reuse backend functions, confirm destructive
actions, surface errors, headless pilot test). Reuse it verbatim across epics —
it is what the review gate actually checks against.

**Schema ambiguities:** during decomposition, surface any data-model ambiguity
(e.g. a relationship that isn't a direct foreign key) as an explicit question or
a recorded decision in the epic description — don't let it surface
mid-implementation.

**Artifact produced:** the refined backlog of stories with DoD + acceptance
criteria.

### Phase 2 — Story Assignment & Dispatch (**St 56**)
The orchestrator dispatches each ready story to a subagent, choosing the right
subagent size/type for the story's complexity and preparing a **minimal context
window** (only the relevant files, interfaces, and acceptance criteria).

**Ownership contract:** the assignment (`story → subagent → worktree/branch`) is
recorded in the dispatcher log *before* implementation begins. No story is
double-assigned; every dispatched story is owned by exactly one subagent at a
time.

**Artifact produced:** the dispatcher log (assignment + ownership record).

### Phase 3 — Implementation in Worktrees (**St 57**)
Each subagent implements its story in an **isolated git worktree** (or branch),
so many subagents work in parallel without colliding. The subagent writes focused
code against its story's acceptance criteria and leaves the rest of the codebase
alone. Before reporting back it **self-checks the diff** for scope, style, and
unrelated changes, then reports:
- the worktree/branch name, and
- any **assumptions or deviations** from the acceptance criteria.

**Worktree discipline (hard rule):** the subagent edits *only* inside its
worktree — never the main checkout. It stages *only* its own source files
(`git add <file>…`), never `git add -A`. The repo's `.gitignore` must already
exclude worktree directories so a stray `git add -A` can't sweep them in.

**Artifact produced:** a scoped diff/branch, plus an honest self-report.

### Phase 4 — Orchestrator Review (**St 58**)
The orchestrator (tech lead) reviews each subagent's branch in its isolated
worktree **before anything is merged**. This is a hard quality gate:

1. **Acceptance-criteria diff** — the change is checked against the story's
   acceptance criteria, *not just its task list*; every criterion must be
   demonstrably met.
2. **Quality rubrics, exactly as CI would run them:**
   `ruff check backend cli tui main.py tests` and `pytest -q`. A branch that
   fails lint or tests is rejected outright.
3. **Adversarial pass** — probe edge cases, look for spec deviations, dead code,
   and half-implemented paths; *try to break the change*.
4. **Trust-but-verify** — do not trust the subagent's self-report; independently
   run the rubrics and read the diff. If a fix is reported "not fixed," reconsider
   the approach rather than re-applying the same change.
5. **Context-guard check** — every action that assumes a specific entity kind must
   no-op/bell when the app is in a different mode (a story-only action invoked
   while browsing another entity must not crash or edit the wrong row).
6. **State-scoping check** — transient state (search, selection, filters) must be
   scoped to the navigation context and cleared on switch/drill/zoom (a filter
   set in one context must not leak onto another entity's list).
7. **Framework-collision check** — method names must not shadow framework
   internals (a helper named after a framework method can silently override it
   and break the app).

Every finding is recorded as a threaded comment on the story (with the subagent as
author) so the fix-list is auditable, then sent back. The story **loops back to
implementation** until it passes with **zero unaddressed findings**. Only a branch
that clears this gate is approved for merge.

**Gate rule (quality-gate consistency):** a story cannot move from `develop` to
`verify` until the defined criteria are met. (**Ep 13**, **St 58**)

### Phase 5 — Merge & Integration (**St 59**)
Approved branches are integrated into the main line in **small, reviewed
increments**, in dependency order, so the main line is never left in a broken
intermediate state:
- **Rebase** the branch onto the latest main before merging; resolve conflicts,
  using LLM-assisted resolution for non-trivial ones.
- **Verify the branch base** before merging a parallel branch: `git merge-base
  <branch> main` must be the intended parent. If a branch was cut from an older
  base, rebase it onto the latest main *before* review, not after.
- **Sequence shared-file work** — stories that edit the same file/function are
  not truly parallel; merge them in dependency order and expect interleaved
  conflicts to resolve by hand.
- Re-run the quality rubrics on the **integrated main line** after each merge
  before merging the next branch.
- **Record each merge** (source branch → main) as a comment so the integration
  history is auditable.
- **Update cross-story links** (`blocks` / `relates_to` / `duplicates`) so the
  integrated result reflects the real relationships between stories.

The main branch is kept in a **known-good state at all times**.

**Artifact produced:** an auditable merge history on each story.

### Phase 6 — Testing & Verification (**St 60**)
Automated tests run across the **integrated main line** (the whole system, not
just individual branches):
- Enforce the **TDD contract**: tests are written first; the implementation must
  pass them. A dedicated **QA/test subagent** owns this enforcement.
- Run the full suite on the integrated main line.
- Measure **coverage** against the agreed bar; add tests for gaps.
- **Investigate and fix every failure and flaky test** — never ship with known
  failures.
- Produce a **verification report** (pass/fail, coverage, flakiness) as a comment
  on the story; it **gates the release**.

**Artifact produced:** the verification report.

### Phase 7 — Deployment & Documentation (**St 61**)
Known-good, verified work ships to the target environment and docs are updated to
match:
- Orchestrator **tags a release**, runs the deploy step, confirms the running app
  reflects the merged main line.
- A **documentation subagent** updates README, `CONTEXT.md`, the changelog, and
  ADRs so the codebase stays self-explanatory for the next cycle.

**Artifacts produced:** the release artifact + the doc delta (referenced by the
Phase 8 retrospective).

### Phase 8 — Retrospective & Learning (**St 62**)
The orchestrator closes the loop by reflecting on the whole cycle: what worked,
what didn't, and which review findings recurred. It mines the review comments,
fix-lists, **and user rejections/feedback** from Phases 4–6 (e.g. "the example
doesn't cover all options"), then writes concrete, actionable lessons back into
project context (`CLAUDE.md`, a "gotchas"/knowledge base) so the next cycle
repeats successes and avoids past mistakes. It also updates the epic's quality
rubrics and planning guidance, feeding directly into **Phase 1 of the next
cycle** — the self-improving loop.

---

## 4. Cross-cutting controls

Three controls weave through the whole pipeline rather than living in one phase.

### 4.1 Shift-left security (DevSecOps) — **St 64**

Security is not a final audit; it is a first-class quality gate.

- **Planning (Phase 1):** use **STRIDE** threat modeling during story
  refinement; turn each real threat into an **abuse case**; derive security
  acceptance criteria for the story.
- **Review gate (Phase 4):** automated tooling a branch must pass:
  - **SAST** — Bandit (Med/High findings fail the gate);
  - **SCA** — pip-audit (dependency vulnerability scan, must be clean);
  - **Secret scanning** — detect-secrets (must be clean);
  - **DAST** — dynamic analysis, kept because this project builds software that
    must carry consistent security properties even though the tool itself is a
    local CLI.
- **Merge checklist (before any merge, Phase 5):** Bandit clean; pip-audit
  clean; detect-secrets clean; no requirements drift; abuse cases reviewed;
  security ACs met and tested; every accepted risk owned.

**Grounding:** OWASP DevSecOps Guideline; Bandit, pip-audit, detect-secrets docs.

### 4.2 Characterization & golden-fixture tests — **St 65**

Before any refactor, **lock current behavior** so LLM-led changes cannot silently
regress behavior:

- **Characterization tests** use Michael Feathers' **sensing loop** (wrong
  assertion → observe actual → correct the assertion) to capture what the code
  does *today*.
- **Golden-fixture pattern:** "approve on first run, assert on subsequent runs."
  The first run writes the current output as a committed baseline; later runs
  assert the implementation matches it.
- **Tooling:** standardize on **Syrupy** (best per-field sanitization of
  non-deterministic outputs).
- **Process:** never update golden fixtures in CI; review fixture diffs via
  `git diff`; commit atomically (code + its fixture in one commit).

**Grounding:** Michael Feathers, *Working Effectively with Legacy Code*; Syrupy
docs.

### 4.3 Human-in-the-loop guidance — **St 66**

Strategic control stays with humans. Three levels (established AI-oversight
vocabulary):

| Level | Role | In this SDLC |
|-------|------|--------------|
| **HITL** Human-in-the-loop | human is a *gate*; work pauses until the human acts | impact-map approval; high-risk merge sign-off |
| **HOTL** Human-on-the-loop | human is a *switch*; can intervene/override while work proceeds | reviewing a diff before merge |
| **HIC** Human-in-command | human is the *architect*; final authority over goals/strategy | strategic veto on product/architecture |

**Three hard gates:**
1. **Impact map** — a mandatory HITL checkpoint *before* coding begins.
2. **Strategy / architecture** — an HIC veto (humans decide direction).
3. **High-risk merges** — HITL sign-off backed by captured evidence.

**Approval mechanism:** HITL approval is represented by **explicitly assigning
the story to the human user with a comment asking for review** — separating
agent-owned implementation from human-owned strategic approval. Stories are also
assignable to a dedicated **`agent` user**, so agent-work and human-work are
unambiguously distinguished in the planner.

**Grounding:** NIST AI Risk Management Framework; EU AI Act Art. 14; OECD AI
Principles.

### 4.4 Commit & communication hygiene

Small rules that keep the loop fast and the history clean.

- **Sole authorship** — the human is the sole author; no co-author trailers on
  commits.
- **Read feedback before redoing** — read the user's comments on a ticket before
  redoing the work.
- **Status vs action** — when asked for status, report; don't start new work.
- **Research before claiming** — verify against external sources before asserting
  completeness.
- **Prefer the canonical flag** — use the current flag over deprecated aliases.

---

## 5. Design options & configuration

The pipeline above is the **recommended default**. Each of these is an explicit,
tunable choice — decide per project or per story.

| Dimension | Option A (default) | Option B | Notes |
|-----------|--------------------|----------|-------|
| **Orchestration** | hub-and-spoke (one orchestrator, many workers) | flat (single agent does everything) | Hub-and-spoke chosen for cost, isolation, gated review. |
| **Isolation** | git worktrees (shared object DB, separate files/index/branch) | plain branches | Worktrees let parallel agents avoid file collisions; must still isolate ports/DBs per branch. |
| **Human level** | HITL for impact map + high-risk; HOTL for diffs; HIC for strategy | More autonomy (HOTL everywhere) | Tunable per risk tier; default reserves strategy + merges for humans. |
| **Concurrency cap** | ~3–5 concurrent subagents | Higher/lower | Bounded by review bottleneck + token cost. Parallelize only stories that touch disjoint files; sequence stories that edit the same file/function. |
| **Security gate** | SAST + SCA + secret-scan + DAST | SAST + SCA only | DAST kept for build-anything consistency (see 4.1). |
| **Testing bar** | TDD + characterization + golden fixtures (Syrupy) | TDD only | Characterization/golden fixtures added when refactor risk is high. |
| **Lint/formatter** | ruff | — | Used as the hard CI lint gate (Phase 4). |
| **Test runner** | pytest | — | Used as the CI test gate (Phases 4 & 6). |
| **Quality gate rule** | story blocked `develop→verify` until criteria met | — | Enforced at Phase 4. |
| **Persistent-state safety** | isolated/temp DBs for tests & demos; export/import for reconciliation | — | Never `rm`/drop the real database or other persistent state. |
| **Commit authorship** | human sole author, no co-author trailer | — | Enforced at Phase 5 merge. |

### Cost & scaling levers
- **Cap concurrent subagents** to bound token spend and review serialization.
- **Scope subagent context tightly** (minimal files/interfaces only) to cut cost.
- **Batch reviews** to reduce the single-orchestrator bottleneck.
- **Budget for long sessions** — a long multi-epic cycle burns session compute
  over time; plan for capacity to run out near the end of a long run.

---

## 6. Known pitfalls & mitigations — **Ep 13**

| Pitfall | Mitigation |
|---------|------------|
| **Token cost** (~15x single-model) | Scope subagent context; cap concurrency. |
| **Review bottleneck** (one reviewer serializes) | Cap concurrent subagents; batch reviews. |
| **Cascading hallucinations** (wrong assumption propagates) | Maker-checker: independent review persona per branch. |
| **Runtime clash** (agents fight over ports/DBs, or edit the main checkout instead of their worktree) | Worktrees + per-branch DB/port allocation + explicit "edit only in the worktree, stage only your files" instruction. |
| **Shared-file merge conflicts** (parallel stories edit the same file/function → interleaved conflicts on every merge) | Record "shared-file coupling" as a dependency; sequence tightly-coupled stories; parallelize only genuinely disjoint work. |
| **Policy drift** (runs drift from intent) | Distill retrospectives back into `CLAUDE.md`/context each iteration. |

### 6.1 Operational gotchas (observed in practice)

These are concrete failure modes hit while executing this SDLC, generalized so
they apply to any codebase, not just this one.

| Pitfall (observed) | Mitigation |
|---|---|
| **Wiping persistent state** (`rm`/drop the real database) | Never `rm`/drop the real database or other persistent state. Tests use isolated/temp instances; demos use throwaway instances; reconcile via export/import or migrations, never by wiping. |
| **A subagent corrupts a file** (duplicate defs, fragmentary lines) | Review the diff and run lint + tests before merge; never trust the self-report. |
| **A subagent edits the main checkout instead of its worktree** | Instruct subagents to edit only in the worktree; verify the diff is on the branch, not the main tree. |
| **`git add -A` sweeps in worktree dirs / untracked files** | Gitignore worktree directories; stage only the story's source files. |
| **Framework/library API assumptions** (assumed messages/attributes/behaviors that don't exist) | Verify the API before using it; prefer the documented, tested path. |
| **Type coercion** (CLI/user input arrives as strings) | Coerce explicitly to the expected type. |
| **Shell portability** (macOS vs GNU tools, zsh vs bash word-splitting) | Use portable constructs; avoid platform-specific flags. |
| **Auto-fixers remove needed code** (linter `--fix` drops imports) | Review the auto-fix diff before committing. |
| **Reserved words in the query language** | Quote identifiers. |
| **A subcommand default clobbers a top-level flag** | Use `SUPPRESS` (or the framework equivalent) on inherited flags. |

---

## 7. Grounding sources

- Anthropic, **Building Effective Agents** — orchestrator-workers, evaluator-optimizer, parallelization, quality gates.
- Anthropic, **Multi-agent research system** — hub-and-spoke with isolated subagents.
- Anthropic, **How Anthropic secures its AI-native SDLC** — adversarial review, risk-tiered gates, self-improvement loop.
- Tim Schipper — **git worktrees for parallel coding agents** (isolation primitives).
- arXiv **SAGE / EvolveR** — retrospective reflection / experience memory.
- OWASP **DevSecOps Guideline**; **Bandit**, **pip-audit**, **detect-secrets**.
- Michael Feathers, *Working Effectively with Legacy Code*; **Syrupy** docs.
- **NIST AI RMF**; **EU AI Act Art. 14**; **OECD AI Principles**.
- **RFC 5545** (recurrence model in the plan).

---

## 8. Mapping to the tracker

| Document section | Epic / Story |
|------------------|--------------|
| §2 Roles | **Ep 13**, **St 63** (meta pattern), **St 60/61** (QA, docs), **St 58** (adversarial) |
| §3 Phases | **St 55–62** (Phases 1–8) |
| §4.1 Security | **St 64** |
| §4.2 Characterization | **St 65** |
| §4.3 Human-in-the-loop | **St 66** |
| §5 Options | **Ep 13** (mechanisms), **St 58/60** (gates) |
| §6 Pitfalls | **Ep 13** |
| Recurrence (plan-wide) | **St 27** (Ep 9) |

*This file is a planning document; the authoritative tracker of record is
`planner.db`. Keep it in sync as the SDLC evolves.*
