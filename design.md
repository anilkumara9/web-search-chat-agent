# Design

## Local web interface

The browser UI in `web/` calls the loopback-only standard-library HTTP server in
`server.py`. It reuses the same `Agent` and the same one model-visible `web_search`
tool. Conversation, history/title search, and System & setup are implemented;
there is no login or public deployment. Host/origin checks, a per-start token,
allowlisted static files, a content security policy and text-node rendering reduce
common local-web risks, but this is not a security audit or shared-hosting design.
Keys stay in server environment variables. SQLite connections are opened and closed
per request. One global request lock serializes chat across tabs; history remains
available. Configuration badges report credential presence, not provider health.

The original CLI remains supported. Full startup, route details, controls and
privacy limits are documented in [README.md](README.md).

[Open the system diagram and actual narrated walkthrough](https://files.instinct.com/n2jvyjwdzpdh-search-recall-live-walkthrough)

25 deterministic tests pass: the original19 and6 HTTP tests covering static/CSP,
key non-disclosure, validation/configuration, host/origin/token rejection, scoped
persistent history, and busy/control-flow behavior. Scripted model tests are not
accuracy evidence. UI desktop/mobile inspection and current live outcomes are
reported separately from the historical build records below.


## Goal
One tool (web search). Answer fast, get things right, remember what was asked and when.

## Structure
```
chat.py            CLI entry (REPL or one-shot), prints latency per answer
agent/core.py      LLM client, tool-calling loop, memory injection, citation check
agent/search.py    the single tool: web_search (Tavily / Brave / DuckDuckGo), short cache
agent/memory.py    SQLite + FTS5 timestamped per-user log, recall
tests/             unit tests (memory) and control-flow tests (agent, stub model)
```

## Speed
- No streaming: the answer is shown once complete (see Verification status for why).
- At most 2 tool rounds plus a final round with no tools, so the loop always ends.
- Prompt tells the model to answer stable facts directly and to use one precise search
  for anything recent or uncertain, instead of several.
- Search results are trimmed (title, URL, 500-char snippet, 4 results) to keep the prompt small.
- Search results are cached for 5 minutes, keyed by query. A cached result is labeled
  "CACHED ... not a fresh lookup" so the model cannot present it as freshly verified.
- Network timeouts: 8 s for search, 60 s for the model.

## Correctness
- The system prompt contains today's date, so "latest" questions trigger a search.
- The model must cite the source URL for searched claims, and say so when results
  conflict or are thin.
- After the answer is produced, every URL in it is checked against the URLs that actual
  searches returned this turn. Any other URL is replaced with "[unverified link removed]".
  This catches invented links; it does not prove a cited page supports the claim.
- A failed or empty search is returned to the model as an explicit error and is never
  cached; the prompt tells the model to report the failure.

## Memory
- Every user and assistant turn is stored in SQLite with a UTC timestamp and a user id.
- Each question loads (a) the last 8 turns of the current session and (b) up to 5 past
  turns from any session whose keywords match (FTS5), all filtered by the same user id.
- Relative time ("3 h ago") is computed in code from the stored timestamp and shown next
  to the absolute time, so the model never has to calculate it.
- The prompt instructs the model to answer "what did I ask" questions only from MEMORY
  and to say it has no record when nothing matches. Memory text is marked as data, not
  instructions, to reduce prompt-injection from stored content.
- Limits: recall is keyword-based, so a paraphrase with no shared words can be missed.
  Identity is whatever `AGENT_USER` is set to; there is no authentication.

## Failure handling
- LLM errors are caught, reported to the user, and stored.
- A model that requests a tool after the cap is asked again with the search results flattened to plain text and no tools.
- Bad tool arguments produce an error result instead of a crash.

## Verification status
All live numbers below are from real runs on 2026-11-10 (IST evening): real Groq model, real
Tavily search, no stubs. Raw records: `e2e_results.json` (memory/isolation run, earlier code and
model `qwen/qwen3.8-27b`), `eval_results.jsonl` (latest-version eval, every run kept, including
failures), `probe_results.jsonl` (free-form probes). The 19 unit tests use a scripted stub model
and only check control flow, memory, isolation, citation filtering and version parsing.

Latest-version accuracy. The expected value is read from python.org and go.dev at run time
(python.org/downloads, go.dev/dl/?mode=json), never hardcoded:
- Before the fix, "latest Python" was wrong in most runs ("3.15.0" or "3.14.10" instead of
  3.15.2) and "latest Go" was wrong every time (1.27, 1.27.0, 1.27.2 instead of 1.27.3).
  Cause: generic search snippets mention older releases and the model did not pick the highest.
- Fix: for a latest/version question the code runs a second search limited to a candidate
  project site and extracts stable version numbers from the page text in code (pre-releases
  skipped). The site is matched to the project by name, so it is only a candidate: the model
  is told it is not verified as official, and the answer ends with a "Source check" line saying
  the site was matched by name. If no candidate site is found, the answer ends with a
  "could not find a project site to check this against" note.
- After the fix, with `openai/gpt-oss-120b`: Go 2 of 2 clean runs correct (1.27.3, 11-16 s);
  Python 1 of 1 clean run correct (3.15.2, 9.6 s). Earlier post-fix attempts ended in errors
  (429 rate limit; HTTP 400 "Tool choice is none, but model called a tool", since fixed by
  flattening the tool exchange for the final call) or in a stale answer (Go 1.27.2) before
  the discovery search was added. Two or three runs per product is not a reliability figure.
- Non-version freshness: "weather forecast in Bengaluru today" returned a sourced answer for
  the right date, with what could not be verified listed (11.7 s, 2 searches).
- Conflicting-evidence probe (population of Bengaluru): blocked by provider rate limits
  during the build window (waits up to 785 s); NOT demonstrated, so conflict handling is untested live. See `probe_results.jsonl`.

Memory and isolation (`e2e_live.py`, earlier run): a new process recalled earlier questions with
UTC timestamps and relative age, and another user id got "no record". Relative-time wording
beyond minutes is verified by unit tests only.

Limits and raw failures:
- Rate limits dominate. The Groq key allows 8,000 tokens per minute per model, and one
  question costs about 4,000-4,600 tokens. Back-to-back questions hit HTTP 429 and
  sometimes a wait of minutes (observed 17 s up to 270 s). The agent retries 429 twice when
  the provider's reset hint is 25 s or less, otherwise it reports that it is rate limited.
  `qwen/qwen3.8-27b` also hit an output-tokens-per-minute limit of 1,000, so the default is now
  `openai/gpt-oss-120b`, which gave an occasional "tool call validation failed" 400 (retried once).
- No streaming: the answer appears when complete, so visible time equals total time. This avoids
  showing hidden reasoning or a draft made before searching.
- Search latency 0.1-2 s per call; model calls dominate the 10-25 s total. Figures are single runs.
- Keyless DuckDuckGo returned a bot challenge, so a Tavily or Brave key is required.
- Only the web_search tool exists; it reads snippets and page text for the version check, but
  the model cannot open arbitrary pages.
- The citation filter removes invented URLs. It does not prove a cited page supports a claim.
- No claim of zero mistakes is made.

## What I would do with more time
Embedding-based recall, a second search provider as automatic fallback, and a measured
latency/accuracy evaluation on a fixed question set.
