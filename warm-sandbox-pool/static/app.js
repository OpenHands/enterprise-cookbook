// Warm Sandbox Pool - Frontend JavaScript

class PoolUI {
    constructor() {
        this.poolReady = false;
        this.eventSource = null;
        this.initialize();
    }

    initialize() {
        this.setupEventStream();
        this.setupConversationForm();
    }

    setupEventStream() {
        // Connect to Server-Sent Events for real-time pool updates
        this.eventSource = new EventSource('/api/pool/events');

        this.eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.updateUI(data);
        };

        this.eventSource.onerror = (error) => {
            console.error('EventSource error:', error);
            // Try to reconnect after 5 seconds
            setTimeout(() => {
                if (this.eventSource.readyState === EventSource.CLOSED) {
                    this.setupEventStream();
                }
            }, 5000);
        };
    }

    updateUI(poolStatus) {
        // Update pool stats
        document.getElementById('pool-size').textContent = poolStatus.pool_size;
        document.getElementById('ready-count').textContent = poolStatus.ready_count;
        document.getElementById('threshold').textContent = poolStatus.threshold;

        // Update sandbox list
        this.renderSandboxes(poolStatus.sandboxes);

        document.getElementById('halted-notice').style.display =
            poolStatus.halted ? 'block' : 'none';

        // Check if pool is ready
        const wasReady = this.poolReady;
        this.poolReady = poolStatus.ready_count > 0;

        // Toggle conversation interface visibility
        if (this.poolReady && !wasReady) {
            this.showConversationInterface();
        } else if (!this.poolReady && wasReady) {
            this.hideConversationInterface();
        }
    }

    renderSandboxes(sandboxes) {
        const container = document.getElementById('sandbox-list');

        if (sandboxes.length === 0) {
            container.innerHTML = '<p style="text-align: center; color: #6b7280;">No sandboxes yet...</p>';
            return;
        }

        container.innerHTML = sandboxes.map(sb => this.renderSandboxCard(sb)).join('');
    }

    renderSandboxCard(sandbox) {
        const statusClass = `status-${sandbox.state.toLowerCase()}`;
        const statusEmoji = this.getStatusEmoji(sandbox.state);

        const duration = this.calculateDuration(sandbox.created_at, sandbox.ready_at);
        const logs = sandbox.init_log && sandbox.init_log.length > 0
            ? this.renderLogs(sandbox.init_log)
            : '';

        const conversationLink = sandbox.conversation_url
            ? `<a href="${this.escapeHtml(sandbox.conversation_url)}" target="_blank" class="conversation-link">
                   🔗 View Conversation
               </a>`
            : '';

        const errorMessage = sandbox.error_message
            ? `<div class="error-message">❌ ${this.escapeHtml(sandbox.error_message)}</div>`
            : '';

        return `
            <div class="sandbox-card">
                <div class="sandbox-header">
                    <span class="status-badge ${statusClass}">${statusEmoji} ${sandbox.state}</span>
                    <span class="sandbox-id">${this.shortId(sandbox.id)}</span>
                </div>
                ${duration ? `<div class="sandbox-details">⏱️ ${duration}</div>` : ''}
                ${logs}
                ${conversationLink}
                ${errorMessage}
            </div>
        `;
    }

    getStatusEmoji(state) {
        const emojis = {
            'STARTING': '🔴',
            'PREPARING': '🟡',
            'READY': '🟢',
            'ALLOCATED': '🟦',
            'FAILED': '⚠️'
        };
        return emojis[state] || '⚪';
    }

    renderLogs(logs) {
        const recentLogs = logs.slice(-5); // Last 5 lines
        return `
            <div class="sandbox-log">
                ${recentLogs.map(line =>
                    `<div class="sandbox-log-line">${this.escapeHtml(line)}</div>`
                ).join('')}
            </div>
        `;
    }

    calculateDuration(createdAt, readyAt) {
        if (!readyAt) return null;

        const created = new Date(createdAt);
        const ready = new Date(readyAt);
        const seconds = Math.floor((ready - created) / 1000);

        return `Ready in ${seconds}s`;
    }

    shortId(id) {
        return id.length > 12 ? `${id.substring(0, 8)}...${id.substring(id.length - 4)}` : id;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    showConversationInterface() {
        document.getElementById('waiting-state').style.display = 'none';
        document.getElementById('ready-state').style.display = 'block';
    }

    hideConversationInterface() {
        document.getElementById('waiting-state').style.display = 'block';
        document.getElementById('ready-state').style.display = 'none';
    }

    setupConversationForm() {
        const form = document.getElementById('message-input');
        const button = document.getElementById('start-btn');
        const resultArea = document.getElementById('result-area');

        // Set default message
        form.value = "Check if the quote service is running on localhost:4567 and fetch me a random quote. Show me the result.";

        button.addEventListener('click', async () => {
            const message = form.value.trim();

            if (!message) {
                alert('Please enter a message');
                return;
            }

            if (!this.poolReady) {
                alert('Pool is not ready yet. Please wait for at least one sandbox to be ready.');
                return;
            }

            // Disable button during request
            button.disabled = true;
            button.textContent = '⏳ Starting...';

            try {
                const response = await fetch('/api/conversation/start', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ message })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || 'Failed to start conversation');
                }

                // Show success result
                this.showResult(data, false);

                // Clear input
                form.value = '';

            } catch (error) {
                console.error('Error starting conversation:', error);
                this.showResult({ error: error.message }, true);
            } finally {
                button.disabled = false;
                button.textContent = '🚀 Start Conversation';
            }
        });
    }

    showResult(data, isError) {
        const resultArea = document.getElementById('result-area');

        if (isError) {
            resultArea.className = 'result-area error';
            resultArea.innerHTML = `
                <h3>❌ Error</h3>
                <p>${this.escapeHtml(data.error)}</p>
            `;
        } else {
            resultArea.className = 'result-area';
            resultArea.innerHTML = `
                <h3>✅ ${this.escapeHtml(data.message)}</h3>
                <p><strong>Sandbox ID:</strong> <code>${this.escapeHtml(data.sandbox_id)}</code></p>
                <p><strong>Conversation ID:</strong> <code>${this.escapeHtml(data.conversation_id)}</code></p>
                <a href="${this.escapeHtml(data.conversation_url)}" target="_blank">
                    🔗 Open Conversation in OpenHands
                </a>
                <p style="margin-top: 16px; font-size: 0.875rem; color: #6b7280;">
                    The pool will automatically provision a new sandbox to refill the allocated slot.
                </p>
            `;
        }

        resultArea.style.display = 'block';

        // Scroll to result
        resultArea.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    cleanup() {
        if (this.eventSource) {
            this.eventSource.close();
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const poolUI = new PoolUI();

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        poolUI.cleanup();
    });
});
