#!/usr/bin/env python3
"""Disable OpenHands skills for every conversation started by an account.

The App Server's ``effective_disabled_skills`` unions three deny-lists —
account (``user.disabled_skills``), launched agent profile
(``agent_context.disabled_skills``), and per-request
(``AppConversationStartRequest.disabled_skills``). A skill disabled at any
level stays off. This script demonstrates the **account-level** mechanism
(the one the UI's Settings → Skills page toggles) as the primary pattern,
and optionally exercises the per-request escape hatch.

Flow:

  1. ``GET /api/v1/settings`` — read the current ``disabled_skills`` so we
     can restore it on cleanup.
  2. ``POST /api/v1/settings`` with ``{"disabled_skills": [...]}`` — install
     the account-level deny-list. Same call the UI Skills-Settings page
     makes when a member toggles a skill off.
  3. ``POST /api/v1/app-conversations`` — start a conversation with **no**
     ``disabled_skills`` on the request itself. The account-level setting
     alone is expected to keep the skills out.
  4. Fetch the first ``SystemPromptEvent`` from the Cloud events endpoint
     and parse the ``<SKILLS>`` block in ``dynamic_context.text``. Assert
     every disabled skill is absent.
  5. Optional (``--per-request``): repeat with extra per-request disabled
     skills to show they union with the account-level list.
  6. Cleanup: ``DELETE`` the conversation(s) and their sandbox(es), then
     restore the original account-level ``disabled_skills``.

Env / flags:

    export OH_API_KEY=...                        # Cloud API key (required)
    python disabled_skills.py                    # gallery: "no-git-integrations"
    python disabled_skills.py --gallery no-browser
    python disabled_skills.py --disable docker kubernetes
    python disabled_skills.py --per-request      # also demo request-level union
    python disabled_skills.py --keep             # skip cleanup (still restores)
    python disabled_skills.py \
        --base-url https://your-ohe.example.com  # OHE instance
"""

import argparse
import os
import re
import sys
import time

import requests


# Common deny-list recipes. Names must match ``<name>`` values from the
# ``<SKILLS>`` block (see GET /api/v1/skills/search).
GALLERIES: dict[str, list[str]] = {
    "no-git-integrations": [
        "github",
        "gitlab",
        "bitbucket",
        "bitbucket-cloud",
        "bitbucket-data-center",
        "azure-devops",
    ],
    "no-browser": [
        # The 'browser' tool ships in default-tools and can be removed via
        # a custom agent; skills that *use* the browser can be denied here.
        "frontend-design",
        "vercel",
    ],
    "no-docker": ["docker", "kubernetes"],
    "no-github-automations": [
        "github-actions",
        "github-agents-md-maintainer",
        "github-delivery-watchdog",
        "github-issue-to-pr",
        "github-issue-triage",
        "github-pr-review",
        "github-pr-reviewer",
        "github-repo-monitor",
        "github-stale-ci-pr-closer",
    ],
}


def _raise_for_status(resp: requests.Response) -> None:
    """Like ``resp.raise_for_status()`` but prints the server's error body.

    ``raise_for_status`` on its own only surfaces the status line, which
    hides the server-side validation detail (``{"detail": [...]}`` on
    4xx). Printing ``resp.text`` first turns a mystery 422 into an
    actionable "you're missing this field" message for the caller.
    """
    if resp.ok:
        return
    print(
        f"HTTP {resp.status_code} from {resp.request.method} {resp.url}\n"
        f"  body: {resp.text[:2000]}",
        file=sys.stderr,
    )
    resp.raise_for_status()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Disable OpenHands skills at the account level.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("OH_API_KEY"),
        help="Cloud API key (env: OH_API_KEY).",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_API_BASE", "https://app.all-hands.dev"),
        help="Cloud app server base URL (env: OH_API_BASE).",
    )
    p.add_argument(
        "--gallery",
        choices=sorted(GALLERIES.keys()),
        default="no-git-integrations",
        help="Pre-baked deny-list to install at account level.",
    )
    p.add_argument(
        "--disable",
        nargs="+",
        metavar="SKILL",
        help="Explicit skill names to disable (overrides --gallery).",
    )
    p.add_argument(
        "--per-request",
        action="store_true",
        help=(
            "Also start a second conversation with additional per-request "
            "disabled_skills to demonstrate deny-list union."
        ),
    )
    p.add_argument(
        "--request-extra",
        nargs="+",
        default=["flarglebargle"],
        metavar="SKILL",
        help="Extra skills passed on the per-request start (with --per-request).",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Leave conversations/sandboxes running (account setting still restored).",
    )
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "300")),
        help="Seconds to wait for conversation readiness (env: POLL_TIMEOUT).",
    )
    return p.parse_args()


def get_account_disabled_skills(base_url: str, headers: dict) -> list[str] | None:
    """Return the current account-level ``disabled_skills`` (or None if unset)."""
    resp = requests.get(f"{base_url}/api/v1/settings", headers=headers)
    _raise_for_status(resp)
    return resp.json().get("disabled_skills")


def set_account_disabled_skills(
    base_url: str, headers: dict, value: list[str] | None
) -> None:
    """POST a partial settings payload updating only ``disabled_skills``.

    The POST endpoint deep-merges, so sending just this one field leaves
    every other setting untouched. This is the same call the Skills-Settings
    page in the UI makes via ``useSkillMutations.saveDisabledSkills``.

    Note: the server treats a ``null`` payload as "no change" (it copies
    the previously-persisted list forward). To clear the deny-list, send
    ``[]`` instead. ``effective_disabled_skills`` treats ``None`` and
    ``[]`` identically, so the observable behaviour is the same.
    """
    payload_value = value if value is not None else []
    resp = requests.post(
        f"{base_url}/api/v1/settings",
        headers=headers,
        json={"disabled_skills": payload_value},
    )
    _raise_for_status(resp)


def start_conversation(
    base_url: str,
    headers: dict,
    title: str,
    disabled_skills: list[str] | None,
    poll_timeout: int,
) -> tuple[str, str | None]:
    """POST /api/v1/app-conversations -> resolve the app_conversation_id.

    Returns (app_conversation_id, sandbox_id). ``disabled_skills`` is passed
    through as the *per-request* deny-list; leave it None to prove the
    account-level setting alone is doing the work.
    """
    payload: dict = {
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Say hello."}],
        },
        "title": title,
    }
    if disabled_skills is not None:
        payload["disabled_skills"] = disabled_skills

    resp = requests.post(
        f"{base_url}/api/v1/app-conversations", headers=headers, json=payload
    )
    _raise_for_status(resp)
    task = resp.json()
    task_id = task["id"]
    conv_id = task.get("app_conversation_id")
    sandbox_id = task.get("sandbox_id")

    deadline = time.monotonic() + poll_timeout
    while not conv_id:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Start task {task_id} never produced a conversation")
        time.sleep(3)
        resp = requests.get(
            f"{base_url}/api/v1/app-conversations/start-tasks",
            headers=headers,
            params={"ids": task_id},
        )
        _raise_for_status(resp)
        item = resp.json()[0]
        status = item.get("status")
        print("  start-task status:", status)
        if status == "ERROR":
            raise RuntimeError(f"Start task failed: {item.get('detail', 'unknown')}")
        conv_id = item.get("app_conversation_id")
        sandbox_id = item.get("sandbox_id") or sandbox_id
    return conv_id, sandbox_id


def fetch_system_prompt_event(
    base_url: str, headers: dict, conv_id: str, timeout: int
) -> dict:
    """Fetch the first ``SystemPromptEvent`` for the conversation.

    The event is created before the first LLM turn, so it's usually
    available the moment the conversation is READY -- but retry briefly
    to absorb the small write-read gap.
    """
    deadline = time.monotonic() + timeout
    url = f"{base_url}/api/v1/conversation/{conv_id}/events/search"
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"kind__eq": "SystemPromptEvent", "limit": 1},
        )
        _raise_for_status(resp)
        items = resp.json().get("items") or []
        if items:
            return items[0]
        if time.monotonic() > deadline:
            raise TimeoutError(f"No SystemPromptEvent for {conv_id} after {timeout}s")
        time.sleep(2)


# ``<SKILLS>...</SKILLS>`` wraps the injected block; each entry is a
# ``<skill><name>foo</name>...</skill>``. This lets us assert on the exact
# skill list the agent sees, not the raw regexp-search on the whole prompt.
_SKILLS_BLOCK_RE = re.compile(r"<SKILLS>(.*?)</SKILLS>", re.DOTALL)
_SKILL_NAME_RE = re.compile(r"<name>([^<]+)</name>")


def loaded_skill_names(dynamic_context_text: str) -> list[str]:
    """Return the ``<name>`` values inside ``<SKILLS>...</SKILLS>``."""
    m = _SKILLS_BLOCK_RE.search(dynamic_context_text)
    if not m:
        return []
    return _SKILL_NAME_RE.findall(m.group(1))


def verify_disabled(
    label: str,
    dynamic_context_text: str,
    expected_disabled: list[str],
) -> bool:
    """Assert none of ``expected_disabled`` appear in the loaded ``<SKILLS>``."""
    loaded = loaded_skill_names(dynamic_context_text)
    print(f"  {label}: loaded {len(loaded)} skills")
    leaked = sorted(set(expected_disabled) & set(loaded))
    for name in expected_disabled:
        mark = "LEAKED" if name in loaded else "absent"
        print(f"    - {name:40s} {mark}")
    if leaked:
        print(f"  FAIL ({label}): these should have been denied: {leaked}")
        return False
    print(f"  PASS ({label}): all denied skills absent from SystemPromptEvent")
    return True


def cleanup(base_url: str, headers: dict, conv_id: str, sandbox_id: str | None) -> None:
    requests.delete(f"{base_url}/api/v1/app-conversations/{conv_id}", headers=headers)
    print("  deleted conversation", conv_id)
    if sandbox_id:
        # DELETE /api/v1/sandboxes/{id} also requires sandbox_id as a
        # query parameter; omitting it returns HTTP 422.
        requests.delete(
            f"{base_url}/api/v1/sandboxes/{sandbox_id}",
            headers=headers,
            params={"sandbox_id": sandbox_id},
        )
        print("  deleted sandbox", sandbox_id)


def main() -> None:
    args = parse_args()
    if not args.api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")
    headers = {"Authorization": f"Bearer {args.api_key}"}

    account_list = args.disable or GALLERIES[args.gallery]
    print("=== install account-level deny-list ===")
    print(f"  disabling: {account_list}")

    original = get_account_disabled_skills(args.base_url, headers)
    print(f"  previous account setting: {original!r}")

    set_account_disabled_skills(args.base_url, headers, account_list)
    readback = get_account_disabled_skills(args.base_url, headers)
    print(f"  new account setting:      {readback!r}")
    assert readback == account_list, "account-level readback mismatch"

    ok = True
    conversations: list[tuple[str, str | None]] = []
    try:
        print("\n=== conversation 1: no per-request disabled_skills ===")
        conv1, sbx1 = start_conversation(
            args.base_url,
            headers,
            "disabled-skills demo (account only)",
            disabled_skills=None,
            poll_timeout=args.poll_timeout,
        )
        conversations.append((conv1, sbx1))
        print("  conversation:", conv1, "sandbox:", sbx1)

        event = fetch_system_prompt_event(
            args.base_url, headers, conv1, args.poll_timeout
        )
        dc_text = (event.get("dynamic_context") or {}).get("text", "")
        ok &= verify_disabled("account-only", dc_text, account_list)

        if args.per_request:
            extra = args.request_extra
            union = list(dict.fromkeys([*account_list, *extra]))
            print(
                "\n=== conversation 2: per-request disabled_skills = "
                f"{extra} (union should be {union}) ==="
            )
            conv2, sbx2 = start_conversation(
                args.base_url,
                headers,
                "disabled-skills demo (account + per-request)",
                disabled_skills=extra,
                poll_timeout=args.poll_timeout,
            )
            conversations.append((conv2, sbx2))
            print("  conversation:", conv2, "sandbox:", sbx2)

            event2 = fetch_system_prompt_event(
                args.base_url, headers, conv2, args.poll_timeout
            )
            dc2 = (event2.get("dynamic_context") or {}).get("text", "")
            ok &= verify_disabled("account + request", dc2, union)
    finally:
        if not args.keep:
            print("\n=== cleanup ===")
            for conv_id, sandbox_id in conversations:
                cleanup(args.base_url, headers, conv_id, sandbox_id)
        else:
            for conv_id, sandbox_id in conversations:
                print(
                    f"\nLeft running (--keep): {args.base_url}/conversations/{conv_id}"
                )

        print("\n=== restore original account-level setting ===")
        set_account_disabled_skills(args.base_url, headers, original)
        after = get_account_disabled_skills(args.base_url, headers)
        print(f"  restored account setting: {after!r}")
        # ``None`` and ``[]`` are observationally equivalent (both mean "no
        # deny-list") so treat them as a match on restore.
        if (after or []) != (original or []):
            print("  WARNING: restore mismatch")
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
