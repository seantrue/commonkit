# commonkit

Named shared-memory segments for numpy arrays. One process decodes and
publishes; every other process attaches the same physical pages.

The name is for Fortran's `COMMON` block: a named region several separately
compiled programs agree to share. That is exactly the contract here — the name
is the whole interface, and the segment outlives whoever wrote it.

## What it is

* `SharedArrayStore` — publish/attach `.npy` segments under a root directory,
  with admission gates, a TTL reaper and a byte budget.
* `KeyScheme` — the addressing vocabulary for one *domain*. A scheme fixes the
  domain, a version, the legal forms and the legal variants, and turns
  `(digest, variant, form)` into both a key and a relative path.
* `TieredPixelCache` — an in-process L1 memo in front of a store, so a loop
  that looks up the same digest repeatedly pays one attach rather than one per
  call.

## The invariant everything rests on

**A missing segment is never an error, only a slower path.** Every failure mode
— absent root, full filesystem, reaped segment, torn file, malformed key —
returns `None` or `False` and lets the caller recompute. Nothing here may raise
into a caller. The constructor never raises either: an unusable root leaves the
store `disabled` and every method becomes a no-op.

## Keys

    {domain}:{version}:{form}:{digest}:{variant}

and the path it names:

    {root}/{form}/{digest[:2]}/{digest}.{variant}.npy

Two-level sharding, so no directory holds a million entries. The `variant` axis
is whatever distinguishes two renderings of the same source: for images it is
orientation (`o0`, `o1`, `oa`), for audio it would be sample rate (`sr16000`).
Schemes are registered by domain so `parse()` and `relpath()` can dispatch on a
key alone, which is what lets one store hold several domains.

## Origin

Extracted from the pixel-cache layer of a private image-corpus
application, where it ran in production first. The measurements in the module
docstrings were taken there; the framework-coupled half (the settings wiring
and the decode entry point) deliberately stayed behind.
