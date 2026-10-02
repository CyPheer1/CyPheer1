import argparse
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("ACTIVITY", "PROFILE-STATS")
CARD_NAMES = ("stats-dark", "stats-light", "streak-dark", "streak-light")


def replace_section(text, name, content):
    start = f"<!-- {name}:START -->"
    end = f"<!-- {name}:END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Expected exactly one {name} marker pair")
    before, remaining = text.split(start, 1)
    if end not in remaining:
        raise ValueError(f"Reversed {name} markers")
    _, after = remaining.split(end, 1)
    return f"{before}{start}\n{content.strip()}\n{end}{after}"


def fetch_events(username, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CyPheer1-profile-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"https://api.github.com/users/{username}/events/public?per_page=100",
        headers=headers,
    )
    with urlopen(request, timeout=30) as response:
        events = json.load(response)
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Unexpected events response; retaining previous activity")
    return events


def render_activity(events, username, limit=5):
    verbs = {
        "PushEvent": "Pushed code to",
        "PublicEvent": "Made public",
        "CreateEvent": "Created a ref or repository in",
        "PullRequestEvent": "Updated a pull request in",
        "PullRequestReviewEvent": "Reviewed a pull request in",
        "IssuesEvent": "Updated an issue in",
        "ReleaseEvent": "Published a release in",
    }
    rows = []
    seen = set()
    for event in sorted(events, key=lambda item: str(item.get("created_at", "")), reverse=True):
        kind = event.get("type")
        repo = event.get("repo", {}).get("name", "")
        if not event.get("public") or kind not in verbs:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            continue
        if repo.lower() == f"{username}/{username}".lower():
            continue
        try:
            date = datetime.strptime(event["created_at"], "%Y-%m-%dT%H:%M:%SZ").date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        key = (date, kind, repo)
        if key in seen:
            continue
        seen.add(key)
        label = verbs[kind]
        ref_type = event.get("payload", {}).get("ref_type")
        if kind == "CreateEvent" and ref_type in ("branch", "tag", "repository"):
            label = f"Created a {ref_type} in"
        link = f'<a href="https://github.com/{escape(repo, quote=True)}">{escape(repo)}</a>'
        rows.append(f"  <tr><td>{date}</td><td>{label} {link}</td></tr>")
        if len(rows) >= limit:
            break
    if not rows:
        return (
            '<p>No recent public development events in GitHub\'s available event window. '
            f'<a href="https://github.com/{username}?tab=overview">View activity</a>.</p>'
        )
    return "\n".join([
        "<table>",
        "  <tr><th>UTC date</th><th>Public activity</th></tr>",
        *rows,
        "</table>",
    ])


def valid_svg(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        return False
    text = " ".join(root.itertext()).lower()
    failures = ("something went wrong", "could not fetch", "rate limit", "error fetching", "invalid username")
    return bool(text.strip()) and not any(message in text for message in failures)


def promote_cards(staging, root):
    for name in CARD_NAMES:
        source = staging / f"{name}.svg"
        if not source.is_file():
            print(f"::warning::No new {name} card; retaining the existing snapshot.")
            continue
        data = source.read_bytes()
        if not valid_svg(data):
            print(f"::warning::Rejected invalid {name} card; retaining the existing snapshot.")
            continue
        destination = root / "assets" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != data:
            temporary = destination.with_suffix(".svg.tmp")
            temporary.write_bytes(data)
            temporary.replace(destination)


def card_url(kind, mode, username, root):
    relative = f"assets/{kind}-{mode}.svg"
    if (root / relative).is_file():
        return relative
    variant = "mocha" if mode == "dark" else "latte"
    if kind == "stats":
        base = "https://github-readme-stats.vercel.app/api?"
        options = {"username": username, "show_icons": "true", "hide_rank": "true", "theme": f"catppuccin_{variant}"}
    else:
        base = "https://streak-stats.demolab.com/?"
        options = {"user": username, "theme": f"catppuccin-{variant}"}
    options.update(hide_border="true", border_radius="8")
    return escape(base + urlencode(options), quote=True)


def render_stats(username, root):
    pictures = ['<p align="center">']
    for kind, label in (("stats", "public GitHub statistics"), ("streak", "GitHub contribution streak")):
        dark = card_url(kind, "dark", username, root)
        light = card_url(kind, "light", username, root)
        pictures.extend([
            f'  <a href="https://github.com/{username}">',
            "    <picture>",
            f'      <source media="(prefers-color-scheme: dark)" srcset="{dark}" />',
            f'      <img width="440" src="{light}" alt="Mohamed\'s {label}" />',
            "    </picture>",
            "  </a>",
        ])
    return "\n".join([*pictures, "</p>"])


def update_readme(root, username, token="", staging=None):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", username):
        raise ValueError("Invalid GitHub username")
    path = root / "README.md"
    original = path.read_text(encoding="utf-8")
    for name in SECTIONS:
        replace_section(original, name, "")
    updated = original
    try:
        events = fetch_events(username, token)
        updated = replace_section(updated, "ACTIVITY", render_activity(events, username))
    except (URLError, TimeoutError, OSError, ValueError):
        print("::warning::Public activity refresh failed; previous activity is unchanged.")
    if staging is not None:
        promote_cards(staging, root)
    updated = replace_section(updated, "PROFILE-STATS", render_stats(username, root))
    if updated != original:
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(updated, encoding="utf-8", newline="\n")
        temporary.replace(path)
        print("README updated.")
    else:
        print("README is already up to date.")


def main():
    parser = argparse.ArgumentParser(description="Refresh public activity and validated profile cards.")
    parser.add_argument("--cards-dir", type=Path)
    args = parser.parse_args()
    try:
        update_readme(ROOT, os.environ.get("PROFILE_USERNAME", "CyPheer1"), os.environ.get("GITHUB_TOKEN", ""), args.cards_dir)
    except (OSError, ValueError) as error:
        print(f"Profile update failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())