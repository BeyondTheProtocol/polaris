# What Is Missing

**Last updated:** 2026-09-22

This document describes five open problems in Polaris and where we're seeking help. Each box is a missing piece to make the system more robust, transparent, and useful.

**[Versión en español](lo-que-falta.md)**

---

## 1. Unused Local Models

**Problem:** A 5 GB local model was downloaded that no code was calling, and nobody noticed for a week.

**What we need:**
- Add `--modelos` to `tools/inventario.py`: cross-reference the output of `ollama list` with model names that appear in the code (`git grep`) and list those that nobody references.
- If `ollama` is not installed, say so and exit with error (not with empty list).
- Test that simulates `ollama list` output without actually calling ollama.

**Status:** 🔴 Open (Issue #5)

---

## 2. Overlapping or Aging Tools

**Problem:** There's a criterion for creating tools, but not for retiring them. Two pieces that do the same thing with different names. Nobody detects it.

**What we need:**
- Add `--solapes` to `tools/inventario.py`: compare the first line of the docstring of each `tools/*.py` (using `difflib`, stdlib) and list pairs above a configurable threshold.
- Add `--viejas N`: list tools whose last commit is more than N days old, with their current status (`viva`, `solo-test`, `huerfana`, `entrada`).
- Output gives pairs with their similarity and the first two lines, so a person can decide.
- Test with synthetic files: one similar pair that shows up and one different pair that doesn't.
- No new dependencies.

**Status:** 🔴 Open (Issues #2, #3)

---

## 3. Routines That Stop Running Without Notice

**Problem:** A routine that stops running doesn't notify, and there's no distinction between "there was no work" and "it's broken".

**What we need:**
- A pure function, without network or disk, in `tools/estado_rutina.py`:
  `clasificar(ultima_ejecucion, ultimo_exito, periodo_esperado, ahora)` → `al-dia` · `atrasada` · `rota` · `nunca-corrio`.
- Tests with synthetic dates for each state and for boundaries (right at the threshold).
- Fail-closed: an unreadable or absent date does not return `al-dia`.
- Docstring that explains the rationale for each threshold.

**Status:** 🔴 Open (Issue #4)

---

## 4. English Documentation

**Problem:** `docs/lo-que-falta.md` explains the five open problems of Polaris and where we're seeking help. It's only in Spanish, and many people who know about agentic systems don't read it.

**What we need:**
- `docs/what-is-missing.md`, a faithful translation (same figures and boxes) and cross-linking between the two versions.
- Someone who clones the repo can run the tests and understand each SKIP without asking. Linked from the README.

**Status:** 🔴 Open (Issues #6, #7)

---

## 5. Tests That Can't Be Run in a Fork

**Problem:** `bash tests/test_all.sh` skips several tests outside the original machine (for example `test_fuga.sh` and `test_halt.sh`: "needs to be in ~/claudecode"). Someone coming from outside doesn't know what their execution covers and what it doesn't.

**What we need:**
- A short `docs/tests.md`: how to run the battery, which tests do SKIP outside the original machine and why, and which variables (`BTP_REPO`, `BTP_STATE_DIR`) need to be set.
- Linked from the README.

**Status:** 🔴 Open (Issue #6)

---

## How to Help

1. **Choose a box** from above.
2. **Open an issue** if one doesn't already exist (or comment on the existing one).
3. **Send a PR** with the code or documentation.
4. **Include tests** that can be run without access to the original machine.

**Thanks for contributing to Polaris.** 🙏

---

*This document is a starting point. If you see something missing or superfluous, open an issue or send a PR.*