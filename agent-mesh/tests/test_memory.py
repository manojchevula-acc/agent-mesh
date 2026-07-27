"""Conversation memory (JSONL store) tests.

Tests ConversationStore append, load, multi-turn, session isolation.
Uses a temp directory — never touches production data.

Run:
    python -m pytest tests/test_memory.py -v
    python -m unittest tests.test_memory -v
"""
import os
import sys
import pathlib
import shutil
import tempfile
import unittest

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestConversationStore(unittest.TestCase):
    _tmp_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_memory_")
        os.environ["CONVERSATION_STORE_DIR"] = cls._tmp_dir
        os.environ["ENABLE_CONVERSATION_MEMORY"] = "true"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def _make_store(self):
        from src.memory.jsonl_backend import JsonlBackend
        from src.memory.conversation_store import ConversationStore
        return ConversationStore(backend=JsonlBackend(store_dir=self._tmp_dir))

    def test_01_append_creates_file(self):
        store = self._make_store()
        store.append_turn_rich("session_a", "What is the margin?", "The margin is 5%.")
        session_file = pathlib.Path(self._tmp_dir) / "session_a.jsonl"
        self.assertTrue(session_file.exists(), "JSONL file should be created after append")

    def test_02_load_returns_turn(self):
        store = self._make_store()
        store.append_turn_rich("session_b", "What is the credit score?", "Credit score is 690.")
        # load_messages() returns the actual stored turns; load_with_summary() always returns []
        messages = store.load_messages("session_b")
        self.assertGreaterEqual(len(messages), 1, "Should return at least one message after append")

    def test_03_multiple_turns_accumulate(self):
        store = self._make_store()
        store.append_turn_rich("session_c", "Q1", "A1")
        store.append_turn_rich("session_c", "Q2", "A2")
        store.append_turn_rich("session_c", "Q3", "A3")
        messages = store.load_messages("session_c")
        self.assertGreaterEqual(len(messages), 6, "3 turns = 6 messages (user+assistant each)")

    def test_04_session_isolation(self):
        store = self._make_store()
        store.append_turn_rich("session_x", "Question for X only", "Answer for X only")
        store.append_turn_rich("session_y", "Question for Y only", "Answer for Y only")
        file_x = pathlib.Path(self._tmp_dir) / "session_x.jsonl"
        file_y = pathlib.Path(self._tmp_dir) / "session_y.jsonl"
        content_x = file_x.read_text(encoding="utf-8")
        content_y = file_y.read_text(encoding="utf-8")
        self.assertNotIn("Question for Y only", content_x, "session_x file should not contain session_y data")
        self.assertNotIn("Question for X only", content_y, "session_y file should not contain session_x data")

    def test_05_empty_session_returns_empty(self):
        store = self._make_store()
        messages = store.load_messages("nonexistent_session_xyz")
        self.assertEqual(messages, [], "Non-existent session should return empty messages list")

    def test_06_bind_session_does_not_raise(self):
        store = self._make_store()
        store.append_turn_rich("session_d", "Hello", "Hi there")
        try:
            store.bind_session("session_d", "alice")
        except Exception as exc:
            self.fail(f"bind_session raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
