"""Reading for Hermes: newsletters out of the Gmail inbox, into a reader built for them.

The agent half adds:
- the ``hermes reading`` CLI (setup, scan, senders, sync, unsubscribe, export for digest jobs);
- the ``@newsletter:`` context reference, so any chat can attach a newsletter as text;
- a bundled skill that shows Hermes how to set Reading up and wire a digest job.

The reader page is page.html; the Desktop pane is desktop/plugin.js; the page API is
dashboard/plugin_api.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _core():
    import reading_core
    return reading_core


def _make_provider():
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class NewsletterReference(ContextReferenceProvider):
        """``@newsletter:<id>`` attaches one newsletter from the Reading label as text."""

        prefix = "newsletter"
        description = "A newsletter from Reading"

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            core = _core()
            try:
                items = await asyncio.to_thread(core.list_messages, 100)
            except Exception:
                return []
            q = (query or "").lower()
            hits = [m for m in items if q in f"{m['subject']} {m['from']}".lower()] if q else items
            return [ContextCompletionItem(text=m["id"], display=m["subject"][:80], meta=m["from"]) for m in hits[:limit]]

        async def expand(self, target: str) -> str | None:
            target = (target or "").strip()
            if not target.isalnum():
                return None
            return await asyncio.to_thread(_core().message_text, target)

    return NewsletterReference()


def register(ctx):
    from reading_cli import run, setup

    ctx.register_cli_command(
        name="reading",
        help="Newsletters out of the Gmail inbox: setup, senders, scan, sync, export",
        setup_fn=setup,
        handler_fn=run,
        description="Reading moves newsletters out of the Gmail inbox into one label and reads them in a pane.",
    )
    try:
        ctx.register_context_reference(_make_provider())
    except Exception:  # older Hermes without plugin context references: the rest still works
        pass
    skills = _HERE / "skills"
    if skills.is_dir():
        for child in sorted(skills.iterdir()):
            if (child / "SKILL.md").is_file():
                ctx.register_skill(child.name, child / "SKILL.md")
