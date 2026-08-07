# M2 Additions: Rate-Limit Resilience, Score History, PII Inspect

These are additive changes to files you already have. `history.py` is
brand new; the other three are drop-in replacements of files that
already existed in your project.

## Files in this zip

| File | Type | What changed |
|---|---|---|
| `data_quality_engine/phase2/ai_explainer.py` | replace | rate limiter, retry/backoff, PII adapter, trend-aware executive summary, fixed deprecated model default |
| `data_quality_engine/phase2/enhanced_report.py` | replace | PII Inspect button injection, score-trend banner injection |
| `data_quality_engine/phase2/history.py` | **new** | saves each run, computes score trend vs. last run |
| `generate_report_phase2.py` | replace | new `--client-id` flag, wires history in |
| `requirements.txt` | replace | added `requests`, `python-dotenv` (were imported but missing) |
| `tests/test_phase2_m2_additions.py` | **new** | 20 tests covering all of the above |

## 1. Install (if you don't already have these)

```bash
pip install -r requirements.txt
```

## 2. Run the new tests

```bash
pytest tests/test_phase2_m2_additions.py -v
```

Covers, all mocked/offline — no real Gemini key needed:
- rate limiter allows bursts up to the limit, blocks past it
- retry-with-backoff recovers from a transient 429, gives up cleanly after max retries
- `explain_check` still falls back gracefully when Gemini is completely unreachable
- the PII block correctly adapts into the same shape as a normal check summary
- the PII Inspect button gets injected when the section exists, and is a safe no-op when it doesn't
- score trend: first run, improvement, decline, and that different clients/files never mix history

Also re-run your existing suites to confirm nothing broke:

```bash
pytest tests/test_phase2_m1_setup.py -v
pytest tests/test_main_pipeline.py -v
```

## 3. Try it end-to-end

```bash
python generate_report_phase2.py "path/to/your_file.xlsx" --client-id acme --out reports
```

Run it **twice** on the same file (or two different snapshots of similar
data) to see the trend banner appear — the first run will say "First
recorded run", the second will show the actual delta.

## 4. What to look for in the output HTML

- A small colored banner under the score ring: green if improved, red if
  declined, gray if unchanged, blue on the first run
- An **Inspect** button next to "Sensitive Data & Standardization
  Summary" — same modal, same AI/fallback behavior as every other check
- If you don't have a `GEMINI_API_KEY` set yet, everything above still
  works — you'll just see "Rule-based explanation" badges instead of "AI
  Explanation" ones. Get a free key at https://aistudio.google.com/apikey
  when you're ready.

## Notes / things I want you to know I did

- I did **not** touch `engine/` (Phase 1) at all — everything is additive
  in `phase2/` plus the two CLI/report files that were already Phase 2.
- `GEMINI_RPM_LIMIT` (default 8) controls the rate limiter — lower it if
  you still see 429s on the free tier, or raise it if Google gives your
  project more headroom later.
- `history.py` identifies a "client" by whatever string you pass to
  `--client-id`. If you never pass one, everything defaults to
  `"default_client"` and trends still work — they just won't separate by
  client until you start passing real ids.
