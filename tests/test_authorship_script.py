import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "check_authorship.sh"
OWNER_EMAIL = "81777066+Nathan-Luevano@users.noreply.github.com"
BOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def commit(repo: Path, email: str, message: str = "test change") -> str:
    (repo / "file").write_text(message, encoding="utf-8")
    subprocess.run(["git", "add", "file"], cwd=repo, check=True)
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        env=environment,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def check(
    repo: Path, head: str, bot_email: str = ""
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "HEAD_SHA": head,
        "ALLOWED_EMAIL": OWNER_EMAIL,
        "ALLOWED_BOT_EMAIL": bot_email,
    }
    return subprocess.run(
        [SCRIPT], cwd=repo, env=environment, capture_output=True, text=True, check=False
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_accepts_owner_commit(repository: Path) -> None:
    result = check(repository, commit(repository, OWNER_EMAIL))

    assert result.returncode == 0


def test_rejects_foreign_commit(repository: Path) -> None:
    result = check(repository, commit(repository, "other@example.com"))

    assert result.returncode == 1
    assert "unexpected author email" in result.stdout


def test_rejects_dependabot_without_scoped_allowance(repository: Path) -> None:
    result = check(repository, commit(repository, BOT_EMAIL))

    assert result.returncode == 1


def test_accepts_dependabot_with_scoped_allowance(repository: Path) -> None:
    result = check(repository, commit(repository, BOT_EMAIL), BOT_EMAIL)

    assert result.returncode == 0
