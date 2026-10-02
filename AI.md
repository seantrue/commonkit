# AI Contributions — commonkit

*About AI slop.* Fair enough — most of this package was written by a machine,
and so was most of this file. Call it instant ramen: fast, cheap, genuinely
edible, and nobody should pretend it simmered on a stove all afternoon. The
honest answer is not a claim about quality, it is the record below — what the
maintainer specified, what the model wrote, what the model got wrong, and which
of those mistakes were caught by the test suite, by mutation probes, or by
cross-process benchmarks rather than by luck. Several of the invariants this
package enforces exist *because* of a defect written down here. Where AI-written
code shipped one, it is recorded in the same detail as the wins.
Bon appétit, but mind the sodium content.

The original implementation was pixel centric, but the framework supports any
numpy array.

Tracks what AI helpers contributed to commonkit versus the human maintainer.

## Human maintainer

The architecture and the load-bearing decisions are the maintainer's, and they
predate this package — commonkit is an extraction of code that ran in production
inside a non-public application first.

- **The shape of the problem.** An ingest stage, then branching flows whose path
  depends on analysis done in flight, with digests as the key to either finding
  pixels already in memory or loading them into it. Offered as a correction to
  the impoverished linear flows the AI had been benchmarking against.
- **The read-only mechanism** (`a.flags.writeable = False`), and the requirement
  that the store survive back-pressure rather than reading a whole stream into
  memory behind a jam. That requirement is why `_admit` declines instead of
  blocking, and why a decline is normal operation rather than a fault.
- **The storage format, indirectly but decisively.** The observation that what
  is actually wanted on disk is the raw `.shape`, `.dtype` and `.data` triple is
  what sent the AI to check whether `np.save` does more than that. It does not
  materially — a 128-byte header, payload 64-byte aligned, 0.93 ms against
  0.89 ms for a bare buffer write — and that settled `.npy`.
- **The name.** *"Extract shm into a package 'commonkit' named for Fortran
  common."* A Fortran `COMMON` block is a named region several separately
  compiled programs agree to share, which is this contract exactly.
- Direction, review and acceptance throughout, including several corrections
  recorded below.

## Contributions

### 2026-10-02 — `shared_store(attach=...)`: how a caller reads is its own choice (Claude Opus 5.5, Claude Code)

**What the maintainer asked for.** "Do the follow ups". One follow-up was moving alexandria onto the shared store, and alexandria picks `mmap` or `read` attach per process (`PIXEL_SHM_ATTACH`).

**What the AI did.** It added an `attach` parameter to `shared_store()` (default `mmap`; ~6 LOC) and a test (~5 LOC). How a process reads affects only that process, so `attach` sits with `mode` and `min_array_bytes` as a per-caller choice, not tree-wide policy. 66 tests pass.

### 2026-10-02 — `shared_store()`: one store per host, with tree-wide policy from the environment (Claude Opus 5.5, Claude Code)

**What the maintainer asked for.** While adopting commonkit as Virtual Impressionist's checkpoint store, the maintainer pointed out that "all instances of the commonkit cache are supposed to share a common backing store directory with age out" and asked whether separate stores were costing memory. Offered two options, the maintainer chose to define the shared defaults in commonkit rather than reuse alexandria's `PIXEL_SHM_*` settings.

**What the AI did.** The AI added `src/commonkit/shared.py` (~85 LOC), exported `shared_store` from `__init__`, and wrote `tests/test_shared.py` (~64 LOC, 5 tests). It also updated `README.md` and `CLAUDE.md`.

- **Tree-wide policy comes only from the environment.** The root, byte budget, TTL, free-space floor, reap interval and largest array come from `COMMONKIT_STORE_*` variables or `DEFAULTS`, never from a caller. The reason is the existing reaper design: whichever process reaps applies its own values to every segment in the tree, so a per-caller override would silently win over everyone else's.
- **Callers choose only what concerns their own publishing:** `mode` and `min_array_bytes`. A `root` argument exists for tests.
- **Defaults are alexandria's measured macOS values:** `~/Library/Caches/commonkit/store`, a 2 GB budget (because Time Machine local snapshots pin reaped segments), 900 s TTL, and an 8 GB free-space floor. On Linux the default root is `/dev/shm/commonkit` with 8 GB.
- **Bad environment values fall back to the defaults** rather than raising, consistent with the never-raise invariant.

**Answer to the memory question, as measured and read.** RAM is not lost: segments are clean, file-backed, read-only mappings, held once in the unified buffer cache and not charged to `phys_footprint` (alexandria's measurement). Disk was the cost: two trees meant two budgets, and on macOS each tree's turnover pins snapshot space. The consumer's separate 8 GB tree was a mistake by the AI, corrected by this change before anything was written to it.

**Testing.** The full suite passes (65 tests, 5 new). ruff's six findings are all in pre-existing files (`cache.py`, `keys.py`, `read.py`).

**Process slip.** The first commit of this change went out without this entry, because an edit to `AI.md` failed and the commit ran anyway. The AI caught it before merging, and the entry was added as a follow-up commit on the same PR.

**Not done.** alexandria still configures its own tree through `PIXEL_SHM_*`. Moving it to `shared_store()` is a separate change in that repository.

### 2026-09-18 — CLAUDE.md, clearer dtype tests, sox and ffmpeg end-to-end tests (Claude Opus 5, Claude Code)

**What the maintainer asked for, and the correction that shaped it.** After a
CLAUDE.md was generated (`/init`, ~95 LOC), the maintainer asked what dtype
`wav16k` was "vs the form", then pointed at `tests/test_store.py` where
`wav16k` looked like a dtype, then asked *"where is the mapping for f32chw ->
fp32?"* There was none. The pre-existing suite, written by an AI during the
extraction, used `f32chw` -- a name that says float32 -- as its example of an
*unconstrained* form. That is correct code aimed at a misleading target, and
the maintainer caught it by reading the tests, not any test run. Asked to "make
the tests and configuration clear about what they are testing": `f32chw` now
declares float32, a new `raw` form is the explicitly unconstrained one
(parametrised over four dtypes), a new test shows each form is held to its own
entry, and comments state that `dtypes` maps FORM -> dtype and that a form's
name enforces nothing (~28 LOC changed across `test_store.py`, `test_keys.py`).
A mutation probe -- the store's dtype lookup forced to `None` -- failed exactly
the two dtype-policy tests.

**Real media, end to end, at the maintainer's request.** *"Add tests that will
run if sox is installed, dump sample audio to raw using sox, and then map it
in"*, then, after asking whether video had an equivalent, *"yes, add the ffmpeg
tests"*. `tests/test_sox.py` (~116 LOC, 4 tests) and `tests/test_ffmpeg.py`
(~142 LOC, 5 tests) render from each tool's built-in generator (`synth`,
`lavfi testsrc`), so no media is checked in and the digest names the source
while rate and encoding become variants and forms. Each publishes, attaches as
a read-only `np.memmap`, attaches again from a child process by key alone,
stores two rates side by side, and refuses a float dump under the integer form.
The video file also pins that an over-ceiling clip is declined whole. Both skip
when their tool is off `PATH`, checked by running them with a stripped `PATH`.
Per-frame chunking was deliberately left out: the key has no frame-index field,
and where one goes is a design decision, not a test's. README and CLAUDE.md
gained a short section on these tests (~23 LOC in README).

**Defects in this AI-written change.** One: the sox scheme was first written as
`dict(...)`, which ruff's C408 flagged; rewritten as a literal. Suite: 60
passed with both tools installed.

### 2026-09-18 — Extracted into its own package (Claude Opus 5, Claude Code)

**What moved, and what that means for attribution.** Most of the source is not
new. `store.py` (442 LOC) moved essentially verbatim — it was already numpy-only
— and `cache.py` (118) is a class lifted out of a module whose application-coupled
half stayed behind. `keys.py` (207) is the one substantially rewritten file.
`read.py` (49) is the counter vocabulary, reduced to the genuinely shared part.
Tests are new: 448 LOC across three files. Measured, not estimated.

**The generalisation that made the claim honest.** The key was
`pixels:v3:{form}:{digest}:o{token}`, with orientation welded into the regex. It
is now `{domain}:{version}:{form}:{digest}:{variant}`, built by a `KeyScheme` a
domain instantiates — images bind `o0`/`o1`/`oa`, a waveform store would bind
`sr16000`. Schemes register by domain so `parse()` and `relpath()` dispatch on a
key alone, which is what lets one store hold several domains without growing a
parameter for something the key already says. Keys and paths are **byte-identical**
to before, because the consuming application builds the same string
independently on its other code path and a disagreement between the two is the
worst failure this design allows: no exception, no counter, just a cache that
quietly never hits again.

**A real fix the move forced.** `_admit_dtype` contained `if form != "pil":
return True` — a pixels policy sitting inside the supposedly generic store,
which would have shipped as genericity in name only. The dtype rule is now
*declared* by the scheme and merely enforced by the store, so a float32 tensor
or an int16 waveform is unconstrained without the store knowing anything about
images.

**Defects in this AI-written change, and how each was caught.** An edit script
buffered its edits and wrote only at the end, so for a file edited twice the
second replacement clobbered the first; two independent gates caught it before
anything was committed. A later script aborted mid-way on an anchor whose
indentation was wrong — the assert-before-write guard turned that into a clean
abort rather than a half-copied file. Two of the new tests were wrong rather
than the code: domain names containing underscores are illegal under the
scheme's own `[a-z0-9]+` rule, and an upper-case digest is *folded*, not
refused — deliberate, because the consumer's lenient key builder does not fold
case, so that test became a positive pin instead. Verifying a PNG-embeds-ICC
claim by searching the saved bytes was unsound, since PNG stores the profile in
a zlib-compressed `iCCP` chunk; the conclusion held but the method did not, and
it was replaced with a round-trip.

**Method.** The equivalence proof is the consuming application's own shm suite,
run **unmodified** after the move: 141 tests. Not touching them is what makes
them evidence. commonkit's own 47 tests drive the generic layer through a
deliberately **non-pixels** scheme (audio, variant axis sample rate), because a
suite that only ever addressed images would not demonstrate the genericity being
claimed; one test pins the pixels spelling byte-for-byte. Five mutation probes
against the parts rewritten rather than moved — the scheme's declared dtype, the
store's enforcement of it, the registry's refusal of two disagreeing schemes,
the two-level path sharding, the case fold — each failed exactly the pin meant
to catch it.

### Measured after the extraction (same day)

Re-ran the pre-existing cross-process harnesses rather than writing a new one,
so the numbers stay comparable to the runs that predate the move. Dataset: a
pinned 48-image set, identical on both hosts.

* **Memory sharing is the robust, platform-independent result.** On Linux,
  121.50 MB Pss for 4 consumers against 509.0 MB unshared — 4.2×. On Apple
  Silicon, 11.50 MB of footprint for 4 attachers against 570.77 MB for 4 private
  decoders, and 23.15 MB against 1160.32 MB at 8. All mappings resolve to 48 VM
  objects, resident once.
* **Decode-once holds:** 192 (and 384) attaches, 0 decodes.
* **The time win is modest, and one reported figure was wrong.** A first Linux
  run showed 17.97×; that was an artifact. The harness runs its control first
  with no warm-up, so it paid cold page cache for 127 MB of source images.
  Three successive runs settle at control 7.01 s → 1.01 s → 0.94 s and the
  speedup at **~2.6×**. The inflated number was retracted rather than quoted.
* **No wall-time win at all on Apple Silicon**, and one-shot *including* the
  publisher is slower: 1.72 s against 0.92 s. The store pays for re-reads, not
  for a single pass. Said plainly rather than letting the table imply otherwise.

## Inherited record: defects from before the extraction

These were found while this code ran inside the application it came from. They
are kept because several are the reason a line in the shipped source looks the
way it does — deleting them would leave the invariants looking arbitrary.

- **The writability probe used a fixed filename.** Concurrent constructors
  raced: the first unlink won, every loser's `FileNotFoundError` was caught as
  "unusable root", and that process ran permanently without a cache and without
  a log line. Measured at 8 of 16 disabled. It is why the probe name carries a
  pid and thread id, and why `test_concurrent_construction_never_disables_a_store`
  exists.
- **The two-tier cache double-counted.** `TieredPixelCache` ticked `shm_hit` and
  `shm_publish` itself while the store it delegates to counted the same events,
  and `drain_stats()` sums the two — inflating exactly the hit rate the counters
  exist to measure. The tier now leaves those to the store.
- **A hit rate was inferred from counter deltas and was wrong for two days.** A
  caller classified by reading `shm_hit` off the tier, which deliberately does
  not tick it, so every cross-process attach was reported as a decode; a correct
  production measurement was retracted on the strength of it. That is why
  `get_with_source` returns which tier answered instead of leaving it to be
  inferred, and why `read.py` owns the counter names.
- **L1 did not earn the argument first made for it.** The claim was that two
  tiers were settled by a loop that looks the same digest up 36 times; the
  premise was true and the conclusion did not follow, because an attach is only
  ~3 ms. At `max_items=64` L1 absorbed every lookup and was still the slowest
  configuration measured, and the default dropped. L1 removes syscalls; it is
  not the reason the store is worth running.
- **Three harness bugs preceded any trustworthy number**, each giving a
  plausible wrong answer: forked children inheriting the parent's DB sockets
  (every lookup raised, the exception was swallowed, and the run reported zero
  decodes *and* zero hits); Pss reading 0 because only a `[:1]` slice was
  retained, so the pages never faulted in; and an unsynchronised measurement
  reporting 2.0× for a 4-way share because each consumer sampled whenever it
  happened to finish.
- **A maintainer's question exposed what the harness could not see.** Asked
  whether the processes had all seen an empty cache and decoded in parallel, the
  specific diagnosis turned out not to be what happened — but the question
  revealed the harness pre-published before forking and so could not observe a
  thundering herd at all. Adding a cold run showed the herd is nearly total.
- **On Apple Silicon, three instruments were wrong before any number was
  reported**: a Mach task port cached at import so forked children saw no
  regions; footprint in forked children dominated by copy-on-write noise, which
  forced spawned children; and a Spotlight query that "found" files a global
  index never returned.
