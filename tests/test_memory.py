import os, tempfile, unittest
from agent.memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.m = Memory(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_recent_order_and_timestamps(self):
        self.m.add("u","s", "user", "first", ts=1000)
        self.m.add("u","s", "assistant", "reply", ts=1001)
        self.m.add("u","other", "user", "elsewhere", ts=1002)
        rows = self.m.recent("u","s")
        self.assertEqual([r[2] for r in rows], ["first", "reply"])
        self.assertEqual(rows[0][0], 1000)

    def test_cross_session_search(self):
        self.m.add("u","old", "user", "What is the capital of Australia?", ts=5)
        self.m.add("u","new", "user", "Weather in Paris?", ts=9)
        hits = self.m.search("u","what did I ask about Australia earlier?")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], "old")

    def test_stopword_only_query(self):
        self.assertEqual(self.m.search("u","what did you ask"), [])

    def test_users_are_isolated(self):
        self.m.add("alice", "s1", "user", "my secret project is Falcon", ts=1)
        self.assertEqual(self.m.search("bob", "Falcon project"), [])
        self.assertEqual(self.m.recent("bob", "s1"), [])
        self.assertEqual(len(self.m.search("alice", "Falcon")), 1)

    def test_ago(self):
        from agent.memory import ago
        self.assertEqual(ago(1000, now=1030), "just now")
        self.assertEqual(ago(0, now=3 * 3600), "3 h ago")
        self.assertEqual(ago(0, now=3 * 86400), "3 days ago")


if __name__ == "__main__":
    unittest.main()


class PrereleaseTests(unittest.TestCase):
    def test_highest_versions_skips_prereleases(self):
        from agent.search import highest_versions
        text = "Python 3.16.0a1 Python 3.15.2 Python 3.16.0rc1 Python 3.15.1"
        self.assertEqual(highest_versions(text, "Python"), ["3.15.2", "3.15.1"])
