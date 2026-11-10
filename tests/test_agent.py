"""Agent-loop tests. These use a SCRIPTED STUB LLM (clearly not a real model) to
exercise control flow deterministically: tool-round cap, failure handling,
citation filtering, cache labeling, memory injection. They do not measure answer
quality; that needs the live end-to-end run (see design.md)."""
import json, os, tempfile, unittest
from agent.core import Agent, MAX_TOOL_ROUNDS
from agent.memory import Memory
from agent import search as S


class StubLLM:
    def __init__(self, script): self.script, self.seen = list(script), []
    def chat(self, messages, tools=None):
        self.seen.append((messages, tools))
        step = self.script.pop(0)
        if isinstance(step, Exception): raise step
        return step


def call(q, i="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": i, "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": q})}}]}


def fake_search(q, site=None, raw=False):
    return {"query": q, "backend": "test", "elapsed_ms": 1,
            "results": [{"title": "T", "url": "https://real.example/a", "snippet": "s"}]}


def mk(script, user="u", search=fake_search, mem=None):
    mem = mem or Memory(os.path.join(tempfile.mkdtemp(), "t.db"))
    return Agent(user_id=user, llm=StubLLM(script), memory=mem, session="s", search=search), mem


class AgentTests(unittest.TestCase):
    def test_search_then_answer(self):
        a, _ = mk([call("x"), {"role": "assistant", "content": "Answer https://real.example/a"}])
        self.assertIn("https://real.example/a", a.ask("q"))
        self.assertEqual(len(a.last_stats["searches"]), 1)

    def test_invented_url_removed(self):
        a, _ = mk([call("x"), {"role": "assistant", "content": "See https://made.up/page and https://real.example/a"}])
        out = a.ask("q")
        self.assertNotIn("made.up", out)
        self.assertIn("https://real.example/a", out)
        self.assertEqual(a.last_stats["removed_urls"], ["https://made.up/page"])

    def test_tool_round_cap(self):
        script = [call("x", f"c{i}") for i in range(MAX_TOOL_ROUNDS)] + \
                 [{"role": "assistant", "content": "final"}]
        a, _ = mk(script)
        self.assertEqual(a.ask("q"), "final")
        self.assertIsNone(a.llm.seen[-1][1])  # last call offers no tools
        # model that keeps asking for tools even without them does not loop forever
        script = [call("x", f"c{i}") for i in range(MAX_TOOL_ROUNDS + 1)]
        a, _ = mk(script)
        self.assertIn("could not finish", a.ask("q"))

    def test_llm_failure_is_reported_and_stored(self):
        a, mem = mk([RuntimeError("boom")])
        self.assertIn("could not be completed", a.ask("q"))
        self.assertEqual(len(mem.recent("u", "s")), 2)

    def test_search_failure_is_passed_to_model(self):
        a, _ = mk([call("x"), {"role": "assistant", "content": "search failed, no answer"}],
                  search=lambda q, site=None, raw=False: {"query": q, "results": [], "error": "blocked"})
        a.ask("q")
        tool_msg = [m for m in a.llm.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("blocked", tool_msg["content"])

    def test_memory_injected_with_timestamp_and_scoped(self):
        mem = Memory(os.path.join(tempfile.mkdtemp(), "t.db"))
        mem.add("alice", "old", "user", "How tall is Mount Everest?", ts=1.0)
        a, _ = mk([{"role": "assistant", "content": "ok"}], user="alice", mem=mem)
        a.ask("What did I ask about Everest?")
        sys_prompt = a.llm.seen[0][0][0]["content"]
        self.assertIn("How tall is Mount Everest?", sys_prompt)
        self.assertIn("1970-01-01", sys_prompt)
        b, _ = mk([{"role": "assistant", "content": "ok"}], user="bob", mem=mem)
        b.ask("What did I ask about Everest?")
        self.assertNotIn("Mount Everest", b.llm.seen[0][0][0]["content"].split("MEMORY")[-1].split("What did I ask")[0])

    def test_reasoning_tags_stripped(self):
        a, _ = mk([{"role": "assistant", "content": "<think>hidden</think>Visible answer"}])
        self.assertEqual(a.ask("q"), "Visible answer")
        a, _ = mk([{"role": "assistant", "content": "Visible\n</think>\n\nSecond"}])
        self.assertNotIn("think", a.ask("q"))

    def test_question_digest_lists_all_past_questions_for_user_only(self):
        mem = Memory(os.path.join(tempfile.mkdtemp(), "t.db"))
        mem.add("alice", "old1", "user", "first question about cats", ts=10)
        mem.add("alice", "old2", "user", "second question about tax", ts=20)
        mem.add("bob", "x", "user", "bob private question", ts=15)
        a, _ = mk([{"role": "assistant", "content": "ok"}], user="alice", mem=mem)
        a.ask("List every question I asked")
        p = a.llm.seen[0][0][0]["content"]
        self.assertIn("first question about cats", p)
        self.assertIn("second question about tax", p)
        self.assertNotIn("bob private", p)

    def test_latest_question_without_site_gets_official_site_note(self):
        a, _ = mk([call("python latest"), {"role": "assistant", "content": "done"}])
        a.ask("What is the latest version of Python?")
        tool_msg = [m for m in a.llm.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("official domain", tool_msg["content"])
        a, _ = mk([call("python"), {"role": "assistant", "content": "done"}])
        a.ask("Explain decorators")
        tool_msg = [m for m in a.llm.seen[1][0] if m["role"] == "tool"][0]
        self.assertNotIn("official domain", tool_msg["content"])

    def test_latest_version_checks_official_site_and_flags_when_unverified(self):
        calls = []

        def srch(q, site=None, raw=False):
            calls.append(site)
            url = "https://python.org/downloads" if site == "python.org" else "https://python.org/old"
            return {"query": q, "results": [{"title": "t", "url": url, "snippet": "3.15.2"}]}

        a, _ = mk([call("python"), {"role": "assistant", "content": "3.15.2"}], search=srch)
        out = a.ask("What is the latest version of Python?")
        self.assertIn("python.org", calls)          # automatic official-site search
        self.assertNotIn("could not confirm", out)
        # no result host matches the product name -> honest caveat appended
        a, _ = mk([call("zork"), {"role": "assistant", "content": "v9"}],
                  search=lambda q, site=None, raw=False: {"query": q, "results": [{"title": "t", "url": "https://blog.example.com/x", "snippet": "v9"}]})
        out = a.ask("What is the latest version of Zork?")
        self.assertIn("could not find a project site", out)
        self.assertTrue(a.last_stats["official_unverified"])

    def test_highest_versions_computed_from_text(self):
        t = "Python 3.14.10, python-3.15.2 and Python 3.15.0; pip 26.2.1; build 2026.10.25.1"
        self.assertEqual(S.highest_versions(t, "python", 3), ["3.15.2", "3.15.0", "3.14.10"])
        self.assertEqual(S.highest_versions("go1.27.3 go1.26.10 v9.9.9", "go"), ["1.27.3", "1.26.10"])

    def test_tool_attempt_after_cap_is_recovered(self):
        script = [call("x", f"c{i}") for i in range(MAX_TOOL_ROUNDS)] + \
                 [RuntimeError("model API HTTP 400: Tool choice is none, but model called a tool"),
                  {"role": "assistant", "content": "answer from results"}]
        a, _ = mk(script)
        self.assertEqual(a.ask("Explain decorators"), "answer from results")

    def test_cache_is_labeled(self):
        S._cache.clear()
        S._cache[("q", None, 4, False)] = (__import__("time").time() - 100, {"query": "q", "results": [], "backend": "t"})
        r = S.web_search("q")
        self.assertTrue(r["cached"])
        self.assertGreaterEqual(r["cached_age_s"], 100)
        self.assertIn("CACHED", r["note"])


if __name__ == "__main__":
    unittest.main()
