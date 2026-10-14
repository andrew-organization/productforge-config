"""Check or apply the GitHub repository settings every ProductForge
repository shares — settings/github.json, bundled into this package the
same way settings/python.toml and settings/markdownlint.jsonc are (see
_bundled.py).

The file holds the repository fields, the Actions workflow permissions, a
set of named `rulesets` and the `variants` that bundle them. A variant
names the rulesets it applies, may `extends` another (inheriting its
rulesets, bypass actors and default required checks), and may add
`bypass_actors` (written into the ruleset that requires a pull request to
change `main`) or `required_checks` (the status checks that ruleset
requires by default). The variants are `standard` (the default), `product`
(`standard` requiring `ci / ci`, the name a job calling a reusable workflow
reports), `publishes` (`standard` plus the owner as an always-bypass actor)
and `block-only` (blocks rewriting and deleting `main`, requires no check).

`check(repo, ...)` is read-only: it reads the live repository through `gh
api` and returns a line for each setting whose live value differs from the
file, in `"<section>.<key>: <live> -> <wanted>"` shape (or, for a ruleset,
one line per differing field, or one "missing" line when it doesn't exist
yet). It never writes anything. `check_report` returns the same lines plus
the notes that are not differences, such as rules skipped on a plan without
them.

`apply(repo, ...)` applies the file: `gh api -X PATCH repos/<repo>` for the
repository fields, `gh api -X PUT repos/<repo>/actions/permissions/workflow`
for the Actions default workflow permissions, when a product is given
`gh api -X PATCH repos/<repo>/properties/values` for the organisation's
custom property `product`, and the variant's rulesets, each created if no
ruleset of that name exists and updated in place by name if one does, on a
repository of any visibility. Where GitHub answers that the plan offers no
rules for the repository, the rules are reported as skipped with GitHub's
reason and the rest still applies. Each part is read first and written only
when it differs (the repository fields and Actions permissions, the
`product` value, each ruleset's compared fields), and reported `unchanged`
otherwise, so a second run sends no write at all. Only the repository's own
rulesets are matched by name, never one inherited from the organisation.

An unknown variant, or a required check given with `block-only`, is refused
(`GithubError`) before any `gh` call.

Both shell out to `gh api` rather than talking to the GitHub API
directly, so this stays stdlib-only and reuses the caller's own `gh`
authentication — the same reason cli.py's `update` command never fetches
its shared settings over the network itself.
"""

import copy
import json
import re
import subprocess
from typing import Any

from productforge_config._bundled import bundled_settings_dir

# The fields compared (and applied) on a ruleset — everything a ruleset in
# settings/github.json carries; its name is the key it is stored under.
_RULESET_FIELDS = ("target", "enforcement", "bypass_actors", "conditions", "rules")

DEFAULT_VARIANT = "standard"

PRODUCT_PROPERTY = "product"

# GitHub's answer for a private repository on a plan without rules.
_NO_RULES_MARKER = "Upgrade to GitHub"


class GithubError(Exception):
    """A request the command refuses, or a failure it names."""


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


def _get_all(endpoint: str) -> list[Any]:
    """Every item of a paginated list endpoint, across all pages."""
    pages = _run_gh(["api", "--paginate", "--slurp", endpoint]) or []
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def _own_rulesets(repo: str) -> dict[str, dict[str, Any]]:
    """The repository's own rulesets by name: an organisation ruleset
    inherited into it is never listed, so one of the same name is never matched.
    """
    summaries = _get_all(f"repos/{repo}/rulesets?includes_parents=false")
    return {s.get("name"): s for s in summaries if s.get("source_type", "Repository") == "Repository"}


def _get_ruleset(repo: str, ruleset_id: int) -> dict[str, Any]:
    return _get(f"repos/{repo}/rulesets/{ruleset_id}")


def _holds(live: Any, wanted: Any) -> bool:
    """Whether `live` carries everything `wanted` states: every key a
    wanted mapping names, holding its wanted value, and every item of a
    wanted list, in order. GitHub fills in a ruleset's other fields with
    its own defaults once it's saved, so those extra fields aren't drift.
    """
    if isinstance(wanted, dict):
        return isinstance(live, dict) and all(k in live and _holds(live[k], v) for k, v in wanted.items())
    if isinstance(wanted, list):
        return isinstance(live, list) and len(live) == len(wanted) and all(map(_holds, live, wanted))
    return live == wanted


def _canonical_rules(rules: Any) -> Any:
    """`rules` in a canonical order — by type, and a status-check rule's
    checks by context — so the order GitHub returns them in isn't drift.
    """
    if not isinstance(rules, list):
        return rules
    canonical = []
    for rule in rules:
        rule = copy.deepcopy(rule)
        checks = rule.get("parameters", {}).get("required_status_checks") if isinstance(rule, dict) else None
        if isinstance(checks, list):
            rule["parameters"]["required_status_checks"] = sorted(checks, key=lambda c: c.get("context", ""))
        canonical.append(rule)
    return sorted(canonical, key=lambda r: r.get("type", "") if isinstance(r, dict) else "")


def _ruleset_differences(live: dict[str, Any], wanted: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """The compared fields of a ruleset whose live value doesn't hold the
    wanted one, as (field, live, wanted).
    """
    differences = []
    for key in _RULESET_FIELDS:
        wanted_value, live_value = wanted.get(key), live.get(key)
        if key == "bypass_actors":
            wanted_value, live_value = _normalised_bypass_actors(wanted_value), _normalised_bypass_actors(live_value)
        elif key == "rules":
            wanted_value, live_value = _canonical_rules(wanted_value), _canonical_rules(live_value)
        if not _holds(live_value, wanted_value):
            differences.append((key, live_value, wanted_value))
    return differences


def _gh_reason(exc: subprocess.CalledProcessError) -> str:
    """Return GitHub's own message from a failed `gh api` call: its stderr without
    the `gh: ` prefix and the trailing `(HTTP nnn)`.
    """
    text = (exc.stderr or str(exc)).strip()
    text = text.removeprefix("gh: ")
    return re.sub(r"\s*\(HTTP \d+\)$", "", text)


def _rulesets_unavailable(exc: subprocess.CalledProcessError) -> str | None:
    """Return GitHub's reason when `exc` is the answer for a plan without rules, else None."""
    reason = _gh_reason(exc)
    return reason if _NO_RULES_MARKER in reason else None


def _resolve(settings: dict[str, Any], variant: str, required_checks: list[str]) -> dict[str, dict[str, Any]]:
    """The rulesets `variant` applies, by name, as the full bodies to send:
    the variant's inherited bypass actors written into the ruleset that
    requires a pull request, and the required checks (the ones given, else
    the variant's default, else the file's) into each ruleset requiring
    status checks. Refuses an unknown variant, or checks given to a variant
    that requires none, before any `gh` call.
    """
    variants = settings["variants"]
    if variant not in variants:
        raise GithubError(f"unknown variant {variant!r}; choose one of {', '.join(variants)}")

    chain = []
    name: str | None = variant
    while name is not None:
        chain.append(variants[name])
        name = variants[name].get("extends")
    chain.reverse()  # base first, so the variant's own entries win

    ruleset_names: list[str] = []
    bypass_actors: list[dict[str, Any]] = []
    default_checks: list[str] | None = None
    for layer in chain:
        ruleset_names = layer.get("rulesets", ruleset_names)
        bypass_actors = layer.get("bypass_actors", bypass_actors)
        default_checks = layer.get("required_checks", default_checks)

    rulesets = {}
    for ruleset_name in ruleset_names:
        body = copy.deepcopy(settings["rulesets"][ruleset_name])
        body["name"] = ruleset_name
        if any(rule["type"] == "pull_request" for rule in body["rules"]):
            body["bypass_actors"] = copy.deepcopy(bypass_actors)
        rulesets[ruleset_name] = body

    checks_rules = [
        rule for body in rulesets.values() for rule in body["rules"] if rule["type"] == "required_status_checks"
    ]
    if required_checks and not checks_rules:
        raise GithubError(f"--required-check can't be used with the {variant} variant, which requires no check")
    for rule in checks_rules:
        contexts = required_checks or default_checks
        if contexts:
            rule["parameters"]["required_status_checks"] = [{"context": context} for context in contexts]
    return rulesets


def _normalised_bypass_actors(actors: Any) -> Any:
    """`actors` with an `OrganizationAdmin` actor's id dropped: GitHub reads
    it back with `actor_id: null`, so it is compared on its type and mode.
    """
    if not isinstance(actors, list):
        return actors
    return [
        {k: v for k, v in actor.items() if k != "actor_id"} if actor.get("actor_type") == "OrganizationAdmin" else actor
        for actor in actors
    ]


def _live_product(repo: str) -> str | None:
    for entry in _get(f"repos/{repo}/properties/values") or []:
        if entry.get("property_name") == PRODUCT_PROPERTY:
            return entry.get("value")
    return None


def check_report(
    repo: str,
    variant: str = DEFAULT_VARIANT,
    product: str | None = None,
    required_checks: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """The differences between settings/github.json's `variant` and the live
    `repo` (one line each) and the notes that are not differences. Read-only:
    no `gh api` call here writes anything.
    """
    settings = _bundled_github_settings()
    rulesets = _resolve(settings, variant, required_checks or [])
    live_repo = _get(f"repos/{repo}")
    diffs = []
    notes = []

    for key, wanted in settings["repository"].items():
        live = live_repo.get(key)
        if live != wanted:
            diffs.append(f"repository.{key}: {live!r} -> {wanted!r}")

    live_perms = _get(f"repos/{repo}/actions/permissions/workflow") or {}
    for key, wanted in settings["actions_workflow_permissions"].items():
        live = live_perms.get(key)
        if live != wanted:
            diffs.append(f"actions_workflow_permissions.{key}: {live!r} -> {wanted!r}")

    live_product = _live_product(repo)
    if product is None:
        if live_product is None:
            diffs.append(f"{PRODUCT_PROPERTY}: missing")
    elif live_product != product:
        live_text = "missing" if live_product is None else repr(live_product)
        diffs.append(f"{PRODUCT_PROPERTY}: {live_text} -> {product!r}")

    try:
        by_name = _own_rulesets(repo)
    except subprocess.CalledProcessError as exc:
        reason = _rulesets_unavailable(exc)
        if reason is None:
            raise
        notes.append(f"ruleset: skipped ({reason})")
        return diffs, notes

    for name, ruleset in rulesets.items():
        summary = by_name.get(name)
        if summary is None:
            diffs.append(f"ruleset {name!r}: missing")
            continue
        existing = _get_ruleset(repo, summary["id"])
        for key, live, wanted in _ruleset_differences(existing, ruleset):
            diffs.append(f"ruleset {name!r}.{key}: {live!r} -> {wanted!r}")

    return diffs, notes


def check(
    repo: str,
    variant: str = DEFAULT_VARIANT,
    product: str | None = None,
    required_checks: list[str] | None = None,
) -> list[str]:
    """Every setting `variant` states in settings/github.json whose live
    value on `repo` differs, as one line per difference. An empty list
    means `repo` already matches exactly.
    """
    return check_report(repo, variant, product, required_checks)[0]


def apply(
    repo: str,
    variant: str = DEFAULT_VARIANT,
    product: str | None = None,
    required_checks: list[str] | None = None,
) -> list[str]:
    """Apply settings/github.json's `variant` to `repo`, idempotently — safe
    to run repeatedly: each part is written only when it differs from the
    live value, so a second run sends no write. Returns one line per part,
    for reporting. Needs repository admin rights on `repo`.
    """
    settings = _bundled_github_settings()
    rulesets = _resolve(settings, variant, required_checks or [])
    actions = []

    live_repo = _get(f"repos/{repo}")
    if any(live_repo.get(key) != wanted for key, wanted in settings["repository"].items()):
        _patch(f"repos/{repo}", settings["repository"])
        actions.append("repository: applied")
    else:
        actions.append("repository: unchanged")

    live_perms = _get(f"repos/{repo}/actions/permissions/workflow") or {}
    if any(live_perms.get(key) != wanted for key, wanted in settings["actions_workflow_permissions"].items()):
        _put(f"repos/{repo}/actions/permissions/workflow", settings["actions_workflow_permissions"])
        actions.append("actions_workflow_permissions: applied")
    else:
        actions.append("actions_workflow_permissions: unchanged")

    if product is not None:
        if _live_product(repo) == product:
            actions.append(f"{PRODUCT_PROPERTY}: unchanged")
        else:
            body = {"properties": [{"property_name": PRODUCT_PROPERTY, "value": product}]}
            try:
                _patch(f"repos/{repo}/properties/values", body)
            except subprocess.CalledProcessError as exc:
                raise GithubError(
                    f"couldn't set the organisation property {PRODUCT_PROPERTY!r} on {repo}: {_gh_reason(exc)}"
                ) from exc
            actions.append(f"{PRODUCT_PROPERTY}: set to {product!r}")

    try:
        by_name = _own_rulesets(repo)
    except subprocess.CalledProcessError as exc:
        reason = _rulesets_unavailable(exc)
        if reason is None:
            raise
        actions.append(f"ruleset: skipped ({reason})")
        return actions

    for name, ruleset in rulesets.items():
        summary = by_name.get(name)
        if summary is None:
            _post(f"repos/{repo}/rulesets", ruleset)
            actions.append(f"ruleset {name!r}: created")
        elif _ruleset_differences(_get_ruleset(repo, summary["id"]), ruleset):
            _put(f"repos/{repo}/rulesets/{summary['id']}", ruleset)
            actions.append(f"ruleset {name!r}: updated")
        else:
            actions.append(f"ruleset {name!r}: unchanged")

    return actions
