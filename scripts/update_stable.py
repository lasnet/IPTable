"""Merge a tested dev snapshot into stable while keeping developer files in dev."""

import argparse
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


DEV_ONLY = (
    "AGENT.md", "DEVELOPMENT.md", "requirements-dev.txt", "tests/", ".github/",
    "scripts/update_stable.py", "scripts/smoke_test.py",
)


def developer_file(path: str) -> bool:
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in DEV_ONLY)


def git(repo: Path, *args: str, data: bytes | None = None, env: dict | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=data, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout


def prepare_merge(repo: Path, source: str, target: str) -> str | None:
    source_sha = git(repo, "rev-parse", "--verify", source + "^{commit}").decode().strip()
    target_sha = git(repo, "rev-parse", "--verify", target + "^{commit}").decode().strip()
    base = git(repo, "merge-base", source_sha, target_sha).decode().strip()
    changed = git(repo, "diff", "--name-only", "-z", base, target_sha).split(b"\0")
    for raw_path in filter(None, changed):
        path = os.fsdecode(raw_path)
        if not developer_file(path) and git(repo, "diff", source_sha, target_sha, "--", path):
            raise ValueError(f"Stable has an independent change in {path}. Bring it into dev first.")

    with TemporaryDirectory(prefix="iptable-merge-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        git(repo, "read-tree", source_sha, env=env)
        files = git(repo, "ls-files", "-z", env=env).split(b"\0")
        excluded = [path for path in files if path and developer_file(os.fsdecode(path))]
        if excluded:
            git(repo, "update-index", "--force-remove", "-z", "--stdin", data=b"\0".join(excluded) + b"\0", env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    if tree == git(repo, "rev-parse", target_sha + "^{tree}").decode().strip():
        return None
    parents = ["-p", target_sha]
    if source_sha != target_sha:
        parents.extend(["-p", source_sha])
    return git(repo, "commit-tree", tree, *parents, "-m", f"Merge dev into stable ({source_sha[:12]})").decode().strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true", help="Publish the prepared merge to origin/stable")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        if git(repo, "status", "--porcelain").strip():
            raise ValueError("Commit or set aside local changes before updating stable.")
        git(repo, "fetch", "origin", "dev", "stable")
        commit = prepare_merge(repo, "origin/dev", "origin/stable")
        if commit is None:
            print("Stable already contains the current application from dev.")
            return
        print(git(repo, "diff", "--stat", "origin/stable", commit).decode(), end="")
        if args.push:
            git(repo, "push", "origin", f"{commit}:refs/heads/stable")
            print(f"Stable updated: {commit}")
        else:
            print("Preview only. Use --push after testing dev to publish this merge.")
    except (ValueError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.decode(errors="replace") if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        parser.exit(1, detail + "\n")


if __name__ == "__main__":
    main()
