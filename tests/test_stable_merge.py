from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.update_stable import git, prepare_merge


class StableMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "app").mkdir()
        (self.repo / "app/main.py").write_text("version = 1\n")
        (self.repo / "README.md").write_text("User guide\n")
        (self.repo / "AGENT.md").write_text("Developer instructions\n")
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_app.py").write_text("test fixture\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Initial")
        git(self.repo, "branch", "stable")
        git(self.repo, "switch", "-qc", "dev")
        (self.repo / "app/main.py").write_text("version = 2\n")
        git(self.repo, "commit", "-qam", "Application change")

    def test_merge_preserves_code_and_history_but_keeps_developer_files_in_dev(self) -> None:
        target = git(self.repo, "rev-parse", "stable").decode().strip()
        source = git(self.repo, "rev-parse", "dev").decode().strip()
        commit = prepare_merge(self.repo, "dev", "stable")
        self.assertEqual(git(self.repo, "show", commit + ":app/main.py"), b"version = 2\n")
        files = git(self.repo, "ls-tree", "-r", "--name-only", commit).decode().splitlines()
        self.assertIn("README.md", files)
        self.assertNotIn("AGENT.md", files)
        self.assertNotIn("tests/test_app.py", files)
        parents = git(self.repo, "show", "-s", "--format=%P", commit).decode().split()
        self.assertEqual(parents, [target, source])
        self.assertEqual(git(self.repo, "show", "dev:AGENT.md"), b"Developer instructions\n")
        self.assertEqual(git(self.repo, "rev-parse", "stable").decode().strip(), target)

    def test_repeated_merge_without_runtime_changes_is_a_noop(self) -> None:
        commit = prepare_merge(self.repo, "dev", "stable")
        git(self.repo, "update-ref", "refs/heads/stable", commit)
        self.assertIsNone(prepare_merge(self.repo, "dev", "stable"))
        (self.repo / "AGENT.md").write_text("Updated developer instructions\n")
        git(self.repo, "commit", "-qam", "Developer documentation")
        self.assertIsNone(prepare_merge(self.repo, "dev", "stable"))

    def test_independent_stable_fix_cannot_be_overwritten(self) -> None:
        git(self.repo, "switch", "-q", "stable")
        (self.repo / "app/main.py").write_text("version = 'stable fix'\n")
        git(self.repo, "commit", "-qam", "Independent stable fix")
        git(self.repo, "switch", "-q", "dev")
        with self.assertRaisesRegex(ValueError, "independent change"):
            prepare_merge(self.repo, "dev", "stable")
