#!/usr/bin/env python3
"""Build OpenHands ``/launch`` URLs (and HTML buttons / Markdown badges) for plugins.

The ``/launch`` endpoint lets anyone start a conversation with one or more
plugins pre-loaded just by clicking a link -- no API key, no code. The recipe is
a base64-encoded list of plugin specs in the ``plugins`` query param plus an
optional starting ``message``:

    https://app.all-hands.dev/launch?plugins=<BASE64>&message=<URL-ENCODED>

When the link is opened the OpenHands frontend decodes ``plugins``, shows a
confirmation modal (pre-filling any parameter form fields), and on submit calls
``POST /api/v1/app-conversations`` -- the same endpoint the minimal
``load-plugin`` example calls directly. See:
https://github.com/OpenHands/OpenHands/blob/main/enterprise/doc/design-doc/plugin-launch-flow.md

This module shows exactly how that URL is built and emits ready-to-paste HTML
buttons and Markdown badges. Run it with no arguments to print two worked
examples, or pass flags to build your own.
"""

from __future__ import annotations

import argparse
import base64
import json
from urllib.parse import parse_qs, quote, urlparse


DEFAULT_BASE_URL = "https://app.all-hands.dev"


# --- The core: encode plugin specs into a /launch URL -------------------------


def encode_plugins(plugins: list[dict]) -> str:
    """JSON-encode the plugin-spec list, then base64 it for the ``plugins`` param.

    This matches the encoding the OpenHands plugin directory uses:

        list[dict]  ->  JSON text  ->  UTF-8 bytes  ->  standard base64

    ``json.dumps`` defaults (``", "`` / ``": "`` separators) are kept so the
    output is byte-for-byte identical to the launch badges generated elsewhere.
    """
    raw = json.dumps(plugins)  # e.g. [{"source": "...", "repo_path": "..."}]
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def decode_plugins(encoded: str) -> list[dict]:
    """Inverse of :func:`encode_plugins` -- handy for verifying a URL."""
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def build_launch_url(
    plugins: list[dict],
    message: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """Return a ``/launch`` URL that loads ``plugins`` and optionally sends ``message``.

    * ``plugins`` is base64-encoded (then URL-escaped so ``+`` / ``/`` survive).
    * ``message`` is URL-encoded. Provide it to auto-run an entry command
      (e.g. ``"/dad-joke:about"``); omit it to just load the plugin and let
      the user type their own first prompt.
    """
    params = "plugins=" + quote(encode_plugins(plugins), safe="")
    if message:
        params += "&message=" + quote(message, safe="")
    return f"{base_url}/launch?{params}"


# --- Render the URL as a clickable button / badge -----------------------------


def html_button(label: str, url: str) -> str:
    """A minimal HTML button you can paste into a web page or docs site."""
    return f'<a href="{url}"><button>{label}</button></a>'


def _shield_label(label: str) -> str:
    """Escape a label for a shields.io badge path.

    shields.io treats ``-`` as a field separator, so a literal dash must be
    doubled (``--``) and spaces become ``%20``.
    """
    return quote(label.replace("-", "--").replace(" ", "%20"), safe="%")


def markdown_badge(label: str, url: str, color: str = "blue") -> str:
    """A shields.io badge that links to the launch URL (great for a README)."""
    shield = f"https://img.shields.io/badge/{_shield_label(label)}-{color}"
    return f"[![{label}]({shield})]({url})"


def plugin_spec(
    source: str,
    repo_path: str,
    ref: str = "main",
    parameters: dict | None = None,
) -> dict:
    """Build a single plugin spec, omitting ``parameters`` when not supplied.

    ``parameters`` hold *default* values used to pre-fill the launch modal's
    form fields; the user can edit them before starting the conversation.
    """
    spec: dict = {"source": source, "ref": ref, "repo_path": repo_path}
    if parameters:
        spec["parameters"] = parameters
    return spec


# --- Pretty-print one fully worked example ------------------------------------


def show(label: str, plugins: list[dict], message: str | None, base_url: str) -> None:
    url = build_launch_url(plugins, message=message, base_url=base_url)
    print(f"### {label}")
    print()
    print("plugins (decoded):", json.dumps(plugins))
    print("message:          ", repr(message))
    print()
    print("Launch URL:")
    print(" ", url)
    print()
    print("HTML button:")
    print(" ", html_button(label, url))
    print()
    print("Markdown badge:")
    print(" ", markdown_badge(label, url))
    print()
    # Round-trip check so readers can see the encoding is reversible.
    # parse_qs URL-decodes the value (turning %3D back into the '=' padding).
    encoded = parse_qs(urlparse(url).query)["plugins"][0]
    assert decode_plugins(encoded) == plugins
    print("-" * 72)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build an OpenHands /launch URL, HTML button, and README badge.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Frontend base URL.")
    p.add_argument(
        "--source",
        default="github:OpenHands/enterprise-cookbook",
        help="Plugin source, e.g. 'github:owner/repo'.",
    )
    p.add_argument("--ref", default="main", help="Git ref/branch/tag.")
    p.add_argument(
        "--repo-path",
        default=None,
        help="Plugin sub-directory. If omitted, two worked examples are shown.",
    )
    p.add_argument(
        "--message",
        default=None,
        help="Optional entry command / first message (e.g. '/dad-joke:about').",
    )
    p.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Default value to pre-fill in the launch modal (repeatable).",
    )
    p.add_argument("--label", default="Launch plugin", help="Button / badge label.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # No specific plugin requested -> show the two canonical variants, both
    # using this example's self-contained plugin (./dad-joke).
    if not args.repo_path:
        plugin_source = "github:OpenHands/enterprise-cookbook"
        plugin_path = "launch-plugin-badge/dad-joke"
        print("Two ways to use the /launch endpoint with a plugin:\n")

        # Variant 1: auto-run the plugin's entry command on launch.
        show(
            "Tell a dad joke",
            [plugin_spec(plugin_source, plugin_path, parameters={"animal": "duck"})],
            message="/dad-joke:about",
            base_url=args.base_url,
        )

        # Variant 2: just load the plugin; the user types their own first prompt.
        show(
            "Open with dad-joke loaded",
            [plugin_spec(plugin_source, plugin_path)],
            message=None,
            base_url=args.base_url,
        )
        return 0

    # Otherwise build exactly what was asked for on the command line.
    parameters = dict(kv.split("=", 1) for kv in args.param) if args.param else None
    plugins = [plugin_spec(args.source, args.repo_path, args.ref, parameters)]
    show(args.label, plugins, args.message, args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
