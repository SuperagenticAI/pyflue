"""Two-way chat integration example: webhook -> dispatch -> reply tool.

Mirrors the reference Chat pattern (``apps/docs/.../guide/chat.md``) without a
Chat-SDK dependency. The platform boundary lives in application code:

    GitHub webhook
      -> verify signature
      -> normalize the comment
      -> dispatch(assistant, id=thread, session=thread, input=...)
      -> continuing Flue agent session
      -> explicit reply tool posts back to the thread

Run it::

    uvicorn examples.chat.app:app --reload

Key ideas:
- The chat thread id is used as BOTH the agent instance id and session, so one
  thread maps to one continuing conversation.
- Outbound posting is an explicit, observable tool (not an implicit transport),
  and the tool closes over the dispatched thread id: the model only chooses the
  reply text, never the destination.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import FastAPI, Request, Response

from pyflue import create_agent, define_tool, dispatch

# Stand-in for a real provider client (e.g. the GitHub API). A production
# integration would post through the provider SDK and add idempotency.
SENT_REPLIES: list[dict[str, str]] = []

WEBHOOK_SECRET = os.environ.get("CHAT_WEBHOOK_SECRET", "dev-secret")


def _build_assistant(ctx: Any) -> dict[str, Any]:
    # The agent instance id is the chat thread id; the reply tool closes over it.
    thread_id = ctx.id

    def reply_to_chat_thread(args: dict[str, Any]) -> str:
        SENT_REPLIES.append({"thread": thread_id, "text": args["text"]})
        return "Reply sent."

    return {
        "model": "anthropic/claude-haiku-4-5",
        "instructions": (
            "You assist in a chat thread. When a response is appropriate, call "
            "reply_to_chat_thread with the message text."
        ),
        "tools": [
            define_tool(
                "reply_to_chat_thread",
                reply_to_chat_thread,
                description="Post a response into the current chat thread.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ],
    }


assistant = create_agent(_build_assistant)

app = FastAPI(title="PyFlue Chat Example")


def _verify_signature(body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@app.post("/webhooks/github", status_code=202)
async def github_webhook(request: Request) -> Any:
    body = await request.body()
    if not _verify_signature(body, request.headers.get("x-hub-signature-256", "")):
        return Response(status_code=401)

    event = json.loads(body or b"{}")
    # Normalize: a GitHub issue comment to a thread (the issue) and its text.
    thread_id = str(event.get("issue", {}).get("number", "thread"))
    text = event.get("comment", {}).get("body", "")

    receipt = await dispatch(
        assistant,
        id=thread_id,
        session=thread_id,
        input={"type": "chat.message", "text": text},
    )
    return {"status": "accepted", "dispatch_id": receipt.dispatch_id}
