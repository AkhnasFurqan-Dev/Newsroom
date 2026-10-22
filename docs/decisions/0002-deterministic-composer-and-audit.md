# 0002 — Deterministic composer first, Ollama optional, independent audit always

## Context

The application must work with zero cost and no cloud account, and must never
present invented facts as verified. A local model is desirable for natural prose
but is not guaranteed to be installed, and asking one model to write and approve
its own article is not independent verification.

## Decision

- Ship a deterministic **fixture composer** that writes from the claim ledger and
  is the default for synthetic channels.
- Provide an optional **Ollama** provider (`qwen2.5:3b-instruct` default) with a
  strict Pydantic output schema and bounded retries.
- If Ollama is unreachable or returns invalid output, fall back to the
  deterministic composer and record the reason on the article and audit payload.
- Always run a separate deterministic **evidence audit**: every sentence with a
  name, place, date, number or quote must be supported by the claim ledger.
  Unsupported sentences become review items and block release.
- Keep prompts version-controlled separately from code paths and require source
  IDs, schema output and uncertainty labels.
- Label demo output as synthetic and never present fixture prose as live reporting.

## Alternatives considered

- **Cloud model**: rejected for cost, privacy and data-permission reasons.
- **Ollama required**: rejected because the fixture path must work on a clean
  Windows install with no GPU model.
- **Model self-audit**: rejected as not independent.

## Consequences

- Reproducible tests and demos are possible without a model.
- Quality differs between the fixture composer and a real local model; the edition
  records which provider produced each draft.
- A model fallback is visible, not silent.
- **Limit:** Ollama was not installed on the build machine, so real model quality,
  latency and VRAM use are unverified. The adapter’s schema/fallback behaviour is
  covered by tests and by the unavailable path in `diagnostics`.

## Files

- `channel_press/ai/providers.py`
- `channel_press/editorial/drafting.py`
- `channel_press/editorial/audit.py`
- `channel_press/editorial/style.py`
- `tests/test_editorial.py`
