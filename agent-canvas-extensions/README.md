# Agent Canvas UI Extensions (Experimental)

> ⚠️ **EXPERIMENTAL** — The examples in this directory showcase experimental UI extension
> functionality from [OpenHands/agent-canvas](https://github.com/OpenHands/agent-canvas).
> This feature is under active development and **may never be released** as a stable API.
> The extension system, manifest format, and host APIs are subject to breaking changes
> without notice.

## What are UI Extensions?

UI extensions are small, sandboxed bundles that add custom UI to Agent Canvas (sidebar
panels, commands, menus) without modifying the host application. They run isolated from
your DOM, cookies, and credentials — every capability must be explicitly approved at
install time.

## Examples

| Extension | Description |
|-----------|-------------|
| [dad-jokes](./dad-jokes/) | 👴 Because every coding session needs more groaning — sidebar panel, commands, menus, and settings |

---

## Tutorial: Building Agent Canvas with Extensions Enabled

This step-by-step guide walks you through cloning Agent Canvas, enabling the experimental
extensions feature, and running a development build.

### Prerequisites

Before you begin, ensure you have:

- **Node.js 22.12.x or later** — Check with `node --version`
- **npm** — Comes with Node.js
- **uv** (Python package manager) — Install via `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Git** — For cloning the repository

### Step 1: Clone the Agent Canvas Repository

```bash
git clone https://github.com/OpenHands/agent-canvas.git
cd agent-canvas
```

### Step 2: Checkout the Extensions Feature Branch

The UI extensions feature lives on a dedicated branch:

```bash
git checkout feature/ui-extensions
```

### Step 3: Install Dependencies

```bash
npm install
```

This installs all Node.js dependencies. The first run may take a few minutes.

### Step 4: Enable the Extensions Feature Flag

Create or edit the `.env` file in the project root to enable extensions:

```bash
echo 'VITE_ENABLE_EXTENSIONS=true' >> .env
```

Or manually create `.env` with the following content:

```env
VITE_ENABLE_EXTENSIONS=true
```

> **Note:** This is a build-time flag. Vite inlines `VITE_*` variables during the build,
> so changing it requires restarting the dev server.

### Step 5: Start the Development Server

For the full local stack (frontend + backend + automation server):

```bash
npm run dev
```

Or for frontend-only development (if you have a separate backend):

```bash
npm run dev:frontend
```

The dev server typically starts at:
- **Frontend:** http://localhost:8000
- **Backend API:** http://localhost:8001

### Step 6: Verify Extensions Are Enabled

1. Open http://localhost:8000 in your browser
2. Look for **Extensions** in the left sidebar (below Skills)
3. Click it to open the Extensions page (`/extensions`)

If you see "The extensions feature is turned off...", double-check your `.env` file and
restart the dev server.

---

## Tutorial: Installing an Extension from This Repository

Once you have Agent Canvas running with extensions enabled, follow these steps to install
the Dad Jokes extension (or any extension hosted on GitHub).

### Step 1: Open the Extensions Page

Navigate to http://localhost:8000/extensions (or click **Extensions** in the sidebar).

You'll see your installed extensions, or an empty state if this is your first time.

### Step 2: Click "Add" to Install a New Extension

Click the **Add** button to open the install dialog.

### Step 3: Enter the GitHub Source Reference

In the "From a source ref" field, enter:

```
gh:OpenHands/enterprise-cookbook/agent-canvas-extensions/dad-jokes@main
```

**Understanding the format:**
- `gh:` — Prefix indicating a GitHub-hosted extension
- `OpenHands/enterprise-cookbook` — GitHub owner/repo
- `/agent-canvas-extensions/dad-jokes` — Path to the extension directory within the repo
- `@main` — Branch or tag (use `@v1.0.0` for a specific release)

### Step 4: Fetch and Review the Extension

Click **Fetch** (or press Enter). Agent Canvas will:

1. Download the `extension.json` manifest from the specified location
2. Validate the manifest structure
3. Display the extension details and requested permissions

You should see:
- **Name:** Dad Jokes
- **Publisher:** dadjokes
- **Permissions requested:**
  - `conversation:read` — Read the active conversation
  - `storage` — Store data on your device

### Step 5: Review and Approve Permissions

Read through the permissions carefully. This extension requests:

| Permission | Why it's needed |
|------------|-----------------|
| **conversation:read** | To show the active conversation name in the panel |
| **storage** | To persist your groan count and settings locally |

Click **Install** to grant these permissions and install the extension.

> **Security note:** Extensions run sandboxed and cannot access your DOM, cookies, network,
> or credentials beyond what you explicitly approve.

### Step 6: Verify Installation

After installation, you should see:

1. **Extensions page** — The Dad Jokes card appears with version and "Installed" status
2. **Sidebar rail** — A new 👴 mustache icon appears in the activity bar
3. **Command menu** — Press `Cmd+K` (Mac) or `Ctrl+K` (Windows/Linux) and search for "Dad Joke"

### Step 7: Use the Extension!

**Try the sidebar panel:**
1. Click the 👴 icon in the sidebar rail
2. Read the setup of a joke
3. Click "🥁 Reveal Punchline"
4. Rate it with 😩 Groan or 😂 LOL
5. Click "👴 Tell me another one, Dad!" for more

**Try the commands:**
1. Press `Cmd+K` / `Ctrl+K` to open the command menu
2. Type "dad" to filter commands
3. Select "🤣 Tell me a Dad Joke" — a toast notification appears with a joke

**Try the menu items:**
1. Right-click on any conversation tab
2. Look for "🤣 Tell me a Dad Joke" in the context menu

**Try the settings page:**
1. Go to Settings (gear icon)
2. Scroll down to find "Dad Jokes" in the navigation
3. Configure your Dad Name, joke categories, and view your stats

---

## Alternative Installation Methods

### From a Local Development Server

If you're developing an extension locally, you can serve it and install via HTTPS URL:

```bash
# In your extension directory
npx serve .
# Note the URL, e.g., http://localhost:3000
```

Then install using the full URL:
```
https://localhost:3000
```

### From npm (if published)

For extensions published to npm:

```
npm:@enterprise-cookbook/dad-jokes-extension@^1
```

### From a Specific Git Tag

For reproducible installs pinned to a release:

```
gh:OpenHands/enterprise-cookbook/agent-canvas-extensions/dad-jokes@v1.0.0
```

---

## Troubleshooting

### "The extensions feature is turned off"

1. Ensure `VITE_ENABLE_EXTENSIONS=true` is in your `.env` file
2. Restart the dev server (`Ctrl+C`, then `npm run dev`)
3. Hard refresh the browser (`Cmd+Shift+R` / `Ctrl+Shift+R`)

### Extension doesn't appear after installation

1. Check the browser console for errors (`F12` → Console tab)
2. Verify the extension manifest is valid JSON
3. Try refreshing the page

### "Failed to fetch extension"

1. Verify the GitHub URL is correct and the repo is public
2. Check your network connection
3. Ensure the branch/tag exists

### Panel shows "Error" or doesn't load

1. Check the browser console for CSP or loading errors
2. Verify all files referenced in `extension.json` exist
3. Ensure HTML files have valid structure

---

## Reference

For the authoritative documentation on the extension system, see the `feature/ui-extensions`
branch of [OpenHands/agent-canvas](https://github.com/OpenHands/agent-canvas):

- `docs/EXTENSIONS.md` — User guide for installing and managing extensions
- `docs/EXTENSION_POINTS.md` — Reference for all contribution points
- `src/extensions/README.md` — Internal architecture and module reference
- `docs/proposals/ui-extensions.md` — Design proposal and rationale

---

## Disclaimer

These examples are provided for experimentation and feedback purposes only. Do not build
production workflows that depend on this functionality until it reaches a stable release.
