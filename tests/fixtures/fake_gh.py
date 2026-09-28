#!/usr/bin/env python3
"""A stand-in for `gh` that test_github.py installs onto PATH, so
github.py's subprocess calls hit no network at all.

State (a repository's fields, its Actions workflow permissions, and its
rulesets) lives in the JSON file named by FAKE_GH_STATE, read and
rewritten on each call so a PATCH/PUT/POST is reflected in the next GET —
letting a test call apply() twice and see the second call change nothing
further. Every call actually made is also appended, as one JSON line
each, to the file named by FAKE_GH_CALLS, so a test can assert on
exactly which `gh api` calls github.py issued.

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

    endpoint = rest[0]
    rest = rest[1:]

    body = None
    if "--input" in rest:
        idx = rest.index("--input")
        if rest[idx + 1] == "-":
            raw = sys.stdin.read()
            body = json.loads(raw) if raw.strip() else {}

    state_path = os.environ["FAKE_GH_STATE"]
    calls_path = os.environ.get("FAKE_GH_CALLS")
    _log_call(calls_path, method, endpoint, body)
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

    if tail[:3] == ["actions", "permissions", "workflow"]:
        if method == "GET":
            print(json.dumps(state["workflow_permissions"]))
        elif method == "PUT":
            state["workflow_permissions"].update(body or {})
            _save_state(state_path, state)
            print(json.dumps(state["workflow_permissions"]))
        return 0

    if tail[0] == "rulesets":
        if len(tail) == 1:
            if method == "GET":
                summaries = [
                    {"id": r["id"], "name": r["name"], "target": r.get("target"), "enforcement": r.get("enforcement")}
                    for r in state["rulesets"]
                ]
                print(json.dumps(summaries))
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
