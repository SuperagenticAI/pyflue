# Chat integration example

A minimal two-way chat integration showing PyFlue's recommended pattern:

```
GitHub webhook → verify signature → dispatch(agent, id=thread, session=thread)
              → continuing agent session → explicit reply tool → post to thread
```

It mirrors the reference [Chat guide](https://flueframework.com/docs/guide/chat/)
without requiring a Chat-SDK dependency. The platform boundary is ordinary
application code, and outbound posting is an explicit, observable tool.

## Run

```bash
pip install 'pyflue[server]'
export CHAT_WEBHOOK_SECRET=your-webhook-secret
uvicorn examples.chat.app:app --reload
```

Point a GitHub webhook (issue comments, `application/json`, with the same
secret) at `POST /webhooks/github`.

## Design notes

- **Thread = instance = session.** The chat thread id is used as both the agent
  instance id and the session name, so one thread maps to one continuing
  conversation with its own history.
- **`dispatch()` is async.** The webhook returns `202 {dispatch_id}` immediately;
  the agent processes the message in the background and replies through its tool.
  A dispatched message is an agent operation, not a workflow run.
- **Outbound is an explicit tool.** `reply_to_chat_thread` closes over the
  dispatched thread id, so the model only chooses the reply *text*, never the
  destination. Swap `SENT_REPLIES` for a real provider client (and add an
  idempotency key) in production.
- **Verify inbound, scope outbound.** The HMAC check rejects forged webhooks;
  the tool can only post to the thread the agent instance represents.
