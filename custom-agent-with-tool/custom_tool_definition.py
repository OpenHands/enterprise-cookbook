#!/usr/bin/env python3
"""Custom tool definition - Bureau of Bug Registration.

A deliberately absurd custom tool built with the OpenHands SDK. `working_example.py`
deploys this file into a Cloud sandbox as an importable module so the agent-server
can load it via `tool_module_qualnames`. Importing this module registers the tool
(see the `register_tool` call at the bottom).

Why this tool? It does something an LLM would never produce on its own and could not
fake: it assigns each bug a deterministic, hash-derived Case ID (e.g. BUG-7F3A2C)
plus a gloriously bureaucratic classification. Because the Case ID is a SHA-256 slice
of the bug report, the only way the agent's answer can contain the correct ID is if it
actually called this tool - which makes the end-to-end demonstration unfalsifiable.
"""

import hashlib
from typing import ClassVar

from openhands.sdk import Action, Observation
from openhands.sdk.tool import ToolDefinition, ToolExecutor, register_tool
from pydantic import Field


# Absurd, official-sounding taxonomy. Chosen deterministically from the bug's hash,
# so a given bug always files under the same class - but no LLM would invent these.
CLASSIFICATIONS = [
    "Gremlin-Class Off-By-One Poltergeist",
    "Heisenbug of the Third Kind",
    "Quantum Semicolon Anomaly",
    "Rogue Cosmic-Ray Bit-Flip",
    "Malicious Whitespace Entity",
    "Schrodinger's NullPointer",
    "Time-Traveling Race Condition",
    "Loch Ness Memory Leak",
    "Feral Edge Case (undomesticated)",
    "Haunted Copy-Paste Residue",
    "Passive-Aggressive Type Mismatch",
    "Gaslighting Boolean",
]

REMEDIATION_RITUALS = [
    "Rotate your chair 90 degrees widdershins, whisper the variable's name, "
    "and re-read line 1.",
    "Explain the code to a houseplant. If no plant is available, a stapler "
    "is legally sufficient.",
    "Add a print statement. Remove it. Add it back. Achieve enlightenment.",
    "Rename the variable to temp_final_v2_REAL and promise yourself you'll "
    "fix it later.",
    "Delete the code, walk away, and reimplement it identically but with "
    "more confidence.",
    "Blame the compiler. Apologize to the compiler. Re-examine your own logic.",
    "Summon a second engineer; the bug will vanish the instant they arrive.",
    "Turn it off and on again, then look genuinely surprised when that works.",
]


class BugReport(Action):
    """Input for filing a bug with the Bureau."""

    problem: str = Field(..., description="Description of the bug or misbehavior")
    code: str | None = Field(
        default=None, description="The offending code snippet (optional)"
    )


class BugFiling(Observation):
    """Output: an official (and legally meaningless) bug filing."""

    case_id: str = Field(..., description="The assigned Case ID, e.g. BUG-7F3A2C")
    classification: str = Field(..., description="The bug's official classification")


class BugRegistryExecutor(ToolExecutor[BugReport, BugFiling]):
    """Registers a bug and issues a deterministic, hash-derived filing."""

    def __call__(self, action: BugReport, _conversation=None) -> BugFiling:
        digest = hashlib.sha256(
            (action.problem + "\n" + (action.code or "")).encode("utf-8")
        ).hexdigest()

        case_id = "BUG-" + digest[:6].upper()
        classification = CLASSIFICATIONS[int(digest[6:10], 16) % len(CLASSIFICATIONS)]
        goblins = int(digest[10:12], 16) % 5 + 1
        ritual = REMEDIATION_RITUALS[int(digest[12:16], 16) % len(REMEDIATION_RITUALS)]
        reg_num = int(digest[16:18], 16)
        reg_sub = chr(ord("a") + int(digest[18], 16) % 6)
        clerk = digest[19:23].upper()

        receipt = "\n".join(
            [
                "STAMP  BUREAU OF BUG REGISTRATION - Official Filing Receipt  STAMP",
                "",
                f"  Case ID:        {case_id}",
                f"  Classification: {classification}",
                f"  Severity:       {'*' * goblins}{'.' * (5 - goblins)}  "
                f"({goblins}/5 goblins)",
                f"  Filed under:    Regulation section {reg_num}.{reg_sub}, "
                "'Undefined Behavior & Adjacent Shenanigans'",
                "",
                "  Prescribed remediation ritual:",
                f"    -> {ritual}",
                "",
                f"  Filed by Clerk 0x{clerk} of the Night Shift.",
                "  This filing is legally binding in exactly zero (0) jurisdictions.",
                f"  Retain {case_id} for your records and your memoirs.",
            ]
        )

        return BugFiling.from_text(
            text=receipt, case_id=case_id, classification=classification
        )


class BugRegistryTool(ToolDefinition[BugReport, BugFiling]):
    """Tool definition for the Bureau of Bug Registration."""

    name: ClassVar[str] = "bug_registry"

    @classmethod
    def create(cls, conv_state=None) -> list["ToolDefinition"]:  # noqa: ARG003
        return [
            cls(
                description=(
                    "File a bug with the Bureau of Bug Registration. Returns an "
                    "official Case ID and a formal classification for the bug. Use "
                    "this whenever a bug needs to be registered on the record. Pass "
                    "a description of the problem and, optionally, the offending code."
                ),
                action_type=BugReport,
                observation_type=BugFiling,
                executor=BugRegistryExecutor(),
            )
        ]


# Registering the tool is the side effect that agent-server relies on when it
# imports this module via tool_module_qualnames.
register_tool("bug_registry", BugRegistryTool)


if __name__ == "__main__":
    obs = BugRegistryExecutor()(
        BugReport(
            problem="ZeroDivisionError on single-element list", code="len(nums)-1"
        )
    )
    print(obs.case_id, "|", obs.classification)
