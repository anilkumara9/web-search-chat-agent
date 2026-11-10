"""Live end-to-end run against the REAL model and REAL Tavily search (no stubs).
Needs LLM_API_KEY, TAVILY_API_KEY (and optionally LLM_MODEL). Writes e2e_results.json.
Usage: python tests/e2e_live.py
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.core import Agent, LLM
from agent.memory import Memory

db = os.path.join(tempfile.mkdtemp(), "e2e.db")
model = os.environ.get("LLM_MODEL", "")
log = []


def run(agent, q):
    time.sleep(float(os.environ.get('E2E_PAUSE', '0')))  # pacing for provider token-per-minute limits
    first = []
    t0 = time.time()
    ans = agent.ask(q, stream_cb=lambda t: first.append(time.time()) if not first else None)
    rec = {"session": agent.session, "question": q, "answer": ans,
           "answer_visible_ms": int((first[0] - t0) * 1000) if first else None,
           "total_ms": agent.last_stats["total_ms"], "searches": agent.last_stats["searches"],
           "llm_calls": agent.last_stats["llm_calls"], "removed_urls": agent.last_stats.get("removed_urls"),
           "error": agent.last_stats.get("error")}
    log.append(rec)
    print(f"\nQ: {q}\nA: {ans}\n   total {rec['total_ms']} ms, answer visible {rec['answer_visible_ms']} ms, "
          f"searches {[s['query'] for s in rec['searches']]}, removed urls {rec['removed_urls']}")
    return rec


mem = Memory(db)
a = Agent(user_id="e2e-user", memory=mem, session="session-A")
run(a, "What is the latest stable version of Python and when was it released?")
run(a, "Who is the current CEO of Nvidia?")
run(a, "What is 17 times 23?")                       # needs no search
# new process-like session: fresh Agent + Memory objects over the same file
a2 = Agent(user_id="e2e-user", memory=Memory(db), session="session-B")
run(a2, "What did I ask you about Python earlier, and when?")
run(a2, "List every question I have asked you so far.")
b = Agent(user_id="someone-else", memory=Memory(db), session="session-C")
run(b, "What did I ask you earlier about Python or Nvidia?")   # must not see e2e-user's history
json.dump({"model": model or a.llm.model, "runs": log}, open(os.environ.get("E2E_OUT", "e2e_results.json"), "w"), indent=1)
