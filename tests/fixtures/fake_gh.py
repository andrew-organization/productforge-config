#!/usr/bin/env python3
"""A stand-in for `gh` that test_github.py installs onto PATH, so
github.py's subprocess calls hit no network at all.

State (a repository's fields, its Actions workflow permissions, its
rulesets and its custom property values) lives in the JSON file named by FAKE_GH_STATE, read and
rewritten on each call so a PATCH/PUT/POST is reflected in the next GET —
letting a test call apply() twice and see the second call change nothing
further. Every call actually made is also appended, as one JSON line
each, to the file named by FAKE_GH_CALLS, so a test can assert on
exactly which `gh api` calls github.py issued.

Environments are held in `environments`, by name, each with its
`deployment_branch_policy` and its branch `policies` (id, name, type); an
environment that doesn't exist answers 404, and one created by a PUT starts
with no policies, as GitHub's does.

State may also set `rulesets_error` or `environments_error` (a message every
rulesets, or environment, call then fails with, as `gh` prints a 403),
`environments_failure` (the same, as a 500) and `no_product_property` (the organisation has
no property `product`, so setting it fails).

A rulesets list GET honours `includes_parents=false` (an inherited ruleset
carries `source_type: "Organization"`) and `--paginate --slurp`.

Understands exactly the shapes github.py's `_run_gh` produces: `api
[-X METHOD] <endpoint> [--input -]`, with a JSON body on stdin when
`--input -` is given.
"""

import json
import os
import sys


def _load_state(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f)


def _log_call(path: str | None, method: str, endpoint: str, body) -> None:
    if not path:
        return
    with open(path, "a") as f:
        f.write(json.dumps({"method": method, "endpoint": endpoint, "body": body}) + "\n")


def _environments(state: dict, state_path: str, method: str, tail: list[str], body, slurp: bool) -> int:
    if state.get("environments_error"):
        print(f"gh: {state['environments_error']} (HTTP 403)", file=sys.stderr)
        return 1
    if state.get("environments_failure"):
        print(f"gh: {state['environments_failure']} (HTTP 500)", file=sys.stderr)
        return 1
    environments = state.setdefault("environments", {})
    environment = environments.get(tail[0])
    if method == "PUT" and len(tail) == 1:
        if environment is None:
            environment = environments[tail[0]] = {"policies": []}
        environment["deployment_branch_policy"] = (body or {}).get("deployment_branch_policy")
        _save_state(state_path, state)
        print(json.dumps({"name": tail[0], "deployment_branch_policy": environment["deployment_branch_policy"]}))
        return 0
    if environment is None:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        return 1
    policies = environment["policies"]
    if len(tail) == 1 and method == "GET":
        print(json.dumps({"name": tail[0], "deployment_branch_policy": environment["deployment_branch_policy"]}))
    elif tail[1:2] == ["deployment-branch-policies"]:
        if len(tail) == 2 and method == "GET":
            page = {"total_count": len(policies), "branch_policies": policies}
            print(json.dumps([page] if slurp else page))
        elif len(tail) == 2 and method == "POST":
            created = {"id": max((p["id"] for p in policies), default=0) + 1, **(body or {})}
            policies.append(created)
            _save_state(state_path, state)
            print(json.dumps(created))
        elif len(tail) == 3 and method == "DELETE":
            environment["policies"] = [p for p in policies if p["id"] != int(tail[2])]
            _save_state(state_path, state)
        else:
            print(f"fake gh: unhandled environment call {method} {tail}", file=sys.stderr)
            return 1
    else:
        print(f"fake gh: unhandled environment call {method} {tail}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] != "api":
        print("fake gh: only `gh api ...` is understood", file=sys.stderr)
        return 1
    rest = args[1:]

    method = "GET"
    if rest and rest[0] == "-X":
        method = rest[1]
        rest = rest[2:]

    paginate = "--paginate" in rest
    slurp = "--slurp" in rest
    rest = [a for a in rest if a not in ("--paginate", "--slurp")]
    raw_endpoint = rest[0]
    endpoint, _, query = raw_endpoint.partition("?")
    rest = rest[1:]

    body = None
    if "--input" in rest:
        idx = rest.index("--input")
        if rest[idx + 1] == "-":
            raw = sys.stdin.read()
            body = json.loads(raw) if raw.strip() else {}

    state_path = os.environ["FAKE_GH_STATE"]
    calls_path = os.environ.get("FAKE_GH_CALLS")
    _log_call(calls_path, method, raw_endpoint, body)
    state = _load_state(state_path)

    parts = endpoint.split("/")
    # parts: "repos", "<owner>", "<name>", ...tail
    tail = parts[3:]

    if not tail:
        if method == "GET":
            print(json.dumps(state["repo"]))
        elif method == "PATCH":
            state["repo"].update(body or {})
            _save_state(state_path, state)
            print(json.dumps(state["repo"]))
        return 0

    if tail == ["properties", "values"]:
        if method == "GET":
            print(json.dumps(state.get("properties", [])))
        elif method == "PATCH":
            if state.get("no_product_property"):
                print("gh: Validation Failed (HTTP 422)", file=sys.stderr)
                return 1
            properties = state.setdefault("properties", [])
            for wanted in (body or {})["properties"]:
                for existing in properties:
                    if existing["property_name"] == wanted["property_name"]:
                        existing["value"] = wanted["value"]
                        break
                else:
                    properties.append(dict(wanted))
            _save_state(state_path, state)
        return 0

    if tail[:3] == ["actions", "permissions", "workflow"]:
        if method == "GET":
            print(json.dumps(state["workflow_permissions"]))
        elif method == "PUT":
            state["workflow_permissions"].update(body or {})
            _save_state(state_path, state)
            print(json.dumps(state["workflow_permissions"]))
        return 0

    if tail[0] == "environments":
        return _environments(state, state_path, method, tail[1:], body, slurp)

    if tail[0] == "rulesets":
        if state.get("rulesets_error"):
            print(f"gh: {state['rulesets_error']} (HTTP 403)", file=sys.stderr)
            return 1
        if len(tail) == 1:
            if method == "GET":
                # Like GitHub, an inherited ruleset is listed unless includes_parents=false
                summaries = [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "source_type": r.get("source_type", "Repository"),
                        "target": r.get("target"),
                        "enforcement": r.get("enforcement"),
                    }
                    for r in state["rulesets"]
                    if "includes_parents=false" not in query or r.get("source_type", "Repository") == "Repository"
                ]
                # --paginate --slurp: one array holding each page's array
                print(json.dumps([summaries] if paginate and slurp else summaries))
            elif method == "POST":
                new_id = max((r["id"] for r in state["rulesets"]), default=0) + 1
                created = dict(body or {})
                created["id"] = new_id
                state["rulesets"].append(created)
                _save_state(state_path, state)
                print(json.dumps(created))
            return 0
        ruleset_id = int(tail[1])
        for r in state["rulesets"]:
            if r["id"] == ruleset_id:
                if method == "GET":
                    print(json.dumps(r))
                elif method == "PUT":
                    r.update(body or {})
                    _save_state(state_path, state)
                    print(json.dumps(r))
                return 0
        print(f"fake gh: no ruleset {ruleset_id}", file=sys.stderr)
        return 1

    print(f"fake gh: unhandled endpoint {endpoint!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
