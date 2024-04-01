"""The working directory of the factory data GUI: a clone of the artifacts repo.

Every CSV and every generated file lives in one Git repository, so a record
of what was produced (and from which input) is kept with the firmware builds:

    <repo>/factory/factory-data-gui/csv/   sys.csv, cal.csv, diag.csv  (text)
    <repo>/factory/factory-data-gui/out/   <id>.sys .cal .diag, manifest.csv

The generated files are Git LFS objects (``.gitattributes`` of the repo); the
CSVs and the manifest stay plain text so their diffs are readable.  Nothing
here needs Qt.  Pushing is never automatic: :func:`commit` only commits, and
:func:`push` runs when the user asks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .generators import GENERATORS
from .rules import RULES_NAME
from .rules import SAMPLE as RULES_SAMPLE
from .units import REGISTRY_NAME

ARTIFACTS_URL = "git@github.com:soccentric-stm32/artifacts.git"
SUBDIR = Path("factory") / "factory-data-gui"
PRODUCTS_DIR = "products"
WORKSPACE_DIR = "workspace"       # in the app folder; holds the clone
REPO_NAME = "artifacts"
ENV_REPO = "FACTORY_DATA_ARTIFACTS"
ENV_URL = "FACTORY_DATA_ARTIFACTS_URL"
GIT_TIMEOUT = 300


def _check_name(name: str) -> None:
    """A product or batch name is one safe folder name."""
    if not name or name in (".", "..") or not all(
            c.isalnum() or c in "-_." for c in name):
        raise WorkspaceError(
            f"{name!r} is not a valid name; use letters, digits, - _ and .")


class WorkspaceError(Exception):
    """A git or layout problem the user can read and act on."""


@dataclass(frozen=True)
class Status:
    branch: str
    changed: int          # files under the factory data folder with changes
    ahead: int | None     # commits not pushed; None when there is no upstream
    behind: int | None

    def summary(self) -> str:
        parts = [self.branch or "detached",
                 "clean" if not self.changed else f"{self.changed} changed"]
        if self.ahead is None:
            parts.append("no upstream")
        else:
            if self.ahead:
                parts.append(f"{self.ahead} to push")
            if self.behind:
                parts.append(f"{self.behind} behind")
        return " · ".join(parts)


def workspace_dir() -> Path:
    """The ``workspace/`` folder in the app folder (relative form)."""
    return Path(WORKSPACE_DIR)


def ensure_workspace_dir() -> Path:
    """Create ``workspace/`` in the app folder when it does not exist."""
    path = paths.resolve(workspace_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_repo_path() -> Path:
    """Where the artifacts clone lives, relative to the app folder.

    ``$FACTORY_DATA_ARTIFACTS``, else ``workspace/artifacts`` inside the app
    folder.  The result is always relative so it can be stored in QSettings
    and shown in the window without a machine specific prefix.
    :class:`Workspace` resolves it when it touches disk.
    """
    env = os.environ.get(ENV_REPO)
    if env:
        return paths.shown(Path(env).expanduser())
    return workspace_dir() / REPO_NAME


def default_url() -> str:
    return os.environ.get(ENV_URL, ARTIFACTS_URL)


def _git(args: list[str], cwd: Path | None = None,
         timeout: int = GIT_TIMEOUT) -> str:
    """Run git without ever waiting for a prompt; return its output."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
            timeout=timeout)
    except FileNotFoundError:
        raise WorkspaceError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git {args[0]} timed out") from None
    if result.returncode:
        text = (result.stderr or result.stdout).strip()
        raise WorkspaceError(f"git {args[0]} failed: {text}")
    return result.stdout


class Workspace:
    def __init__(self, root: Path, url: str | None = None,
                 scope: Path | str | None = None) -> None:
        # root is kept as given (the default is relative); _root is the
        # resolved form used for every git and file operation.
        self.root = Path(root).expanduser()
        self._root = paths.resolve(self.root)
        self.url = url or default_url()
        # "" is the flat layout (csv/ and out/ directly under the tool's
        # folder); "products/<product>/<batch>" keeps one batch per folder.
        self.scope = Path(scope) if scope else Path()

    # ---- layout ---------------------------------------------------------

    @property
    def resolved_root(self) -> Path:
        return self._root

    @property
    def base(self) -> Path:
        return self.root / SUBDIR / self.scope

    @property
    def base_path(self) -> Path:
        return self._root / SUBDIR / self.scope

    @property
    def tool_path(self) -> Path:
        """The factory-data-gui folder of the clone, whatever the scope."""
        return self._root / SUBDIR

    @property
    def units_path(self) -> Path:
        """The unit registry: one for the whole repository."""
        return self.tool_path / REGISTRY_NAME

    @property
    def rules_path(self) -> Path:
        """Rules of the selected product (or of the flat layout)."""
        if self.product:
            return self.tool_path / PRODUCTS_DIR / self.product / RULES_NAME
        return self.tool_path / RULES_NAME

    # ---- products and batches ------------------------------------------

    @property
    def product(self) -> str:
        parts = self.scope.parts
        return parts[1] if len(parts) == 3 and parts[0] == PRODUCTS_DIR else ""

    @property
    def batch(self) -> str:
        parts = self.scope.parts
        return parts[2] if len(parts) == 3 and parts[0] == PRODUCTS_DIR else ""

    def products(self) -> list[str]:
        root = self.tool_path / PRODUCTS_DIR
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def batches(self, product: str) -> list[str]:
        root = self.tool_path / PRODUCTS_DIR / product
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def select(self, product: str, batch: str) -> None:
        """Work in one batch of one product ("" and "" for the flat layout)."""
        if product and batch:
            _check_name(product)
            _check_name(batch)
            self.scope = Path(PRODUCTS_DIR) / product / batch
        else:
            self.scope = Path()

    def create_batch(self, product: str, batch: str) -> list[str]:
        """Create (or open) a batch, with the product's rules and sample CSVs."""
        _check_name(product)
        _check_name(batch)
        self.select(product, batch)
        made = self.ensure_layout()
        rules = self.rules_path
        if not rules.exists():
            rules.parent.mkdir(parents=True, exist_ok=True)
            rules.write_text(RULES_SAMPLE, encoding="utf-8", newline="\n")
            made.append(f"{PRODUCTS_DIR}/{product}/{RULES_NAME}")
        return made

    @property
    def csv_dir(self) -> Path:
        return self.base / "csv"

    @property
    def csv_dir_path(self) -> Path:
        return self.base_path / "csv"

    @property
    def c_dir_path(self) -> Path:
        """Where the generated firmware C API is written (plain text)."""
        return self.base_path / "c"

    @property
    def out_dir(self) -> Path:
        return self.base / "out"

    @property
    def out_dir_path(self) -> Path:
        return self.base_path / "out"

    def csv_path(self, kind: str) -> Path:
        return self.csv_dir / f"{kind}.csv"

    def csv_path_path(self, kind: str) -> Path:
        return self.csv_dir_path / f"{kind}.csv"

    @property
    def cloned(self) -> bool:
        return (self._root / ".git").exists()

    def ensure_layout(self) -> list[str]:
        """Create csv/ and out/ and seed the sample CSVs that are missing.

        Existing CSVs are never touched.  Returns what was created.
        """
        made: list[str] = []
        self.csv_dir_path.mkdir(parents=True, exist_ok=True)
        self.out_dir_path.mkdir(parents=True, exist_ok=True)
        keep = self.out_dir_path / ".gitkeep"
        if not any(self.out_dir_path.iterdir()):
            keep.touch()
        for kind, gen in GENERATORS.items():
            path = self.csv_path_path(kind)
            if not path.exists():
                path.write_text(gen.sample(), encoding="utf-8")
                made.append(f"csv/{path.name}")
        return made

    # ---- git ------------------------------------------------------------

    def _lfs_tracked(self) -> bool:
        attrs = self._root / ".gitattributes"
        return attrs.is_file() and "filter=lfs" in attrs.read_text()

    def _need_lfs(self) -> None:
        try:
            _git(["lfs", "version"])
        except WorkspaceError:
            raise WorkspaceError(
                "git-lfs is not installed; the repository stores the "
                "generated files with Git LFS. Install it and run "
                "'git lfs install'.") from None

    def clone(self) -> list[str]:
        """Clone the repository into the workspace, then bring it current.

        Refuses when the clone already exists (use :meth:`sync` to pull).
        """
        if self.cloned:
            raise WorkspaceError(
                f"{paths.display(self.root)} is already cloned; use Pull")
        return self.sync()

    def sync(self) -> list[str]:
        """Clone the repository when missing, otherwise fast-forward it.

        Returns progress lines.  A dirty tree or a branch without upstream is
        left alone; it is reported, not an error.
        """
        log: list[str] = []
        if not self.cloned:
            if self._root.exists() and any(self._root.iterdir()):
                raise WorkspaceError(
                    f"{paths.display(self.root)} exists and is not a git "
                    "repository")
            self._root.parent.mkdir(parents=True, exist_ok=True)
            _git(["clone", self.url, str(self._root)])
            log.append(f"Cloned {self.url} into {paths.display(self.root)}")
        if self._lfs_tracked():
            self._need_lfs()
            self._git("lfs", "install", "--local")
        if self._git("status", "--porcelain").strip():
            log.append("Local changes present; not pulling")
            return log
        try:
            self._git("rev-parse", "--abbrev-ref", "@{u}")
        except WorkspaceError:
            log.append("No upstream branch; not pulling")
            return log
        self._git("fetch", "--quiet")
        before = self._git("rev-parse", "HEAD").strip()
        self._git("merge", "--ff-only", "--quiet", "@{u}")
        if self._git("rev-parse", "HEAD").strip() != before:
            log.append("Pulled new commits")
        if self._lfs_tracked():
            self._git("lfs", "pull")
        log.append(f"Artifacts repository up to date at "
                   f"{paths.display(self.root)}")
        return log

    def _git(self, *args: str) -> str:
        return _git(list(args), self._root)

    def status(self) -> Status:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        changed = len([
            line for line in self._git(
                "status", "--porcelain", "--untracked-files=all", "--",
                str(SUBDIR)).splitlines()
            if line.strip()])
        ahead = behind = None
        try:
            counts = self._git("rev-list", "--left-right", "--count",
                               "@{u}...HEAD").split()
            behind, ahead = int(counts[0]), int(counts[1])
        except (WorkspaceError, ValueError, IndexError):
            pass
        return Status(branch, changed, ahead, behind)

    def commit(self, message: str) -> str:
        """Commit the factory data folder only; return a one-line result."""
        if not message.strip():
            raise WorkspaceError("A commit message is required")
        if self._lfs_tracked():
            self._need_lfs()
        self._git("add", "-A", "--", str(SUBDIR))
        staged = self._git("diff", "--cached", "--name-only", "--",
                           str(SUBDIR)).split()
        if not staged:
            return "Nothing to commit"
        self._git("commit", "-q", "-m", message, "--only", "--",
                  str(SUBDIR))
        return (f"Committed {len(staged)} file(s): "
                f"{self._git('rev-parse', '--short', 'HEAD').strip()}")

    def push(self) -> str:
        """Push the current branch; LFS objects go up with the pre-push hook."""
        state = self.status()
        branch = state.branch
        if state.ahead is None:
            self._git("push", "--set-upstream", "origin", branch)
        else:
            self._git("push")
        return f"Pushed {branch} to origin"


def have_git() -> bool:
    return shutil.which("git") is not None
