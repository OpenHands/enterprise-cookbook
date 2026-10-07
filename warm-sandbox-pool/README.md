# Warm Sandbox Pool

Demonstrates maintaining a pool of pre-initialized "warm" sandboxes that late-bind to conversations on demand, eliminating startup delays for end users.

**This example demonstrates a technique for deploying Ruby-based applications** using OpenHands Cloud APIs, showing that custom images are not the only viable approach for handling initialization that takes more than a few seconds.

## Context: Alternative to Custom Images

When applications have components that run outside the agent control loop and must be available on the system where the agent is running, a custom image is not the only mechanism for packaging these dependencies.

Even when using custom images in OpenHands Enterprise, some scenarios require additional tasks to be completed on the running sandbox to make it ready for use. **If these tasks take more than a few seconds, the Warm Sandbox Pool technique eliminates the apparent delay an end-user would see** by preparing a pool of pre-initialized sandboxes that late-bind to conversations when an end-user begins to interact with the agent.

This same approach can be used to install and prepare application services in sandboxes via API calls available in the OpenHands SaaS/Cloud platform, providing a viable alternative to custom images for your deployment needs.

## Concept

Instead of waiting for sandbox provisioning and initialization every time a user starts a conversation, this approach:

1. **Maintains a pool** of pre-initialized sandboxes (e.g., 3 sandboxes)
2. **Pre-installs dependencies** (Ruby, gems, application services) during sandbox preparation
3. **Late-binds conversations** - when a user needs a sandbox, one is pulled from the pool instantly
4. **Auto-refills** - when pool drops below threshold, new sandboxes are automatically provisioned and prepared in the background

This is particularly valuable when:
- Setup tasks take more than a few seconds (installing Ruby, gems, starting services)
- You want consistent, fast conversation startup times
- You need services running and ready before the agent starts working
- You're using the OpenHands SaaS/Cloud platform without custom images

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Web UI                              │
├──────────────────────────┬──────────────────────────────────┤
│   Pool Visualization     │    Conversation Interface        │
│                          │                                  │
│  🟢 Sandbox 1: READY     │  [Waiting for pool to be ready]  │
│  🟡 Sandbox 2: PREPARING │                                  │
│  🔴 Sandbox 3: STARTING  │  [Then: conversation input box]  │
└──────────────────────────┴──────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Pool Controller      │
              │   (Flask Backend)      │
              └────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ Sandbox  │      │ Sandbox  │      │ Sandbox  │
  │ (READY)  │      │ (READY)  │      │ (READY)  │
  └──────────┘      └──────────┘      └──────────┘
       │                 │                 │
       └─────────────────┴─────────────────┘
                         │
              Each sandbox has Ruby +
              Sinatra gem + demo service
```

## Demo Application: Ruby Sinatra Service

This example installs a Ruby/Sinatra web service in each sandbox to demonstrate the warm pool technique in a realistic scenario. **The Sinatra service demonstrates how Ruby-based systems** can be installed and running before agent conversations begin.

The initialization process (installing Ruby runtime, gems, starting the service) shows how to deploy application services using the same API-driven preparation approach for your specific use case.

## Quick Start

### Prerequisites

- Python 3.10+
- OpenHands Cloud API key (`OH_API_KEY`)
- `uv` or standard Python environment

### Installation

```bash
# Install dependencies
uv pip install -e .

# Or with pip
pip install -r requirements.txt
```

### Run the Demo

```bash
export OH_API_KEY=your_api_key_here
python pool_controller.py
```

Then open http://localhost:5000 in your browser.

> **Heads up: this creates real sandboxes.** The controller immediately starts
> `POOL_SIZE` sandboxes (default 3) and keeps topping the pool up as you use them.
> Try it with `POOL_SIZE=1 POOL_THRESHOLD=1` first. Press Ctrl-C (or send SIGTERM)
> to stop: every sandbox still sitting in the pool is deleted. Sandboxes already
> attached to a conversation are left running, like any other conversation sandbox.
> If the controller is killed with SIGKILL it cannot clean up, so check your sandbox
> list afterwards.

### What You'll See

1. **Initial State**: "Preparing pool..." message while the sandboxes initialize
   (roughly 10-30 seconds each when the platform has capacity, longer otherwise)
2. **Pool Visualization**: Real-time status of each sandbox:
   - 🔴 **STARTING**: OpenHands is provisioning the sandbox
   - 🟡 **PREPARING**: Installing Ruby, gems, starting service
   - 🟢 **READY**: Fully initialized and available
3. **Conversation UI**: Once pool is ready, type a message to start a conversation
4. **Sandbox Allocation**: A ready sandbox is pulled from the pool and attached to your conversation
5. **Auto-Refill**: Watch as a new sandbox automatically begins initializing to refill the pool

### Configuration

Environment variables:

- `OH_API_KEY`: OpenHands Cloud API key (required)
- `OH_API_BASE`: API base URL (default: `https://app.all-hands.dev`). Set this to
  point at a different OpenHands instance.
- `POOL_SIZE`: Target pool size (default: `3`)
- `POOL_THRESHOLD`: Trigger refill when pool drops below this (default: `2`)
- `SANDBOX_SPEC_ID`: Optional sandbox spec (runtime image) to use for pool sandboxes
- `INIT_TIMEOUT`: Seconds the init script may run in a sandbox (default: `300`)
- `MAX_FAILURES`: Stop refilling after this many provisioning failures in a row
  (default: `3`). A failed sandbox is deleted immediately, so a broken init script
  cannot silently create sandboxes forever.
- `HOST`: Address the web UI binds to (default: `127.0.0.1`). The UI has no
  authentication and can start conversations with your API key, so only widen this
  on a trusted network.
- `PORT`: Web server port (default: `5000`)

### Testing the Ruby Service

Once a sandbox is in READY state, the Sinatra service is listening on port 4567
**inside** the sandbox. That port is not exposed publicly, so reach it from the
sandbox itself. The easiest way is to start a conversation and ask the agent:

```
"Call the quote service running on localhost:4567 and show me today's quote"
```

## Implementation Details

### Sandbox Initialization Process

The controller uploads `sandbox_prep/init_ruby_service.sh` and
`sandbox_prep/quote_service.rb` to the sandbox's agent-server (`POST /api/file/upload`)
and runs the script (`POST /api/bash/execute_bash_command`). The script does the
following (sandboxes run as a non-root user, so it uses `sudo` for installs):

1. **Install Ruby**: `apt-get install ruby-full` (Ruby 3.3 on current sandbox images)
2. **Install Sinatra**: `gem install sinatra rackup puma`
3. **Deploy Service**: Copies the uploaded `quote_service.rb` into `/workspace/services`
4. **Start Service**: Launches the Sinatra app in the background on port 4567
5. **Verify**: Confirms the service responds to health checks

This simulates a realistic scenario where your agent needs specific tools/services pre-installed.

### Pool Management

The `PoolController` class handles:

- **Provisioning**: Creates sandboxes via OpenHands Cloud API
- **Monitoring**: Polls sandbox status until RUNNING
- **Initialization**: Executes preparation scripts via agent-server API
- **Queue Management**: Thread-safe queue of ready sandboxes
- **Auto-Refill**: Background thread maintains pool size, and stops after
  `MAX_FAILURES` consecutive failures
- **Cleanup**: Deletes failed sandboxes immediately and all unused pool sandboxes
  on shutdown
- **Conversation Binding**: Attaches conversations to pre-warmed sandboxes

### Real-Time Updates

The web UI uses Server-Sent Events (SSE) to stream pool state updates in real-time without polling.

## Files

```
warm-sandbox-pool/
├── README.md                          # This file
├── QUICKSTART.md                      # Short run-it-now guide
├── pool_controller.py                 # Flask backend + pool manager
├── requirements.txt                   # Python dependencies
├── pyproject.toml                     # Same dependencies, for uv
├── test_structure.py                  # Sanity check of the example's files
├── sandbox_prep/
│   ├── init_ruby_service.sh          # Bash script to initialize each sandbox
│   └── quote_service.rb              # Ruby/Sinatra demo service (uploaded to each sandbox)
├── static/
│   ├── app.js                        # Frontend JavaScript
│   └── styles.css                    # UI styling
└── templates/
    └── index.html                    # Main web page
```

## Use Cases

### 1. Custom Runtime Environments

Pre-install language runtimes (Ruby, Java, Go) that take time to set up:

```bash
# In init script (sandboxes run as a non-root user; use sudo for system installs)
sudo apt-get install -y ruby-full
sudo gem install rails bundler
```

### 2. Service Dependencies

Start databases, caches, or mock APIs before the agent runs:

```bash
# In init script
docker run -d -p 5432:5432 postgres
redis-server --daemonize yes
./mock-api-server &
```

### 3. Large Codebases

Clone and prepare large repositories with dependencies:

```bash
# In init script
git clone --depth 1 https://github.com/your-org/monorepo /workspace/project
cd /workspace/project
bundle install
npm install
make build
```

### 4. Custom Application Integration

Replace the demo Sinatra service with your actual application initialization:

```bash
# In init script (replace sandbox_prep/init_ruby_service.sh contents)
# Install Ruby (or use a runtime that already has it)
sudo apt-get install -y ruby-full

# Install your application gems
sudo gem install your_gem_name

# Initialize your application
cd /workspace
# ... your app-specific setup commands
bundle install

# Start your services in daemon mode
rails server -d -p 4567

# Wait for service to be ready
until curl -f http://localhost:4567/health; do
    echo "Waiting for application..."
    sleep 2
done

echo "✅ Application ready"
```

By pre-warming sandboxes with your application already running, agents can immediately interact with your services without the 30-60 second initialization delay on every conversation start.

## Benefits vs. Custom Images

| Approach | Pros | Cons |
|----------|------|------|
| **Custom Images** | Fastest cold start, baked-in dependencies | Requires OpenHands Enterprise, image build pipeline, version management |
| **Warm Sandbox Pool** | Works on SaaS, flexible initialization, no image builds | Requires pool management code, higher resource usage |
| **Just-in-Time Init** | Simplest code, minimal resources | Slow user experience, wait time on every conversation |

**Warm Sandbox Pool is ideal when**:
- You're on OpenHands SaaS/Cloud (custom images not available)
- Setup time is 10-60 seconds (too slow for UX, too fast to justify custom image complexity)
- You want flexibility to change initialization without rebuilding images
- You have predictable conversation volume

## Extending the Example

### Add More Preparation Steps

Edit `sandbox_prep/init_ruby_service.sh` to install additional tools:

```bash
# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install Python packages
pip install pandas numpy jupyter
```

### Customize the Demo Service

Replace `sandbox_prep/quote_service.rb` with your own Ruby application or gem.

### Adjust Pool Parameters

```bash
POOL_SIZE=5 POOL_THRESHOLD=3 INIT_TIMEOUT=600 python pool_controller.py
# Maintain 5 ready sandboxes, refill when below 3, allow 10 minutes for init
```

### Add Health Checks

Extend the initialization to verify services are actually ready:

```bash
# In init script
until curl -f http://localhost:4567/health; do
    echo "Waiting for service..."
    sleep 2
done
```

## Troubleshooting

### Pool Never Reaches Ready State

Check the controller's terminal output and the init log in the web UI. After
`MAX_FAILURES` failures in a row the controller stops creating sandboxes and the UI
says so. Common issues:
- Ruby installation timeout (increase `INIT_TIMEOUT`)
- Network issues downloading gems
- Insufficient sandbox resources

### Sandboxes Get Stuck in PREPARING

Failed sandboxes are deleted right away, so to debug the init script keep a
sandbox alive and run it by hand. Create one with the `start-sandbox/` example (or
`POST /api/v1/sandboxes`), then read its `session_api_key` and `AGENT_SERVER` URL
from `GET /api/v1/sandboxes?id=<sandbox-id>`:

```bash
AGENT=https://<agent-server-url>
KEY=<session-api-key>

curl -X POST "$AGENT/api/file/upload?path=/tmp/init.sh" \
  -H "X-Session-API-Key: $KEY" -F file=@sandbox_prep/init_ruby_service.sh
curl -X POST "$AGENT/api/file/upload?path=/tmp/quote_service.rb" \
  -H "X-Session-API-Key: $KEY" -F file=@sandbox_prep/quote_service.rb
curl -X POST "$AGENT/api/bash/execute_bash_command" \
  -H "X-Session-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"command": "bash /tmp/init.sh", "timeout": 300}'
```

Delete the sandbox when you are done (`DELETE /api/v1/sandboxes/<id>?sandbox_id=<id>`).

### High Resource Usage

Reduce `POOL_SIZE` or implement smarter pool management:
- Scale pool size based on time of day
- Implement idle timeout (destroy sandboxes after 30 min unused)
- Use pool only for peak hours, fall back to JIT otherwise

## Next Steps

1. **Production Deployment**: Add error handling, logging, metrics
2. **Persistent Storage**: Save pool state to Redis/database for crash recovery
3. **Multi-Tenant**: Separate pools per user/organization
4. **Dynamic Scaling**: Adjust pool size based on demand
5. **Cost Optimization**: Implement sandbox recycling (reset instead of destroy)

## Related Examples

- `start-sandbox/` - Basic sandbox provisioning
- `clone-and-attach/` - Conversation attachment patterns
- `upload-skills/` - Pre-loading agent skills

## License

MIT - See repository root LICENSE file
