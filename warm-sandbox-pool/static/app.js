// Warm Sandbox Pool - Frontend JavaScript

class PoolUI {
    constructor() {
        this.everReady = false;
        this.readyCount = 0;
        this.requestInFlight = false;
        this.lastEventSeq = 0;
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
            // A 401 (e.g. the controller restarted and issued a new access code)
            // looks like a plain stream error, so ask the API directly.
            fetch('/api/pool/status').then(response => {
                if (response.status === 401) window.location.reload();
            });
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

        const allocated = poolStatus.sandboxes.filter(sb => sb.state === 'ALLOCATED');
        this.renderSandboxes(poolStatus.sandboxes.filter(sb => sb.state !== 'ALLOCATED'));
        this.renderClaimed(allocated);
        this.renderStats(poolStatus.stats);
        this.renderRefillLine(poolStatus);
        this.renderEvents(poolStatus.events);

        document.getElementById('halted-notice').style.display =
            poolStatus.halted ? 'block' : 'none';

        // Once the pool has been ready, keep the form visible: an empty pool
        // between claim and refill is the interesting moment, not a reset.
        this.readyCount = poolStatus.ready_count;
        if (this.readyCount > 0 && !this.everReady) {
            this.everReady = true;
            this.showConversationInterface();
        }
        const refilling = this.everReady && this.readyCount === 0 && !poolStatus.halted;
        document.getElementById('refill-notice').style.display = refilling ? 'block' : 'none';
        document.getElementById('ready-info').style.display = refilling ? 'none' : 'block';
        this.syncStartButton();
    }

    syncStartButton() {
        const button = document.getElementById('start-btn');
        button.disabled = this.requestInFlight || this.readyCount === 0;
    }

    formatSeconds(value, digits = 0) {
        return value === null || value === undefined ? '-' : `${value.toFixed(digits)}s`;
    }

    renderStats(stats) {
        document.getElementById('claim-count').textContent = stats.claims;
        document.getElementById('avg-warmup').textContent =
            this.formatSeconds(stats.avg_warmup_seconds);
        document.getElementById('avg-attach').textContent =
            this.formatSeconds(stats.avg_attach_seconds, 1);
    }

    renderRefillLine(status) {
        const inProgress = status.sandboxes.filter(
            sb => sb.state === 'STARTING' || sb.state === 'PREPARING'
        ).length;
        let text;
        if (status.halted) {
            text = '⛔ Refilling stopped after repeated failures';
        } else if (inProgress > 0) {
            const verb = this.everReady || status.stats.claims > 0 ? 'Refilling' : 'Filling';
            text = `🔄 ${verb}: ${inProgress} in progress (${status.ready_count} ready, target ${status.pool_size})`;
        } else if (status.ready_count >= status.pool_size) {
            text = `✅ Pool is full: ${status.ready_count} ready`;
        } else if (status.ready_count >= status.threshold) {
            text = `⏸ ${status.ready_count} of ${status.pool_size} ready. A refill starts when ready drops below ${status.threshold}.`;
        } else {
            text = '🔄 Below the threshold: a refill starts within a few seconds';
        }
        document.getElementById('refill-line').textContent = text;
    }

    renderClaimed(sandboxes) {
        const container = document.getElementById('claimed-list');
        if (sandboxes.length === 0) {
            container.innerHTML = '<p class="empty-note">Nothing claimed yet.</p>';
            return;
        }
        container.innerHTML = sandboxes.slice().reverse().map(sb => {
            const link = sb.conversation_url
                ? `<a href="${this.escapeHtml(sb.conversation_url)}" target="_blank" class="conversation-link">🔗 Conversation</a>`
                : '<span class="conversation-link">attaching...</span>';
            return `
                <div class="claimed-row">
                    <code>${this.shortId(sb.id)}</code>${link}
                    <div class="claimed-timing">
                        warm ${this.formatSeconds(sb.warmup_seconds)} ·
                        waited in pool ${this.formatSeconds(sb.idle_seconds)} ·
                        attach ${this.formatSeconds(sb.attach_seconds, 1)}
                    </div>
                </div>`;
        }).join('');
    }

    renderEvents(events) {
        const last = events.length ? events[events.length - 1].seq : 0;
        if (last === this.lastEventSeq) return;
        this.lastEventSeq = last;

        const emojis = {
            created: '➕', preparing: '🛠️', ready: '🟢', allocated: '🟦',
            claimed: '✅', failed: '⚠️', deleted: '🗑️', halted: '⛔',
            miss: '🙅', shutdown: '🛑', refill: '🔄'
        };
        document.getElementById('event-log').innerHTML = events.slice().reverse().map(e => `
            <div class="event-row event-kind-${this.escapeHtml(e.kind)}">
                <span class="event-time">${this.escapeHtml(e.timestamp.substring(11, 19))}</span>
                <span>${emojis[e.kind] || '•'}</span>
                <span class="event-sandbox">${e.sandbox_id ? this.shortId(e.sandbox_id) : ''}</span>
                <span class="event-message">${this.escapeHtml(e.message)}</span>
            </div>`).join('');
    }

    renderSandboxes(sandboxes) {
        const container = document.getElementById('sandbox-list');

        if (sandboxes.length === 0) {
            container.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">No sandboxes yet...</p>';
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

            this.requestInFlight = true;
            this.syncStartButton();
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
                this.requestInFlight = false;
                this.syncStartButton();
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
                <p style="margin-top: 16px; font-size: 0.875rem; color: var(--text-secondary);">
                    The pool refills automatically once its ready sandboxes drop below
                    the threshold. Watch the activity feed below.
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
