# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

This is a Korean-language learning guide (not a software project) for becoming a Database Administrator (DBA). It contains no source code, build system, tests, or dependencies — the entire repository is Markdown content organized into a three-tier curriculum: `01-beginner` → `02-intermediate` → `03-advanced`, plus an `appendix/`. There is nothing to build, lint, or test; "development" here means writing and editing Markdown chapters.

## Content architecture

- **Progression is encoded in directory names, not just content.** `01-beginner/`, `02-intermediate/`, `03-advanced/` map to career stages (예비/신입 DBA → 실무 독립 수행 DBA → 시니어 DBA/DB 아키텍트). Each tier's `00-overview.md` states prerequisites and a checklist for graduating to the next tier — read it before adding or reordering chapters in that tier.
- **Chapters are numbered and vendor-neutral by design.** The guide deliberately avoids committing to one DBMS; every chapter (except `00-overview.md`) covers a concept once and gives commands for PostgreSQL, then MySQL, then Oracle (MSSQL where relevant) in that fixed order, calling out divergence only when the behavior differs meaningfully — not just syntax.
- **Every chapter file (except `00-overview.md`) follows the same four-section structure**, in this order:
  1. `## 핵심 개념 설명` — why the concept matters, when it comes up in practice
  2. `## 주요 명령어/문법` — commands/syntax, PostgreSQL → MySQL → Oracle
  3. `## 실습 예제` — a short scenario-driven walkthrough
  4. `## 체크리스트` — `- [ ]` checkbox list of what the reader should now be able to do
  New chapters must follow this exact section order and heading names so the guide reads consistently across tiers.
- **`00-overview.md` in each tier uses a different structure**: prerequisites / competencies gained / criteria (checklist) for moving to the next tier (the top-level advanced overview instead describes ongoing growth directions, since there is no tier after it).
- **Each tier ends with a `*-commands-cheatsheet.md`** — a single-page Markdown table summarizing that tier's commands across the three DBMSes. When adding a command to a chapter, add the corresponding row to that tier's cheatsheet.
- **`appendix/dbms-comparison-matrix.md`** is the cross-tier, cross-DBMS reference table (including cloud managed services — AWS/GCP/Azure) and **`appendix/glossary.md`** is the cross-tier term glossary. These are meant to be kept in sync with terms/commands introduced in any tier's chapters — when introducing a new term or command in a chapter, consider whether it belongs in one or both of these appendix files too.
- **`README.md`** is the table of contents and learning roadmap; every chapter file is linked from it by relative path. Adding, removing, or renaming a chapter file requires updating the corresponding row/link in `README.md`.

## Working in this repository

- All content is written in Korean; keep new content in Korean, consistent with the rest of the guide.
- Preserve the chapter filename convention: two-digit prefix + kebab-case topic (e.g., `04-user-and-privilege-management.md`). The prefix determines reading order within a tier.
- Cross-references between chapters use relative Markdown links (e.g., `03-advanced/03-disaster-recovery.md` linking to `07-cloud-managed-db-advanced.md`); keep links relative to the repository root or the linking file, matching existing usage.
