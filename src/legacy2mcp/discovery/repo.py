"""
Resolve a `legacy2mcp init` source: either a GitHub (or any git) URL, cloned
shallowly into a temp dir, or a local path used as-is.

The GitHub token (if any) is read from an environment variable name, never
accepted as a raw CLI argument or written to disk -- it's spliced into the
clone URL only in-memory and is never logged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class RepoError(Exception):
    """Raised when a repo can't be resolved (clone failure, bad path, ...)."""


def _with_token(url: str, token: str) -> str:
    if url.startswith("https://") and "@" not in url:
        return url.replace("https://", f"https://{token}@", 1)
    return url


def clone(url: str, token_env: str | None = None) -> Path:
    """Shallow-clone `url` into a new temp dir and return its path.

    Caller is responsible for calling `cleanup()` on the returned path when
    done (it is a temp dir, never the user's working directory).
    """
    clone_url = url
    if token_env:
        token = os.environ.get(token_env)
        if token is None:
            raise RepoError(
                f"Environment variable '{token_env}' is not set (required for the GitHub token)."
            )
        clone_url = _with_token(url, token)

    dest = Path(tempfile.mkdtemp(prefix="legacy2mcp-init-"))
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", clone_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        cleanup(dest)
        raise RepoError("git is not installed or not on PATH -- required to clone a repo URL.") from exc
    except subprocess.CalledProcessError as exc:
        cleanup(dest)
        # exc.stderr may contain the token if auth failed partway -- strip it.
        stderr = (exc.stderr or "").replace(clone_url, url)
        raise RepoError(f"Failed to clone '{url}': {stderr.strip() or exc}") from exc

    return dest


def resolve_local(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoError(f"Local path '{path}' is not a directory.")
    return resolved


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
