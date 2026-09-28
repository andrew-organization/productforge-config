"""Check or apply the GitHub repository settings every ProductForge
repository shares — settings/github.json, bundled into this package the
same way settings/python.toml and settings/markdownlint.jsonc are (see
_bundled.py).

`check(repo)` is read-only: it reads the live repository through `gh api`
and returns a line for each setting whose live value differs from the
file, in `"<section>.<key>: <live> -> <wanted>"` shape (or, for the
ruleset, one line per differing field, or one "missing" line when it
doesn't exist yet). It never writes anything.

`apply(repo)` applies the file: `gh api -X PATCH repos/<repo>` for the
repository fields, `gh api -X PUT repos/<repo>/actions/permissions/workflow`
for the Actions default workflow permissions, and — only when the live
repository is public — the ruleset requiring a pull request to change
main, created if it doesn't exist yet or updated in place by name if it
does. Every call sends the file's full values rather than only the ones
that differ, so running it twice in a row is a no-op the second time:
idempotent by construction, not by pre-checking a diff first.

Both shell out to `gh api` rather than talking to the GitHub API
directly, so this stays stdlib-only and reuses the caller's own `gh`
authentication — the same reason cli.py's `update` command never fetches
its shared settings over the network itself.
"""

import json
import subprocess
from typing import Any

from productforge_config._bundled import bundled_settings_dir

# The fields compared (and applied) on a ruleset — everything settings/github.json's
# "ruleset_public_only" carries except its own "name", which identifies it rather
# than describing a value to converge on.
_RULESET_FIELDS = ("target", "enforcement", "bypass_actors", "conditions", "rules")


def _bundled_github_settings() -> dict[str, Any]:
    path = bundled_settings_dir() / "github.json"
    return json.loads(path.read_text())


def _run_gh(args: list[str], input_data: dict[str, Any] | None = None) -> Any:
    """Run `gh <args>`, feeding `input_data` as a JSON body on stdin when
    given, and return its stdout parsed as JSON (or None for an empty
    response, e.g. a 204 from a PUT that returns no body).
    """
    result = subprocess.run(
        ["gh", *args],
        input=json.dumps(input_data) if input_data is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    text = result.stdout.strip()
    return json.loads(text) if text else None


def _get(endpoint: str) -> Any:
    return _run_gh(["api", endpoint])


def _patch(endpoint: str, body: dict[str, Any]) -> Any:
    return _run_gh(["api", "-X", "PATCH", endpoint, "--input", "-"], body)


def _put(endpoint: str, body: dict[str, Any]) -> Any:
    return _run_gh(["api", "-X", "PUT", endpoint, "--input", "-"], body)


def _post(endpoint: str, body: dict[str, Any]) -> Any:
    return _run_gh(["api", "-X", "POST", endpoint, "--input", "-"], body)


def _find_ruleset_summary(repo: str, name: str) -> dict[str, Any] | None:
    """The `{id, name, ...}` summary of the named ruleset from `GET
    repos/<repo>/rulesets`'s list — which doesn't carry the full
    conditions/rules a summary would need to be diffed against, only
    enough to find the ruleset's id (see `_get_ruleset` for the rest).
    """
    for summary in _get(f"repos/{repo}/rulesets") or []:
        if summary.get("name") == name:
            return summary
    return None


def _get_ruleset(repo: str, ruleset_id: int) -> dict[str, Any]:
    return _get(f"repos/{repo}/rulesets/{ruleset_id}")


def _is_public(repo: str) -> tuple[bool, dict[str, Any]]:
    live = _get(f"repos/{repo}")
    return not live.get("private", True), live


def check(repo: str) -> list[str]:
    """Every setting in settings/github.json whose live value on `repo`
    differs from the file, as one line per difference. An empty list
    means `repo` already matches the file exactly. Read-only: no `gh api`
    call here writes anything.
    """
    settings = _bundled_github_settings()
    is_public, live_repo = _is_public(repo)
    diffs = []

    for key, wanted in settings["repository"].items():
        live = live_repo.get(key)
        if live != wanted:
            diffs.append(f"repository.{key}: {live!r} -> {wanted!r}")

    live_perms = _get(f"repos/{repo}/actions/permissions/workflow") or {}
    for key, wanted in settings["actions_workflow_permissions"].items():
        live = live_perms.get(key)
        if live != wanted:
            diffs.append(f"actions_workflow_permissions.{key}: {live!r} -> {wanted!r}")

    if is_public:
        ruleset = settings["ruleset_public_only"]
        name = ruleset["name"]
        summary = _find_ruleset_summary(repo, name)
        if summary is None:
            diffs.append(f"ruleset {name!r}: missing")
        else:
            existing = _get_ruleset(repo, summary["id"])
            for key in _RULESET_FIELDS:
                wanted = ruleset.get(key)
                live = existing.get(key)
                if live != wanted:
                    diffs.append(f"ruleset {name!r}.{key}: {live!r} -> {wanted!r}")

    return diffs


def apply(repo: str) -> list[str]:
    """Apply settings/github.json to `repo`, idempotently — safe to run
    repeatedly, since every call sends the file's full values rather than
    only a computed diff. Returns one line per action taken, for
    reporting. Needs repository admin rights on `repo`.
    """
    settings = _bundled_github_settings()
    is_public, _ = _is_public(repo)
    actions = []

    _patch(f"repos/{repo}", settings["repository"])
    actions.append("repository: applied")

    _put(f"repos/{repo}/actions/permissions/workflow", settings["actions_workflow_permissions"])
    actions.append("actions_workflow_permissions: applied")

    if not is_public:
        actions.append("ruleset: skipped (private repository — branch rules aren't on the Free plan)")
        return actions

    ruleset = settings["ruleset_public_only"]
    name = ruleset["name"]
    summary = _find_ruleset_summary(repo, name)
    if summary is None:
        _post(f"repos/{repo}/rulesets", ruleset)
        actions.append(f"ruleset {name!r}: created")
    else:
        _put(f"repos/{repo}/rulesets/{summary['id']}", ruleset)
        actions.append(f"ruleset {name!r}: updated")

    return actions
