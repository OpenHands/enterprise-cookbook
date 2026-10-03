#!/usr/bin/env python3
"""Push rendered Cookbook pages to OpenHands/docs and report back.

Used by the docs-preview and docs-publish workflows in the jobs that hold the
docs write token. It only copies files produced by the unprivileged render
job; it never runs the converter or anything else from the pull request.

Environment:
  DOCS_TOKEN    token that can push branches and open PRs on OpenHands/docs
  GITHUB_TOKEN  token for this repository (statuses, PR comments)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


DOCS_REPO = "OpenHands/docs"
COMMENT_MARKER = "<!-- cookbook-docs-preview -->"
STATUS_CONTEXT = "docs-preview"
PASSING = {"success", "skipped", "neutral"}


def run(*args: str, cwd: Path | None = None, token: str | None = None) -> str:
    env = dict(os.environ)
    if token is not None:
        env["GH_TOKEN"] = token
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"command failed: {' '.join(args[:3])} ...\n{result.stderr.strip()}")
    return result.stdout.strip()


def gh_api(path: str, *args: str, token_env: str = "DOCS_TOKEN") -> object:
    out = run("gh", "api", path, *args, token=os.environ[token_env])
    return json.loads(out) if out else None


def set_output(**values: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in values.items()]
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def open_pr_for(branch: str) -> dict | None:
    prs = gh_api(f"repos/{DOCS_REPO}/pulls?state=open&head=OpenHands:{branch}")
    assert isinstance(prs, list)
    return prs[0] if prs else None


def remote_branch(docs: Path, branch: str) -> str | None:
    """Fetch the branch and return its commit, or None if it doesn't exist."""
    if not run("git", "ls-remote", "--heads", "origin", branch, cwd=docs):
        return None
    run("git", "fetch", "-q", "--depth=1", "origin", branch, cwd=docs)
    return run("git", "rev-parse", "FETCH_HEAD", cwd=docs)


def changed_pages(docs: Path) -> list[dict]:
    lines = run(
        "git", "diff", "--name-status", "HEAD~1", "HEAD", "--", "cookbook", cwd=docs
    )
    pages = []
    for row in lines.splitlines():
        status, file = row.split("\t")[0], row.split("\t")[-1]
        slug = Path(file).stem
        pages.append(
            {
                "path": "/cookbook" if slug == "index" else f"/cookbook/{slug}",
                "change": {"A": "added", "D": "removed"}.get(status[0], "updated"),
            }
        )
    return pages


def cmd_push(a: argparse.Namespace) -> None:
    docs, artifact = Path(a.docs), Path(a.artifact)
    shutil.rmtree(docs / "cookbook", ignore_errors=True)
    shutil.copytree(artifact / "cookbook", docs / "cookbook")
    shutil.copy(artifact / "docs.json", docs / "docs.json")

    existing = open_pr_for(a.branch)
    run("git", "add", "-A", "cookbook", "docs.json", cwd=docs)
    if not run("git", "status", "--porcelain", cwd=docs):
        if existing:
            run(
                "gh",
                "pr",
                "close",
                str(existing["number"]),
                "--repo",
                DOCS_REPO,
                "--delete-branch",
                "--comment",
                "The source no longer changes the docs.",
                token=os.environ["DOCS_TOKEN"],
            )
        set_output(changed="false")
        return

    body = Path(a.body_file).read_text()
    run("git", "commit", "-q", "-m", a.title, "-m", body, cwd=docs)
    head_sha = run("git", "rev-parse", "HEAD", cwd=docs)
    remote = remote_branch(docs, a.branch)
    tree = run("git", "rev-parse", "HEAD^{tree}", cwd=docs)
    if remote and run("git", "rev-parse", f"{remote}^{{tree}}", cwd=docs) == tree:
        print(f"{a.branch} already has these files; not pushing")
        head_sha = remote
    else:
        push = f"HEAD:refs/heads/{a.branch}"
        run("git", "push", "-q", "--force", "origin", push, cwd=docs)

    if existing:
        number = existing["number"]
        gh_api(
            f"repos/{DOCS_REPO}/pulls/{number}",
            "-X",
            "PATCH",
            "-f",
            f"title={a.title}",
            "-f",
            f"body={Path(a.body_file).read_text()}",
        )
    else:
        args = [
            "gh",
            "pr",
            "create",
            "--repo",
            DOCS_REPO,
            "--base",
            "main",
            "--head",
            a.branch,
        ]
        args += ["--title", a.title, "--body-file", a.body_file] + (
            ["--draft"] if a.draft else []
        )
        url = run(*args, token=os.environ["DOCS_TOKEN"])
        number = int(url.rstrip("/").split("/")[-1])

    set_output(
        changed="true",
        pr_number=str(number),
        pr_url=f"https://github.com/{DOCS_REPO}/pull/{number}",
        head_sha=head_sha,
        pages=json.dumps(changed_pages(docs)),
    )


def wait_for_preview(sha: str, deadline: float) -> str | None:
    while time.time() < deadline:
        deployments = gh_api(f"repos/{DOCS_REPO}/deployments?sha={sha}")
        assert isinstance(deployments, list)
        for dep in deployments:
            statuses = gh_api(f"repos/{DOCS_REPO}/deployments/{dep['id']}/statuses")
            assert isinstance(statuses, list)
            if statuses and statuses[0]["state"] == "success":
                return statuses[0]["environment_url"]
            if statuses and statuses[0]["state"] in {"failure", "error"}:
                return None
        time.sleep(15)
    return None


def wait_for_checks(sha: str, deadline: float) -> list[dict]:
    while True:
        data = gh_api(f"repos/{DOCS_REPO}/commits/{sha}/check-runs?per_page=100")
        assert isinstance(data, dict)
        runs = data["check_runs"]
        if (
            runs and all(r["status"] == "completed" for r in runs)
        ) or time.time() > deadline:
            return [
                {
                    "name": r["name"],
                    "conclusion": r["conclusion"] or r["status"],
                    "url": r["html_url"],
                }
                for r in runs
            ]
        time.sleep(15)


def cmd_wait(a: argparse.Namespace) -> None:
    deadline = time.time() + a.timeout
    preview = wait_for_preview(a.sha, deadline)
    checks = wait_for_checks(a.sha, deadline)
    failed = [c for c in checks if c["conclusion"] not in PASSING]
    set_output(
        preview_url=preview or "",
        checks=json.dumps(checks),
        ok="true" if preview and not failed else "false",
    )


def upsert_comment(repo: str, pr: str, body: str) -> None:
    comments = gh_api(
        f"repos/{repo}/issues/{pr}/comments?per_page=100", token_env="GITHUB_TOKEN"
    )
    assert isinstance(comments, list)
    mine = next((c for c in comments if COMMENT_MARKER in c["body"]), None)
    if mine:
        gh_api(
            f"repos/{repo}/issues/comments/{mine['id']}",
            "-X",
            "PATCH",
            "-f",
            f"body={body}",
            token_env="GITHUB_TOKEN",
        )
    else:
        gh_api(
            f"repos/{repo}/issues/{pr}/comments",
            "-f",
            f"body={body}",
            token_env="GITHUB_TOKEN",
        )


def set_status(
    repo: str, sha: str, state: str, description: str, url: str = ""
) -> None:
    args = [
        "-f",
        f"state={state}",
        "-f",
        f"context={STATUS_CONTEXT}",
        "-f",
        f"description={description[:140]}",
    ]
    if url:
        args += ["-f", f"target_url={url}"]
    gh_api(f"repos/{repo}/statuses/{sha}", *args, token_env="GITHUB_TOKEN")


def cmd_report(a: argparse.Namespace) -> None:
    pages = json.loads(a.pages or "[]")
    checks = json.loads(a.checks or "[]")
    failed = [c for c in checks if c["conclusion"] not in PASSING]
    preview = a.preview_url.rstrip("/")

    if a.changed != "true":
        set_status(a.repo, a.sha, "success", "This PR does not change the docs")
        return

    if preview and not failed:
        state, headline = "success", f"✅ **Docs preview is ready:** {preview}"
    elif not preview:
        state, headline = (
            "failure",
            "❌ **The docs preview did not deploy.** Check the docs PR for details.",
        )
    else:
        state, headline = (
            "failure",
            f"❌ **Docs checks failed** on the preview: {preview}",
        )

    lines = [COMMENT_MARKER, "### Docs preview", "", headline, ""]
    if pages:
        lines += ["| Page | Change |", "|---|---|"]
        for p in pages:
            link = (
                f"[`{p['path']}`]({preview}{p['path']})"
                if preview and p["change"] != "removed"
                else f"`{p['path']}`"
            )
            lines.append(f"| {link} | {p['change']} |")
        lines.append("")
    if failed:
        lines += (
            ["Failing docs checks:", ""]
            + [f"- [{c['name']}]({c['url']}): {c['conclusion']}" for c in failed]
            + [""]
        )
    lines += [
        f"Docs PR: {a.docs_pr_url} (draft preview; never merged, closes with this PR).",
        f"Rendered from {a.sha[:7]}. After merge, a separate sync PR updates the docs.",
    ]
    upsert_comment(a.repo, a.pr, "\n".join(lines))
    description = "Preview ready" if state == "success" else "Docs preview failed"
    set_status(a.repo, a.sha, state, description, preview or a.docs_pr_url)


def cmd_status(a: argparse.Namespace) -> None:
    set_status(a.repo, a.sha, a.state, a.description, a.url)


def cmd_close(a: argparse.Namespace) -> None:
    existing = open_pr_for(a.branch)
    if not existing:
        print(f"no open docs PR for {a.branch}")
        return
    run(
        "gh",
        "pr",
        "close",
        str(existing["number"]),
        "--repo",
        DOCS_REPO,
        "--delete-branch",
        "--comment",
        a.comment,
        token=os.environ["DOCS_TOKEN"],
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(required=True)

    push = sub.add_parser(
        "push", help="apply rendered files, push the branch, open or update the docs PR"
    )
    push.add_argument("--artifact", required=True)
    push.add_argument("--docs", required=True)
    push.add_argument("--branch", required=True)
    push.add_argument("--title", required=True)
    push.add_argument("--body-file", required=True)
    push.add_argument("--draft", action="store_true")
    push.set_defaults(func=cmd_push)

    wait = sub.add_parser("wait", help="wait for the Mintlify preview and docs checks")
    wait.add_argument("--sha", required=True)
    wait.add_argument("--timeout", type=int, default=1200)
    wait.set_defaults(func=cmd_wait)

    report = sub.add_parser(
        "report", help="comment on the cookbook PR and set the docs-preview status"
    )
    for name in ("repo", "pr", "sha", "changed"):
        report.add_argument(f"--{name}", required=True)
    for name in ("docs-pr-url", "preview-url", "pages", "checks"):
        report.add_argument(f"--{name}", default="")
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help="set the docs-preview commit status")
    for name in ("repo", "sha", "state", "description"):
        status.add_argument(f"--{name}", required=True)
    status.add_argument("--url", default="")
    status.set_defaults(func=cmd_status)

    close = sub.add_parser(
        "close", help="close the docs PR for a branch and delete the branch"
    )
    close.add_argument("--branch", required=True)
    close.add_argument("--comment", required=True)
    close.set_defaults(func=cmd_close)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
