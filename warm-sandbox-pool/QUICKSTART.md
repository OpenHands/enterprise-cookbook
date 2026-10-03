# Quick Start Guide

Get the Warm Sandbox Pool demo running in 5 minutes.

## Prerequisites

- **OpenHands Cloud API Key** - Get one from https://app.all-hands.dev
- **Python 3.10+** - Check with `python --version`
- **Git** - To clone this repository

## Installation (30 seconds)

```bash
# Clone the repository
git clone https://github.com/OpenHands/enterprise-cookbook.git
cd enterprise-cookbook/warm-sandbox-pool

# Install Python dependencies (use uv or pip)
pip install -r requirements.txt

# Or with uv (faster)
uv pip install -e .
```

## Run the Demo (10 seconds)

```bash
# Set your API key
export OH_API_KEY=your_api_key_here

# Start the pool controller
python pool_controller.py

# You should see:
# INFO pool_controller: Pool controller initialized: size=3, threshold=2
# INFO pool_controller: Pool manager thread started
# INFO pool_controller: Starting web server on port 5000
# INFO pool_controller: Open http://localhost:5000 in your browser
```

## Use the Demo (2-3 minutes)

1. **Open http://localhost:5000** in your browser

2. **Watch the pool initialize**:
   - You'll see "Preparing pool..." message
   - 3 sandboxes will start initializing (takes ~30-60 seconds each)
   - Watch them progress: 🔴 STARTING → 🟡 PREPARING → 🟢 READY

3. **Start a conversation**:
   - Once at least one sandbox is READY, the conversation UI appears
   - Type a message or use the default:
     ```
     Check if the quote service is running on localhost:4567 and fetch me a random quote.
     ```
   - Click "🚀 Start Conversation"

4. **Observe the magic**:
   - A ready sandbox is instantly pulled from the pool
   - OpenHands conversation starts immediately (no wait!)
   - Click the conversation link to see your agent working
   - Watch the pool automatically provision a new sandbox to refill

## What's Happening?

```
Before (Without Warm Pool):
User clicks "Start" → Wait 30-60s for sandbox → Agent starts
                       ⏳ User sees delay

With Warm Pool:
User clicks "Start" → Instant sandbox → Agent starts
                       ⚡ No apparent delay
                       (Pool refills in background)
```

## Try These Commands in Your Conversation

Once your conversation is attached to a warm sandbox:

```
"Show me the Ruby version installed"
→ Should show Ruby 3.x already installed

"Check if the quote service is running on port 4567"
→ Should show it's already running

"Fetch a quote from localhost:4567/quote and show it to me"
→ Should return a programming quote from the API

"Get all quotes from localhost:4567/quotes and count them"
→ Should list 8 quotes

"Find quotes by Martin Fowler using the API"
→ Should search the quotes service
```

## Next Steps

### For Demo/Learning
- Experiment with different pool sizes: `POOL_SIZE=5 python pool_controller.py`
- Watch the auto-refill behavior by starting multiple conversations
- Check the sandbox init logs in the web UI

### For Custom Application Deployment
1. **Customize** `sandbox_prep/init_ruby_service.sh` with your application setup
2. **Test** the initialization manually before running the full pool
3. **Adjust** pool size and timing parameters for your needs
4. **Deploy** your adapted pool controller

## Configuration

Customize via environment variables:

```bash
# Basic config
export OH_API_KEY=your_key
export OH_API_BASE=https://app.all-hands.dev  # or your instance
export PORT=5000                               # Web UI port

# Pool sizing
export POOL_SIZE=3          # How many ready sandboxes to maintain
export POOL_THRESHOLD=2     # Start refilling when ready count drops below this

# Sandbox config (optional)
export SANDBOX_SPEC_ID=your_spec_id  # Use custom runtime image
```

## Troubleshooting

### "No ready sandboxes available" error
- **Wait longer**: Initial pool fill takes 1-3 minutes
- **Check API key**: Make sure `OH_API_KEY` is valid
- **View logs**: Look at terminal output for error messages
- **Check web UI**: Look for FAILED sandboxes and error messages

### Sandboxes stuck in PREPARING
- **Check init script**: May be timing out or failing
- **Increase timeout**: Set higher init timeout in code
- **Test manually**: Use `start-sandbox/` example to debug

### "OH_API_KEY is required" error
```bash
# Set your API key before running
export OH_API_KEY=your_actual_key_here
python pool_controller.py
```

### Port already in use
```bash
# Use a different port
PORT=8080 python pool_controller.py
```

## Architecture at a Glance

```
┌─────────────────┐
│  Browser (You)  │
└────────┬────────┘
         │ http://localhost:5000
         ▼
┌─────────────────────────┐
│ Flask Web Server        │
│ - Serves UI             │
│ - REST API              │
│ - SSE for real-time     │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Pool Controller         │
│ - Manages 3 sandboxes   │
│ - Auto-refills pool     │
│ - Attaches conversations│
└────────┬────────────────┘
         │
    ┌────┴────┬────────┐
    ▼         ▼        ▼
┌────────┐ ┌────────┐ ┌────────┐
│Sandbox1│ │Sandbox2│ │Sandbox3│
│ Ruby ✓ │ │ Ruby ✓ │ │ Ruby ✓ │
│ Gems ✓ │ │ Gems ✓ │ │ Gems ✓ │
│Service✓│ │Service✓│ │Service✓│
│ READY  │ │ READY  │ │ READY  │
└────────┘ └────────┘ └────────┘
```

## Files Overview

```
warm-sandbox-pool/
├── README.md                      📖 Comprehensive documentation
├── QUICKSTART.md                  ⚡ This file - get started fast
├── pool_controller.py             🎛️  Main backend (Flask + pool logic)
├── requirements.txt               📦 Python dependencies
├── pyproject.toml                 📦 UV-compatible project config
├── sandbox_prep/
│   ├── init_ruby_service.sh      🔧 Demo: Ruby/Sinatra init
│   └── quote_service.rb          💎 Demo Ruby service
├── static/
│   ├── app.js                    🎨 Frontend JavaScript
│   └── styles.css                🎨 UI styling
└── templates/
    └── index.html                📄 Web UI template
```

## Getting Help

- **Documentation**: Read `README.md` for full details
- **Example Code**: All files are heavily commented
- **Related Examples**: Check `../start-sandbox/` and `../clone-and-attach/`

## Success Criteria

You've successfully run the demo when:

✅ Pool shows 3 sandboxes in READY state  
✅ You can start a conversation and get instant allocation  
✅ The conversation link opens in OpenHands  
✅ The agent can interact with the pre-installed quote service  
✅ Pool automatically provisions a replacement sandbox  

**Total time from zero to working demo: ~5 minutes**

Now you understand how warm sandbox pools work and can adapt this technique for your own application deployment!
