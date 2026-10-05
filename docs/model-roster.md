# Model roster (measured 2026-10-05, local Ollama, CPU-heavy box)

Roles pinned by live trial, not preference. One resident model at a
time — the box serves inference at 88% CPU/12% GPU, so pool
round-robin is aspirational; sequential fallback is the honest shape
until a GPU host arrives.

| Role | Model | Evidence |
|---|---|---|
| Architect / Engineer | `gpt-oss:20B` (MXFP4, 131k ctx) | Binding interfaces kept (`run(fn, opts)` + `RetryOptions`); 4/4 schema-clean tasks (`RETRY-001`–`004`) with runnable `assert` acceptance; obeys literal ID shape + no-redesign rule |
| Developer | `ornith:latest` | 6/6 on hard retrier task across 2 attempts incl. self-repair of `isinstance` crash via REVISE loop (~7 min wall, zero human tokens) |
| Tester (advisory) | same as developer (`coder_fast` pool) | All three candidates judged buggy v1 correctly (`passed=False`); execution overrides numbers under `verify=True`, so locality wins |
| Writer / docs | `mistral-nemo:12b` | Only one producing accurate shippable prose (docstring + change note naming the real fix); `ornith` leaks tool-think, `gpt-oss` minimal |

## Sizing notes

- `qwen3.5:latest` (9B, 262k ctx) pulled but never answered: cold load
  past the 600s timeout. Keep pooled, not leading, until warmed/resident.
- `num_ctx`: 16384 for qwen (64k KV-cache was pure RAM overhead for
  ~2k-char prompts); `ornith` on model default; `temperature: 0`
  everywhere local; `num_predict` 4096–8192.
- `mistral-nemo:12b` unloads after ~4 idle min (`ollama ps` expiry);
  `OLLAMA_KEEP_ALIVE` if it should stay resident as writer.

## Open wiring

- No `writer` role in `configs/workers.yaml` — `writer_task` reachable
  only in scripted tests. Wire writer → reviewer-pool/nemo into the
  workflow WRITE state when docs automation is wanted.
