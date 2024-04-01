"""Tests of the artifacts-clone workspace, using a local bare repository."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

from hwval_factory.core.workspace import SUBDIR, Workspace, WorkspaceError

LFS = subprocess.run(["git", "lfs", "version"],
                     capture_output=True).returncode == 0
needs_lfs = pytest.mark.skipif(not LFS, reason="git-lfs is not installed")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


def make_remote(tmp_path: pathlib.Path) -> pathlib.Path:
    """A bare 'origin' holding one commit with the LFS .gitattributes."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "master", cwd=seed)
    (seed / ".gitattributes").write_text(
        "*.sys filter=lfs diff=lfs merge=lfs -text\n"
        "*.cal filter=lfs diff=lfs merge=lfs -text\n"
        "*.diag filter=lfs diff=lfs merge=lfs -text\n")
    git("add", ".", cwd=seed)
    git("commit", "-q", "-m", "init", cwd=seed)
    bare = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(bare), cwd=tmp_path)
    return bare


@pytest.fixture(autouse=True)
def identity(monkeypatch, tmp_path):
    for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                       ("GIT_COMMITTER_NAME", "t"),
                       ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)


@needs_lfs
def test_clone_seed_commit_push(tmp_path):
    remote = make_remote(tmp_path)
    ws = Workspace(tmp_path / "artifacts", str(remote))
    assert not ws.cloned
    log = ws.sync()
    assert ws.cloned and "Cloned" in log[0]

    made = ws.ensure_layout()
    assert sorted(made) == ["csv/cal.csv", "csv/diag.csv", "csv/sys.csv"]
    assert ws.ensure_layout() == []          # never overwrites
    (ws.csv_path("sys")).write_text(ws.csv_path("sys").read_text() + "# mine\n")
    assert ws.ensure_layout() == []
    assert ws.csv_path("sys").read_text().endswith("# mine\n")

    (ws.out_dir / "SC0001.sys").write_bytes(b"x" * 64)
    state = ws.status()
    assert state.changed >= 3 and state.ahead == 0

    assert ws.commit("Add data").startswith("Committed")
    assert ws.status().changed == 0 and ws.status().ahead == 1
    assert ws.commit("again") == "Nothing to commit"

    # the generated file is stored as an LFS pointer, the CSV as text
    blob = subprocess.run(
        ["git", "show", f"HEAD:{SUBDIR.as_posix()}/out/SC0001.sys"],
        cwd=ws.root, capture_output=True, text=True).stdout
    assert blob.startswith("version https://git-lfs")

    ws.push()
    assert ws.status().ahead == 0


@needs_lfs
def test_sync_fast_forwards_and_leaves_dirty_trees(tmp_path):
    remote = make_remote(tmp_path)
    one = Workspace(tmp_path / "one", str(remote))
    two = Workspace(tmp_path / "two", str(remote))
    one.sync()
    two.sync()
    one.ensure_layout()
    one.commit("from one")
    one.push()
    assert "Pulled new commits" in two.sync()
    assert two.csv_path("sys").is_file()

    (two.csv_dir / "sys.csv").write_text("changed\n")
    assert "Local changes present; not pulling" in two.sync()


def test_not_a_repository(tmp_path):
    target = tmp_path / "folder"
    target.mkdir()
    (target / "file").write_text("x")
    with pytest.raises(WorkspaceError):
        Workspace(target, "unused").sync()


def test_commit_needs_a_message(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).commit("  ")
