# Web-search chat agent

A command-line chat agent with exactly one tool, `web_search`. It answers from live
search results with cited sources, and keeps a timestamped, per-user memory of
every question so it can answer "what did I ask, and when?".

Pure Python 3.10+ standard library. No dependencies to install.

## Run

```
export LLM_API_KEY=...            # any OpenAI-compatible chat-completions API
export LLM_BASE_URL=...           # default: https://api.groq.com/openai/v1
export LLM_MODEL=...              # default: openai/gpt-oss-120b
export TAVILY_API_KEY=...         # or BRAVE_API_KEY; one of them is needed for reliable search
export AGENT_USER=your-name       # memory is scoped to this id (default: local)
python chat.py                    # interactive
python chat.py "your question"    # one shot
```

Memory persists in `memory.db` (override with `AGENT_DB`). Without a search key the agent
falls back to DuckDuckGo's HTML endpoint, which may be blocked by bot checks; when search
fails the agent says so instead of guessing.

## Test

```
python -m unittest discover -s tests
```

`tests/test_agent.py` uses a scripted stub model to check control flow (tool cap, failure
handling, citation filtering, memory scoping). It does not measure answer quality. See
`design.md` for what has and has not been verified.
