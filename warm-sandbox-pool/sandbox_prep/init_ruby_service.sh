#!/bin/bash
# Sandbox initialization script for warm pool
# This demonstrates the preparation steps needed to make a sandbox ready for use
# In a production deployment, you would replace this with your actual application setup

set -e  # Exit on error

echo "🚀 Starting sandbox initialization..."
echo "📦 Sandbox ID: ${SANDBOX_ID:-unknown}"

# Sandboxes run as a non-root user with passwordless sudo
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo -n"
fi

# Run a quiet install step; print its log only if it fails
quiet() {
    if ! "$@" > /tmp/init_step.log 2>&1; then
        echo "❌ Failed: $*"
        tail -n 20 /tmp/init_step.log
        exit 1
    fi
}

# 1. Install Ruby (Debian package, much faster than building with rbenv)
echo "📥 Installing Ruby..."
quiet $SUDO apt-get update -qq
quiet $SUDO apt-get install -y -qq ruby-full ruby-dev build-essential
ruby --version

# 2. Install Sinatra (4.x needs rackup and puma to boot)
echo "💎 Installing Sinatra gem..."
quiet $SUDO gem install sinatra rackup puma --no-document

# 3. Create service directory
echo "📁 Setting up service directory..."
mkdir -p /workspace/services

# 4. Deploy the quote service (uploaded next to this script by the controller)
echo "📝 Deploying quote service..."
cp /tmp/quote_service.rb /workspace/services/quote_service.rb

# 5. Start the service in background
echo "🌐 Starting quote service on port 4567..."
cd /workspace/services
nohup ruby quote_service.rb > /tmp/quote_service.log 2>&1 &
SERVICE_PID=$!
echo "Service PID: $SERVICE_PID"

# 6. Wait for service to be ready (with timeout)
echo "⏳ Waiting for service to respond..."
TIMEOUT=30
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if curl -sf http://localhost:4567/health > /dev/null 2>&1; then
        echo "✅ Service is healthy!"
        curl -s http://localhost:4567/health | jq . || true
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "❌ Service failed to start within ${TIMEOUT}s"
    echo "Service log:"
    cat /tmp/quote_service.log
    exit 1
fi

# 7. Final verification - test the main endpoint
echo "🧪 Testing service endpoint..."
QUOTE_RESPONSE=$(curl -s http://localhost:4567/quote)
echo "Sample quote: $QUOTE_RESPONSE"

# 8. Create a marker file to indicate successful initialization
echo "✅ Creating ready marker..."
echo "$(date -Iseconds)" > /workspace/.sandbox_ready
echo "SANDBOX_ID=${SANDBOX_ID:-unknown}" >> /workspace/.sandbox_ready
echo "SERVICE_PID=$SERVICE_PID" >> /workspace/.sandbox_ready
echo "RUBY_VERSION=$(ruby --version)" >> /workspace/.sandbox_ready

echo ""
echo "🎉 Sandbox initialization complete!"
echo "📊 Summary:"
echo "   - Ruby: $(ruby --version | cut -d' ' -f2)"
echo "   - Sinatra: installed"
echo "   - Quote Service: running on port 4567 (PID $SERVICE_PID)"
echo "   - Status: READY"
echo ""
echo "💡 In production, replace this initialization with your application-specific setup"
