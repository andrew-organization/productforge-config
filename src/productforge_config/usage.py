"""Report the month's GitHub Actions minutes against the allowance.

`usage(org, allowance, from_day, today)` reads the organisation's billing
summary for the current month (`gh api
organizations/<org>/settings/billing/usage/summary?year=Y&month=M`, the month
named explicitly in UTC), adds up the Actions
items measured in minutes (`grossQuantity`, the minutes consumed before the
plan's included minutes are discounted), projects the month evenly (the
minutes so far, divided by the days elapsed since the month began in UTC,
fractional, and multiplied by the days in the month) and returns the line to print and the exit status: 1 when the
projected total reaches the allowance and the day is at or after `from_day`,
otherwise 0. A summary with no Actions items is "no usage yet", status 0.

It shells out to `gh api`, as github.py does, so it reuses the caller's own
`gh` authentication and stays stdlib-only.
"""

import calendar
import datetime

from productforge_config import github

DEFAULT_ALLOWANCE = 3000
DEFAULT_FROM_DAY = 3

NO_USAGE = "no usage yet"


def actions_minutes(summary: dict) -> float | None:
    """The Actions minutes the summary holds, or None when it holds no Actions item measured in minutes."""
    items = [
        item
        for item in summary.get("usageItems", [])
        if item.get("product") == "Actions" and item.get("unitType") == "minutes"
    ]
    if not items:
        return None
    return sum(item["grossQuantity"] for item in items)


def report(summary: dict, allowance: int, from_day: int, now: datetime.datetime) -> tuple[str, int]:
    """The line to print and the exit status for a billing summary at a given UTC instant."""
    used = actions_minutes(summary)
    if used is None:
        return NO_USAGE, 0
    days = calendar.monthrange(now.year, now.month)[1]
    elapsed = (now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
    projected = used / elapsed * days if elapsed else float("inf") if used else 0.0
    line = (
        f"Actions: {round(used)} of {allowance} minutes used, day {now.day} of {days}, "
        f"projected {round(projected) if elapsed else 'n/a'}"
    )
    return line, 1 if projected >= allowance and now.day >= from_day else 0


def usage(org: str, allowance: int, from_day: int, now: datetime.datetime) -> tuple[str, int]:
    summary = github._get(f"organizations/{org}/settings/billing/usage/summary?year={now.year}&month={now.month}")
    return report(summary, allowance, from_day, now)
