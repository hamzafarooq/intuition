# Texting the assistant from a phone

**Deferred.** Phone access is out of scope for now; students talk to the assistant on the laptop. These notes are kept for when it comes back into scope.

The assistant always runs in Claude Code on the student's laptop, against the mock world. The phone is only a way to talk to it. There are two ways to connect, and students need one of them.

## Option 1: channels (closest to Instinct)

A channel is an MCP server that pushes messages from outside into a running Claude Code session and sends Claude's replies back. Students text a Telegram bot, or themselves on iMessage, and the assistant replies in the same chat.

Approvals come to the phone too. When the assistant tries to send an email or make a booking, the permission prompt is relayed to the chat. Telegram shows Approve and Deny buttons, and iMessage asks for `yes <id>` or `no <id>`. Whichever answer arrives first, phone or terminal, wins. This is the live demo for Lesson 4.

**Telegram** (any phone)
1. In Telegram, message @BotFather with `/newbot`, choose a name and a username ending in `bot`, and copy the token.
2. In Claude Code: `/plugin install telegram@claude-plugins-official`, then `/telegram:configure <token>`.
3. Restart with `claude --channels plugin:telegram@claude-plugins-official`.
4. Message the bot, get a pairing code, then `/telegram:access pair <code>` and `/telegram:access policy allowlist`.

**iMessage** (Mac and iPhone)
1. `/plugin install imessage@claude-plugins-official`.
2. Restart with `claude --channels plugin:imessage@claude-plugins-official`.
3. Allow Full Disk Access for the terminal when macOS asks.
4. Text yourself. Your own messages are let through automatically.

**fakechat** (no phone): `/plugin install fakechat@claude-plugins-official`, restart with `--channels plugin:fakechat@claude-plugins-official`, open http://localhost:8787. Useful for checking the setup; it doesn't relay approvals.

Requirements and caveats:
- Channels are a research preview, so commands may change.
- The plugins need Bun installed.
- They need a claude.ai login or a Console API key.
- Team and Enterprise organizations have channels off until an Owner enables them. Students should use personal Pro or Max accounts. Check the Home Depot account before relying on it for the demo.
- Messages only arrive while the Claude Code session is open.
- Only allowlisted senders get through. The docs warn that an ungated channel is a prompt-injection route, which makes a good point for Lesson 4.

## Option 2: Remote Control (fallback, no bot)

Remote Control lets you use a Claude Code session running on your laptop from the Claude app or claude.ai/code. Everything still runs on the laptop, including the MCP server and skills; the phone shows the chat and the approval prompts.

1. In a running session, type `/remote-control` (or `/rc`). From a terminal, `claude remote-control` also works and can show a QR code.
2. On the phone, scan the code, or open the Claude app, tap **Code** and pick the session.

It's less like Instinct, since you're using Claude Code's own interface rather than texting a contact, but it needs no setup beyond the Claude app.

## What to tell students before the workshop

- Install Claude Code, Python 3.11 or later, and Bun.
- Use a personal Claude account.
- Telegram users: create the bot in advance (2 minutes).
- If nothing works on the day: Remote Control.

## Sources

- [Channels](https://code.claude.com/docs/en/channels)
- [Channels reference: permission relay](https://code.claude.com/docs/en/channels-reference)
- [Remote Control](https://code.claude.com/docs/en/remote-control)
- [Official channel plugins](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins): the Telegram and iMessage servers declare `claude/channel/permission`; fakechat doesn't
