"""One free-form live probe: python tests/probe_live.py "question" [model]. Appends to probe_results.jsonl."""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.core import Agent, LLM
from agent.memory import Memory
q = sys.argv[1]; model = sys.argv[2] if len(sys.argv) > 2 else "openai/gpt-oss-120b"
a = Agent(user_id="probe", memory=Memory(os.path.join(tempfile.mkdtemp(), "p.db")), session="s", llm=LLM(model=model))
out = a.ask(q)
row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "question": q, "model": model, "answer": out,
       "searches": [s.get("query") for s in a.last_stats["searches"]], "total_ms": a.last_stats["total_ms"],
       "error": a.last_stats.get("error")}
open(os.path.join(os.path.dirname(__file__), "..", "probe_results.jsonl"), "a").write(json.dumps(row) + "\n")
print(out); print(row["searches"], row["total_ms"], row["error"])
