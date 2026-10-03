# 👴 Dad Jokes Extension

> ⚠️ **EXPERIMENTAL** — This extension uses the experimental UI extensions feature from
> [OpenHands/agent-canvas](https://github.com/OpenHands/agent-canvas) (`feature/ui-extensions`
> branch). This feature is under active development and **may never be released** as a stable
> API.

Because every coding session needs more groaning. 🙄

## Features

### 🎯 Full-Width Page
Click "Dad Jokes" in the sidebar (below Automate) to open the Dad Jokes page:
- View a random programming/tech dad joke
- Click "Reveal Punchline" for the payoff
- Rate jokes with 😩 Groan or 😂 LOL buttons  
- Track your total groans on the Groan-O-Meter™
- See which conversation you're currently annoying

### 💬 Commands
Available in the Command-K menu:
- **🤣 Tell me a Dad Joke** — Get a random joke as a toast notification
- **👴 Words of Wisdom** — Receive dad-style coding encouragement

### 📋 Menu Items
- **Conversation tabs context menu** — Right-click any tab for a quick joke
- **Chat input actions** — Access "Words of Wisdom" from the chat ⊕ menu

### ⚙️ Settings Page
Navigate to Settings → Dad Jokes to:
- Set your Dad Name and Victim's Name for personalization
- Enable/disable joke categories (Programming, AI, Classic, Puns)
- View your Dad Stats (Total Groans, Dad Level)
- Reset your stats if you want to start fresh

## Sample Jokes

> **Why do programmers prefer dark mode?**
> Because light attracts bugs!

> **Why did the AI go to therapy?**
> It had too many deep issues!

> **What did the router say to the doctor?**
> It hurts when IP!

## Installation

### Requirements
1. Agent Canvas built from the `feature/ui-extensions` branch
2. `VITE_ENABLE_EXTENSIONS=true` build flag enabled

### Install from GitHub
In the Agent Canvas Extensions page (`/extensions`), click **Add** and enter:
```
gh:OpenHands/enterprise-cookbook/agent-canvas-extensions/dad-jokes@main
```

### Install from local development
For local testing, use the HTTPS URL to your dev server or file path.

## Permissions

This extension requests:
- **`conversation:read`** — To show the active conversation name in the panel
- **`storage`** — To persist your groan count and settings

## Extension Points Used

This extension demonstrates many of the available extension points:

| Point | Usage |
|-------|-------|
| `pages` | Full-width page in sidebar (like Customize/Automate) at `/x/dadjokes.groan/jokes` |
| `commands` | "Tell me a Dad Joke" and "Words of Wisdom" |
| `menus.conversationTabs/context` | Joke command in tab context menu |
| `menus.chatInput/actions` | Encouragement in chat actions menu |
| `settingsPages` | Custom settings page at `/settings/x/dadjokes.groan` |

## API Methods Used

| Method | Purpose |
|--------|---------|
| `conversation.getActive()` | Get current conversation for personalization |
| `storage.get/set()` | Persist groan count and preferences |
| `window.showInformationMessage()` | Display joke/encouragement toasts |
| `commands.register()` | Register the two commands |

## Development

The extension consists of:
- `extension.json` — Manifest declaring all contribution points
- `main.js` — Web Worker entry (commands, no DOM access)
- `panel.html` — Sandboxed webview for the main page
- `settings.html` — Sandboxed webview for the settings page
- `icon.svg` — Sidebar nav icon (mustache face)
- `package.json` — For npm publishing

## License

MIT — Go forth and spread dad humor responsibly.
