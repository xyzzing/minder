# 5. Laya-studio specializations are offline-fitted, seam-served bundles, never a runtime dependency

Date: 2026-10-10

## Status

Accepted (issue #34)

## Context

The task-difficulty contract is the one laya surface live on the proxy
(`difficulty_router: laya`), and the laya decision head carries no
signal on its rubric: on the difficulty-core-v1 eval set the head scores
at chance (0.333 on validation, 0.390 raw on eval) and its production
value comes entirely from the confidence floor abstaining on 76% of
requests. laya-studio (Hantlowt/laya-studio) fits training-free
specializations over the frozen laya encoder - centroids, whitening,
steering - and exports portable checkpoint-locked bundles loadable
through a laya-like `system_one` entry point. The measured case for
adopting one: whitened prototypes read 0.778 on the fitting corpus's
validation split (vs 0.333 head), and end-to-end 0.754 selective
accuracy at 32.5% coverage vs the head's 0.625 at 24%.

Two precedents bound the decision. The verl integration was rejected
(out-of-scope ledger) because minder exports datasets, it does not
train; laya-studio is the same category boundary - fitting happens
offline, the runtime never sees an optimizer. And the dual-engine work
(ADR-0002 pattern) established that minder adopts optional upstreams
behind explicit operator switches with fail-open fallbacks.

## Decision

1. **Offline fit, runtime serve.** The fitting corpus
   (`benchmarks/difficulty-core-v1/fitting/`) and the sweep/export
   drivers stay offline tooling; laya_studio is never imported unless
   `MINDER_LAYA_SPECIALIZATION` is set. The runtime dependency ratchet
   stays at zero. The exported bundle lives in-repo
   (`presets/laya-specializations/difficulty-v1/`) because it is small
   (4.1MB of matrices, no weights), checkpoint-locked, and fidelity-
   gated; the base checkpoint keeps coming from the HF cache as before.

2. **One contract, one bundle.** The seam answers only the
   task-difficulty contract (detected by contract identity, falling
   back to question shape for callers that pass none); every other
   contract delegates to the base head. This is why routing-core-v1
   replays byte-identically with the seam active. Future bundles for
   other contracts get their own env surface rather than widening this
   one.

3. **One distribution, no fake cross-check.** The bundle answers the
   choice question; the 0-3 score corroborates the argmax. An expected
   index over the flat prototype softmax sits near the scale's middle
   and would escalate through resolve_difficulty's cross-check
   (measured 0.75 -> 0.66 selective accuracy before the fix). The base
   head keeps its real two-head cross-check on its own path.

4. **Own calibration scale, measured.** The prototype softmax is flat;
   the head's 2.0x margin scale would abstain on everything. The
   specialized distribution pins `SPEC_MARGIN_SCALE = 20.0` (floor 0.7
   at a 0.035 margin) from the hard split's margin separation, with
   provenance beside the constant. Scale and endpoints are provisional
   until the owner accepts the draft eval labels; live shadow is the
   arbiter after that.

5. **Fail open, named.** A missing laya_studio, missing bundle, or
   bundle error falls open to the base head. Both failure sites print
   one named stderr line (the bare_except_pass ratchet correctly
   rejected the silent version): an operator who set the flag must be
   able to see it stopped being served.

## Consequences

- Flipping the live proxy is an operator action: set
  `MINDER_LAYA_SPECIALIZATION` in the proxy's environment (the worker
  child inherits it) and restart; `minder-op difficulty replay` is the
  evidence to check first.
- The eval set's `label_status` is `draft_pending_owner_review`; every
  number in this ADR is provisional against those labels. Owner
  acceptance flips the status and re-pins the numbers.
- Steering strategies are binary-only upstream, so head-preserving
  specialization does not exist for this 4-label contract; if
  laya-studio grows multi-class steering, re-run the sweep before
  assuming whitened prototypes stays the winner.
- The difficulty replay's calibration detail (Brier, ECE,
  selective accuracy) is the standing evidence surface for any future
  "the router improved" claim (prd-v0.10 T30 slice; PSI and a wire
  provider remain open).
