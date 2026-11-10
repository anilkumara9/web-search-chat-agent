"""Live accuracy check on questions whose current answer is read from the OFFICIAL source
at run time (python.org and go.dev), so the expected value is not hard-coded.
One case per invocation, results appended to eval_results.jsonl (resume-friendly, and the
caller spaces runs out to respect the provider's tokens-per-minute limit).
Usage: python tests/eval_live.py <python|go> [model]
"""
import json, os, re, sys, tempfile, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.core import Agent, LLM
from agent.memory import Memory

UA = {"User-Agent": "Mozilla/5.0"}
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read().decode()


def truth(case):
    if case == "python":
        m = re.search(r"Download Python (\d+\.\d+\.\d+)", get("https://www.python.org/downloads/"))
        return m.group(1), "What is the latest stable version of Python and when was it released?"
    if case == "go":
        d = json.loads(get("https://go.dev/dl/?mode=json"))
        return d[0]["version"].removeprefix("go"), "What is the latest stable Go release version?"


def version_tokens(text):
    return set(re.findall(r"\b\d+\.\d+(?:\.\d+)?\b", text))


case = sys.argv[1]
if len(sys.argv) > 2:
    os.environ["LLM_MODEL"] = sys.argv[2]
expected, q = truth(case)
a = Agent(user_id="eval", memory=Memory(os.path.join(tempfile.mkdtemp(), "e.db")), session="s")
ans = a.ask(q)
ok = expected in version_tokens(ans) and not any(
    tuple(map(int, v.split("."))) > tuple(map(int, expected.split("."))) for v in version_tokens(ans)
    if len(v.split(".")) == len(expected.split(".")))
rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "case": case, "model": a.llm.model, "expected_from_official_source": expected,
       "correct": bool(ok), "total_ms": a.last_stats["total_ms"], "searches": [s["query"] for s in a.last_stats["searches"]],
       "tokens_used": a.last_stats.get("tokens_used"), "unverified_caveat": a.last_stats.get("official_unverified", False),
       "error": a.last_stats.get("error"), "answer": ans}
open("eval_results.jsonl", "a").write(json.dumps(rec) + "\n")
print(json.dumps({k: v for k, v in rec.items() if k != "answer"}), "\n", ans[:400])
