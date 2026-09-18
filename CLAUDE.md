# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The build backend is `uv_build`; dev tools are in the `dev` extra.

```bash
uv run --extra dev pytest -q                                   # full suite (<1s)
uv run --extra dev pytest tests/test_store.py::test_publish_then_attach_returns_the_same_samples
uv run --extra dev ruff check .                                # line-length 100
uv build                                                       # wheel + sdist
```

`requires-python = ">=3.11"` is deliberate: the consumers are python-3.11 model
venvs. Don't use 3.12+ features, and don't add dependencies beyond numpy.

## What this is

Named shared-memory segments for numpy arrays: one process decodes and
publishes a `.npy` file under a root directory (typically `/dev/shm`), other
processes `np.load(mmap_mode="r")` the same physical pages. Extracted from a
private image-corpus app; the module docstrings carry the production
measurements that justify each design choice — read them before changing
behavior.

## The invariant

**A missing segment is never an error, only a slower path.** Nothing on the
read/publish path may raise into a caller: absent root, full disk, reaped or
torn segment, bad key, wrong dtype all return `None`/`False` and tick a
counter in `self.stats`. The `SharedArrayStore` constructor never raises — an
unusable root sets `disabled` and every method becomes a no-op. The one
exception is `KeyScheme.key()`/`parse()`, which raise `ValueError` loudly at
the call site; the store catches that and counts `shm_bad_key`.

## Architecture (src/commonkit)

- `keys.py` — `KeyScheme` defines one *domain*'s vocabulary. Key
  `{domain}:{version}:{form}:{digest}:{variant}` ↔ path
  `{form}/{digest[:2]}/{digest}.{variant}.npy`. `version` is the decode
  contract (bump to orphan old segments), `form` the kind of array, `variant`
  what distinguishes renderings of the same source (closed tuple, or a regex
  string for open axes like sample rate). Optional `dtypes` per form are
  enforced at publish. Schemes live in a **module-global registry keyed by
  domain**; the store dispatches on the key alone, so one store holds many
  domains. Re-registering an identical scheme is idempotent; a disagreeing one
  raises. The strict token/md5 regexes are a safety boundary: they are what
  keeps a key from naming a path the reaper would unlink.
- `store.py` — `SharedArrayStore`. Modes `off`/`read`/`readwrite`; attach
  `mmap` (read-only enforced by numpy) or `read`. Publish writes a
  pid/thread-unique tmp file then `os.replace` (atomic). `_admit` gates
  cheapest-first: min size, max size, statvfs free-space watermark (cached 1s),
  byte budget from `.usage.json`. The reaper runs opportunistically after
  publish, holds `.reap.lock` (O_EXCL, broken after 300s), walks only the root,
  `lstat`s and only unlinks regular `.npy` files, evicts TTL-expired then
  oldest-mtime down to 80% of `max_bytes`, and writes `.usage.json` as the
  cross-process usage figure. LRU uses explicit mtime touches (at most once
  per 60s), not atime. Unlink-while-mapped is safe, so no refcounting exists.
- `cache.py` — `TieredPixelCache`: L1 in-process memo (anything with
  `get`/`set_array`) in front of the L2 store. L1 memoises the *attached
  mapping*, not a copy. `get_with_source` returns `(arr, "l1"|"shm"|"decode")`
  so callers never infer the tier from counter deltas. The store counts its own
  hits/publishes; the tier must not double-count them (`drain_stats` sums both).
- `read.py` — `note()` (the single writer of `{prefix}{source}` counters,
  raises on an unknown source) and a never-raising `attach()` helper. Not
  re-exported from `__init__`.

Both store and cache satisfy a consumer's `PixelCache` protocol
(`get`/`set`/`set_array`/`get_with_source`/`drain_stats`).

## Tests

Tests deliberately use non-pixel domains (audio: `wav16k`, `sr\d+` variants)
to prove genericity. Because the scheme registry is process-global, **each
test module must register a unique domain name** (`ckaudio`, `cktier`, ...) or
it will collide with another module's scheme. Store fixtures lower
`min_free_bytes`/`min_array_bytes` and set `reap_interval_s=0` so gates and
the reaper are exercisable in `tmp_path`.

`tests/test_sox.py` renders a `synth` sine with `sox` to raw samples and maps
them through the store, including from a child process. It skips when `sox` is
not on PATH. `tests/test_ffmpeg.py` does the same for video: an ffmpeg
`testsrc` clip dumped as `rawvideo` and stored as one `(T, H, W, C)` array,
with the frame rate as the variant (`fr10`). It skips without `ffmpeg`.

## Repo conventions

- `AI.md` is the ledger of AI vs. maintainer contributions, including defects
  AI-written code shipped; update it (via the `ai-md` skill) with each commit.
- Comments explain *why*, often with a measurement; keep that style.
