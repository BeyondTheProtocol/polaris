# 🧱 What is missing

[Español](lo-que-falta.md)

The five open problems in Polaris, in its owner's words. This is not a wish list: these are
**today's pain points**. If one is your area of expertise, a well-reasoned issue is worth more
than a PR.

The figures come from the repository itself and the control loop's log on 19 September 2026.
Items marked as done are already complete; the rest remain open.

---

## 1. 🏠 The local execution route is idle

- [x] State in the documentation that the local route is **available but not actually in use**
- [ ] Find out why it is not being called: are there no tasks of that kind, or did something
      stop invoking it?
- [x] Remove the downloaded local model that nothing was wired up to use (5 GB): it was tested
      against the existing model and produced **the same output**, so it added nothing

**Status:** `ollama` runs `qwen3:8b`, and the boundary guard's log records **122 calls**, the
last on **14 July 2026**. For comparison: `nvidia` 10,065, `claude` 8,720.

**What did change on 19 September:** the Perplexity route started serving **46 models from
several providers with a single key**, so a provider that runs out of credit now has a backup
without opening a new account. This does not solve problem 1 — the local route remains unused —
but removes the excuse that there is no alternative.

**Why it hurts:** the local route is the **only permitted destination for raw data**. If it is
idle, either no de-identification is happening or it is happening somewhere else.

**And there is a second symptom of the same problem:** another local model
(`qwen3-abliterated:8b`, 5 GB) was downloaded a week ago, and **no code knows it exists** — it
appears neither in the router nor in any tool. Installing without wiring things up leaves dead
components that nobody audits.

---

## 2. 🧰 Too many tools and agents, with no lifecycle management

- [x] Identify dead components: `tools/inventario.py --huerfanas` cross-references which
      components mention which others, the daemons and the last commit. **From 5 orphaned tools
      to 0**: one was disconnected (it was connected), and four were undocumented manual CLIs
      (they were documented)
- [x] Require every agent to declare **how often** it is expected to work (`ritmo:` in its
      definition: permanente / a-demanda / estacional / dormido — permanent / on-demand /
      seasonal / dormant), with a test that enforces this
- [ ] Detect overlaps automatically: two components doing the same thing under different names
- [ ] Define criteria for retiring components, not just creating them
- [ ] Require every new tool to declare what it replaces

**Status:** **189 tools** in `tools/`, three locally downloaded models of which **only one is
wired up**, **33 agents** in `.claude/agents/` and **68 declared daemons**. Each new problem
tends to produce a new component.

**Why it hurts:** the catalogue grows faster than anyone can keep track of it. A tool nobody
can find gets rewritten, and then there are two. The auditor watches the work units' charters,
but **nobody watches the inventory**.

**What help we are looking for:** *tool discovery* and retirement patterns for agentic systems
with hundreds of components. A registry with usage telemetry? A component budget per domain?

---

## 3. 🔌 Everything is built on Claude Code

- [ ] Make the control loop run without the Claude Code runtime
- [ ] Have an alternative execution route that has actually been tested, not just written
- [ ] Separate **system logic** from **the tool that executes it**

**Status:** `tools/borde_gateway.py` exists: an OpenAI-compatible gateway that routes through
the boundary guard. The tools **deliberately use only the standard library** (`enruta.py`
makes decisions without depending on the runtime). But the control loop, agents and hooks
belong to Claude Code.

**Why it hurts:** a quota limit or a product change would leave the system without an engine.
It has already happened once: a weekly limit interrupted a committee halfway through its work.

**What help we are looking for:** real experience porting an agentic harness to another runtime,
without rewriting it all or ending up with two diverging copies.

---

## 4. 🧠 It loses overall context between sessions

- [ ] Stop manually repeating things the system already knows
- [ ] Make what is learned in one session available in the next **without hit-or-miss recall**
- [ ] Distinguish what must always be loaded from what should only be retrieved when needed

**Status:** there are indexed memory files, always-loaded rules, local RAG and a continuity log
across sessions. Even so, some things still need explaining again.

**Why it hurts:** every repetition takes time from someone who is ill, and some corrections
get lost precisely when they are needed most.

**What help we are looking for:** how to decide **what belongs in the always-loaded context**
and what should be retrieved on demand, without bloating the prompt or leaving everything to
the retrieval system's luck.

---

## 5. ⏰ Automated routines fail and nobody notices in time

- [x] Detect and restart a **disabled** daemon instead of retrying blindly
- [x] Stop alerts from multiplying on their own: keys without counters, and no iterating over
      a string character by character
- [x] Ensure an agent launched by launchd **does not appear as “unused”**
- [x] Make **an LLM that has run out of credit** trigger an alert: the old probe's GET returned
      200 with zero credit, so nobody noticed. There is now a real call every 6 h
- [ ] Make a routine that stops running **raise an alert automatically**, rather than being
      discovered weeks later
- [ ] Distinguish “there is nothing to do” from “this has been broken for a month”
- [ ] Retry and recover, not just detect

**Status:** the debt ledger currently contains **60 findings** about freshness or daemons, and
the counters speak for themselves: an email triage agent detected as stopped **679 times**;
intermittent reindexing failures **7 times in 55 days**.

**Why it hurts:** the two most important routines are reading email every day and improving
the system once a week. If they fail silently, the system looks alive when it is not.

**What help we are looking for:** *supervision* patterns for periodic jobs on a single machine:
a heartbeat with a threshold, retries with backoff, and alerts that do not turn into noise.

> ✅ **19 September 2026, part of problem 5 fixed:** two daemons (the queue runner and the bot)
> had been down for days because of a launchd `disabled` flag — invisible in `launchctl list`,
> with an error that only said “Input/output error”. Without them, the jobs meant to resolve
> each alert never ran: the alert fired again and another job was queued, in a loop. Two bugs
> feeding this were also fixed: a standalone string being iterated **character by character**,
> creating one alert per character, and alert keys that **included the counters**, so each new
> number became a new alert. All three fixes have tests.

---

> 🙋 **How to help:** open an issue saying which numbered problem you are tackling and what
> evidence you have. A fix is even better, but the right diagnosis is already half the solution.
> Read [CONTRIBUTING.md](../CONTRIBUTING.md) first: this repository is a mirror, and a merge here
> is lost in the next regeneration.
