#!/usr/bin/env python3
#
# github_release.py - publish dist/ as a GitHub release for a version tag
#
# Author:  Sandesh Ghimire <sandesh@soccentric.com>
#
# Shared by every soccentric-stm32 repository (keep the copies identical).
# Run by the Jenkins Release stage when it builds a tag.  The tag must be
# v<version>, where <version> is what ci/version.sh prints, so a release can
# never carry firmware that reports another version.  0.x versions are marked
# as pre-releases.  Re-running for the same tag reuses the release and
# replaces assets of the same name.
#
# Needs GITHUB_TOKEN (Jenkins credential "github-release-token", a token with
# contents:write on the repository).  Python standard library only.
#
# Usage:
#   ci/github_release.py --tag v0.1.0 --version 0.1.0 dist/*
#   ci/github_release.py --tag v0.1.0 --version 0.1.0 --dry-run dist/*
#

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def repo_slug() -> str:
    url = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not match:
        sys.exit(f"release: origin {url} is not a GitHub repository")
    return f"{match.group(1)}/{match.group(2)}"


def request(method: str, url: str, token: str, body=None, data=None, content_type=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "soccentric-stm32-ci",
    }
    if body is not None:
        data = json.dumps(body).encode()
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as err:
        payload = err.read()
        try:
            return err.code, json.loads(payload)
        except ValueError:
            return err.code, {"message": payload.decode(errors="replace")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish dist/ as a GitHub release")
    parser.add_argument("--tag", required=True, help="the git tag being built, e.g. v0.1.0")
    parser.add_argument("--version", required=True, help="the version the build reports")
    parser.add_argument("--dry-run", action="store_true", help="check and print, change nothing")
    parser.add_argument("assets", nargs="+", help="files to upload (directories are skipped)")
    args = parser.parse_args()

    if args.tag != f"v{args.version}":
        sys.exit(f"release: tag {args.tag} does not match version {args.version}; "
                 f"tag v{args.version} or bump the version first")

    assets = [Path(a) for a in args.assets if Path(a).is_file()]
    if not assets:
        sys.exit("release: nothing to upload")

    slug = repo_slug()
    prerelease = args.version.split(".")[0] == "0"
    print(f"release: {slug} {args.tag} ({'pre-release' if prerelease else 'release'})")
    for asset in assets:
        print(f"release:   {asset.name} ({asset.stat().st_size} bytes)")

    token = os.environ.get("GITHUB_TOKEN", "")
    if args.dry_run:
        if token:
            status, repo = request("GET", f"{API}/repos/{slug}", token)
            if status != 200:
                sys.exit(f"release: cannot read {slug}: {status} {repo.get('message')}")
            status, _ = request("GET", f"{API}/repos/{slug}/releases/tags/{args.tag}", token)
            print(f"release: dry run, release for {args.tag} "
                  f"{'exists and would be updated' if status == 200 else 'would be created'}")
        else:
            print("release: dry run without GITHUB_TOKEN, GitHub not contacted")
        return 0
    if not token:
        sys.exit("release: GITHUB_TOKEN is not set")

    status, release = request("GET", f"{API}/repos/{slug}/releases/tags/{args.tag}", token)
    if status == 404:
        status, release = request("POST", f"{API}/repos/{slug}/releases", token, body={
            "tag_name": args.tag,
            "name": f"{slug.split('/')[1]} {args.tag}",
            "generate_release_notes": True,
            "prerelease": prerelease,
        })
        if status != 201:
            sys.exit(f"release: create failed: {status} {release.get('message')}")
        print(f"release: created {release['html_url']}")
    elif status == 200:
        print(f"release: updating {release['html_url']}")
    else:
        sys.exit(f"release: lookup failed: {status} {release.get('message')}")

    existing = {a["name"]: a["id"] for a in release.get("assets", [])}
    upload = release["upload_url"].split("{", 1)[0]
    for asset in assets:
        if asset.name in existing:
            request("DELETE", f"{API}/repos/{slug}/releases/assets/{existing[asset.name]}", token)
        kind = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        url = f"{upload}?name={urllib.parse.quote(asset.name)}"
        status, result = request("POST", url, token, data=asset.read_bytes(), content_type=kind)
        if status != 201:
            sys.exit(f"release: upload of {asset.name} failed: {status} {result.get('message')}")
        print(f"release: uploaded {asset.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
