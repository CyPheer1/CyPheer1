from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from scripts import update_readme as updater


def event(kind="PushEvent", repo="CyPheer1/example", date="2026-09-30T12:00:00Z", **extra):
    return {"type": kind, "repo": {"name": repo}, "created_at": date, "public": True, **extra}


class ProfileTests(unittest.TestCase):
    def test_replacement_is_scoped_and_idempotent(self):
        original = "intro\n<!-- ACTIVITY:START -->\nold\n<!-- ACTIVITY:END -->\noutro"
        changed = updater.replace_section(original, "ACTIVITY", "new")
        self.assertEqual(changed, original.replace("old", "new"))
        self.assertEqual(changed, updater.replace_section(changed, "ACTIVITY", "new"))

    def test_missing_duplicate_and_reversed_markers_fail(self):
        for text in ("", "<!-- ACTIVITY:START -->" * 2, "<!-- ACTIVITY:END --><!-- ACTIVITY:START -->"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                updater.replace_section(text, "ACTIVITY", "new")

    def test_activity_sorts_deduplicates_and_excludes_profile(self):
        events = [event(), event(), event(repo="CyPheer1/CyPheer1"), event(repo="CyPheer1/new", date="2026-10-01T00:00:00Z")]
        rendered = updater.render_activity(events, "CyPheer1")
        self.assertEqual(rendered.count("Pushed code to"), 2)
        self.assertLess(rendered.index("CyPheer1/new"), rendered.index("CyPheer1/example"))
        self.assertNotIn("CyPheer1/CyPheer1", rendered)

    def test_ignores_private_invalid_and_unsupported_events(self):
        events = [event(public=False), event(repo='evil/\"><script>'), event(date="invalid"), event(kind="WatchEvent")]
        rendered = updater.render_activity(events, "CyPheer1")
        self.assertIn("No recent public development events", rendered)
        self.assertNotIn("<script>", rendered)

    def test_empty_feed_and_limit(self):
        self.assertIn("No recent", updater.render_activity([], "CyPheer1"))
        events = [event(repo=f"CyPheer1/repo-{number}") for number in range(10)]
        self.assertEqual(updater.render_activity(events, "CyPheer1").count("Pushed code to"), 5)

    def test_create_event_uses_known_ref_type_only(self):
        rendered = updater.render_activity([event(kind="CreateEvent", payload={"ref_type": "branch"})], "CyPheer1")
        self.assertIn("Created a branch in", rendered)
        rendered = updater.render_activity([event(kind="CreateEvent", payload={"ref_type": "<script>"})], "CyPheer1")
        self.assertNotIn("<script>", rendered)

    def test_api_failure_preserves_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = "before\n<!-- ACTIVITY:START -->\nprevious\n<!-- ACTIVITY:END -->\n"
            content += "<!-- PROFILE-STATS:START -->\n" + updater.render_stats("CyPheer1", root) + "\n<!-- PROFILE-STATS:END -->\nafter\n"
            path = root / "README.md"
            path.write_text(content, encoding="utf-8")
            with patch.object(updater, "fetch_events", side_effect=URLError("offline")):
                updater.update_readme(root, "CyPheer1")
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_update_is_repeatable_and_uses_local_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "README.md"
            path.write_text("intro\n<!-- ACTIVITY:START --><!-- ACTIVITY:END -->\n<!-- PROFILE-STATS:START --><!-- PROFILE-STATS:END -->\noutro", encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets/stats-dark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Stats</text></svg>', encoding="utf-8")
            with patch.object(updater, "fetch_events", return_value=[event()]):
                updater.update_readme(root, "CyPheer1")
                first = path.read_bytes()
                updater.update_readme(root, "CyPheer1")
            self.assertEqual(first, path.read_bytes())
            self.assertIn(b'assets/stats-dark.svg', first)
            self.assertTrue(first.startswith(b"intro"))
            self.assertTrue(first.endswith(b"outro"))

    def test_invalid_cards_never_replace_valid_snapshots(self):
        good = b'<svg xmlns="http://www.w3.org/2000/svg"><text>GitHub stats</text></svg>'
        bad = b'<svg xmlns="http://www.w3.org/2000/svg"><text>Something went wrong</text></svg>'
        self.assertTrue(updater.valid_svg(good))
        for data in (bad, b"<html>error</html>", b"not xml"):
            self.assertFalse(updater.valid_svg(data))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            staging.mkdir()
            candidate = staging / "stats-dark.svg"
            candidate.write_bytes(good)
            updater.promote_cards(staging, root)
            candidate.write_bytes(bad)
            updater.promote_cards(staging, root)
            self.assertEqual((root / "assets/stats-dark.svg").read_bytes(), good)

    def test_readme_has_valid_images_and_markers(self):
        test = self

        class ImageValidator(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "source":
                    test.assertNotIn(",", attributes["srcset"])
                    test.assertEqual(attributes["media"], "(prefers-color-scheme: dark)")
                if tag == "img":
                    test.assertTrue(attributes.get("alt"))

        text = (updater.ROOT / "README.md").read_text(encoding="utf-8")
        ImageValidator().feed(text)
        for name in updater.SECTIONS:
            updater.replace_section(text, name, "test")


if __name__ == "__main__":
    unittest.main()