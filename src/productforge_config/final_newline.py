"""The end-of-file-fixer hook every ProductForge repository shares.

Every text file ends in exactly one newline, however it got into the
repository — written by a person, an editor or a tool such as Obsidian that
saves without one. The fix itself is pre-commit-hooks' own end-of-file-fixer.

What happens after a fix depends on where the hook runs. Inside a commit
(git gives its hooks GIT_INDEX_FILE) the fixed files are staged again and the
commit goes ahead with them, so nobody has to re-run it. In CI, or run by
hand, the fix is left in the working tree and the hook fails, as any fixer
does — so CI still refuses a file that reached the repository some other way.
"""

import argparse
import os
import subprocess
from collections.abc import Sequence

from pre_commit_hooks.end_of_file_fixer import fix_file


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="productforge-final-newline")
    parser.add_argument("filenames", nargs="*")
    args = parser.parse_args(argv)

    fixed = []
    for filename in args.filenames:
        with open(filename, "rb+") as file_obj:
            if fix_file(file_obj):
                fixed.append(filename)
    for filename in fixed:
        print(f"Fixing {filename}")

    if not fixed:
        return 0
    if os.environ.get("GIT_INDEX_FILE") and not os.environ.get("CI"):
        subprocess.run(["git", "add", "--", *fixed], check=True)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
