"""Adds the closing *To take this release* section to a release's notes.

The release workflow runs this by path, in the caller's checkout, after a run that created a version:

    python3 release_notes.py --previous <the earlier stable tag, or empty> --tag <the new tag>

It reads the commit messages the version holds, which are the squashed pull requests' titles and
descriptions, takes the steps section from each, and appends one closing section to the release's
notes. It uses the standard library, `git` and `gh` alone, so the workflow needs no install.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TITLE = "To take this release"
NOTES_HEADING = f"### {TITLE}"
NOTHING_TO_DO = "Nothing to do to take this release."
FIRST_RELEASE = "First release."

HEADING = re.compile(rf"^ {{0,3}}(#{{1,6}})[ \t]+{re.escape(TITLE)}[ \t]*(?:#+[ \t]*)?:?[ \t]*$", re.IGNORECASE)
ANY_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:")
BREAKING_NOTES = re.compile(r"^#{1,6}[ \t]+.*BREAKING CHANGES", re.MULTILINE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
TRAILER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]+\S")
ATTRIBUTION = re.compile(r"^(?:🤖 Generated with |https://claude\.ai/code/session_)")
TRAILER_KEYS = frozenset(
    {
        "co-authored-by",
        "signed-off-by",
        "reviewed-by",
        "acked-by",
        "tested-by",
        "reported-by",
        "suggested-by",
        "helped-by",
        "cc",
        "claude-session",
    }
)


class Refusal(Exception):
    """A reason to stop before anything is written."""


def without_trailers(lines: list[str]) -> list[str]:
    """The lines up to the trailer and attribution lines a squashed commit ends with, which are no part of a section."""
    end = len(lines)
    while end and (not lines[end - 1].strip() or _is_trailer(lines[end - 1]) or ATTRIBUTION.match(lines[end - 1])):
        end -= 1
    return lines[:end]


def _is_trailer(line: str) -> bool:
    match = TRAILER.match(line)
    return bool(match) and match.group(1).lower() in TRAILER_KEYS


def steps_section(message: str) -> str | None:
    """The text of the first steps section in one commit message's body, or None."""
    lines = without_trailers(COMMENT.sub("", message).splitlines())
    fence: tuple[str, int] | None = None
    level = 0
    section: list[str] = []
    for line in lines:
        opening = FENCE.match(line)
        if fence is not None:
            if (
                opening
                and opening.group(1)[0] == fence[0]
                and len(opening.group(1)) >= fence[1]
                and not line.strip(" `~")
            ):
                fence = None
        elif opening:
            fence = (opening.group(1)[0], len(opening.group(1)))
        elif not level:
            heading = HEADING.match(line)
            if heading:
                level = len(heading.group(1))
            continue
        elif BREAKING_FOOTER.match(line):
            break
        else:
            heading = ANY_HEADING.match(line)
            if heading and len(heading.group(1)) <= level:
                break
        if level:
            section.append(line)
    text = "\n".join(section).strip()
    return text or None


def run(*command: str) -> str:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise Refusal(f"`{' '.join(command)}` failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def commits(previous: str, tag: str) -> list[tuple[str, str]]:
    """Each commit of the version as (subject, body), oldest first."""
    log = run("git", "log", "--reverse", "--format=%s%x00%b%x01", f"{previous}..{tag}" if previous else tag)
    found = []
    for entry in log.split("\x01"):
        if entry.strip():
            subject, _, body = entry.partition("\x00")
            found.append((subject.strip(), body))
    return found


def closing_section(previous: str, tag: str, notes: str) -> str | None:
    """The section the notes should end with, or None where a breaking change already says it."""
    if not previous:
        return f"{NOTES_HEADING}\n\n{FIRST_RELEASE}"
    parts = []
    for subject, body in commits(previous, tag):
        steps = steps_section(body)
        if steps:
            parts.append(f"**{subject}**\n\n{steps}")
    if parts:
        return f"{NOTES_HEADING}\n\n" + "\n\n".join(parts)
    if BREAKING_NOTES.search(notes):
        return None
    return f"{NOTES_HEADING}\n\n{NOTHING_TO_DO}"


def add_to_notes(previous: str, tag: str) -> str:
    """Appends the closing section to the release `tag`'s notes and says what it did."""
    try:
        run("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    except Refusal as refusal:
        raise Refusal(f"the tag {tag} does not exist") from refusal
    try:
        notes = json.loads(run("gh", "release", "view", tag, "--json", "body"))["body"] or ""
    except Refusal as refusal:
        raise Refusal(f"no GitHub release exists for {tag}: {refusal}") from refusal
    if re.search(rf"^{re.escape(NOTES_HEADING)}[ \t\r]*$", notes, re.MULTILINE):
        return f"{tag}'s notes already hold the section; nothing changed"
    section = closing_section(previous, tag, notes)
    if section is None:
        return f"{tag}'s notes state a breaking change and no steps; nothing added"
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "notes.md"
        path.write_text(f"{notes.rstrip()}\n\n{section}\n", encoding="utf-8")
        run("gh", "release", "edit", tag, "--notes-file", str(path))
    return f"added the section to {tag}'s notes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--previous", default="", help="the earlier stable tag; empty for a first release")
    parser.add_argument("--tag", required=True, help="the tag of the release to add the section to")
    arguments = parser.parse_args(argv)
    try:
        print(add_to_notes(arguments.previous, arguments.tag))
    except Refusal as refusal:
        print(f"release_notes: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
