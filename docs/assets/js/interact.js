/* docs/assets/js/interact.js - Interactive Documentation with Cloud Bridge */

const WORKER_URL = 'https://omnipkg.1minds3t.workers.dev';
let PORT = 5000;
let isConnected = false;
let checkInterval = null;

document.addEventListener("DOMContentLoaded", function() {
    
    // Get port from URL hash (e.g., #5000)
    if (window.location.hash) {
        const val = parseInt(window.location.hash.substring(1));
        if (!isNaN(val) && val > 1024) PORT = val;
    }

    // Track page view
    sendTelemetry("page_view", {
        path: window.location.pathname,
        title: document.title,
        port: PORT
    });

    // Initialize UI
    createStatusBanner();
    injectRunButtons();
    startHealthCheck();
});

// ==========================================
// Connection Status Banner
// ==========================================
function createStatusBanner() {
    const banner = document.createElement('div');
    banner.id = 'omnipkg-status-banner';
    banner.innerHTML = `
        <div class="status-content">
            <span class="status-dot" id="status-dot"></span>
            <span id="status-text">Checking connection...</span>
            <button id="reconnect-btn" style="display:none;">Retry</button>
        </div>
    `;
    document.body.appendChild(banner);
    
    document.getElementById('reconnect-btn').onclick = () => checkHealth();
}

// ==========================================
// Health Check
// ==========================================
async function checkHealth() {
    try {
        const res = await fetch(`${WORKER_URL}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                port: PORT,
                endpoint: '/health',
                method: 'GET'
            })
        });
        
        if (res.ok) {
            const data = await res.json();
            updateStatus(true, `Connected to OmniPkg v${data.version || 'unknown'} (Port ${PORT})`);
        } else {
            updateStatus(false, 'Bridge not responding');
        }
    } catch (e) {
        updateStatus(false, 'Not connected - Run: omnipkg web start');
    }
}

function updateStatus(connected, message) {
    isConnected = connected;
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btn = document.getElementById('reconnect-btn');
    
    dot.className = connected ? 'status-dot connected' : 'status-dot';
    text.textContent = message;
    btn.style.display = connected ? 'none' : 'inline-block';
    
    // Enable/disable all run buttons
    document.querySelectorAll('.omni-run-btn').forEach(btn => {
        btn.disabled = !connected;
    });
}

function startHealthCheck() {
    checkHealth();
    checkInterval = setInterval(checkHealth, 5000); // Check every 5 seconds
}

// ==========================================
// Inject Run Buttons
// ==========================================
function injectRunButtons() {
    const codeBlocks = document.querySelectorAll('pre > code');

    codeBlocks.forEach((block) => {
        const text = block.innerText.trim();
        
        // Only add buttons for omnipkg/8pkg commands
        if (text.startsWith("omnipkg") || text.startsWith("8pkg")) {
            const button = document.createElement("button");
            button.className = "omni-run-btn";
            button.disabled = !isConnected;
            
            // Create button content with icon
            button.innerHTML = `
                <svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16">
                    <path fill="currentColor" d="M8 5v14l11-7z"/>
                </svg>
                <span>Run Command</span>
            `;
            
            button.onclick = () => runCommand(text, button);
            
            const preBlock = block.parentElement;
            preBlock.parentElement.insertBefore(button, preBlock.nextSibling);
        }
    });
}

// ==========================================
// Execute Command
// ==========================================
async function runCommand(cmd, btnElement) {
    if (!isConnected) {
        alert('Not connected to local bridge. Run: omnipkg web start');
        return;
    }
    
    const originalHTML = btnElement.innerHTML;
    btnElement.innerHTML = `
        <svg class="btn-icon spin" viewBox="0 0 24 24" width="16" height="16">
            <path fill="currentColor" d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z" />
        </svg>
        <span>Running...</span>
    `;
    btnElement.disabled = true;

    sendTelemetry("command_exec", { command: cmd.split(' ')[0] });

    try {
        const res = await fetch(`${WORKER_URL}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                port: PORT,
                endpoint: '/run',
                method: 'POST',
                data: { command: cmd }
            })
        });
        
        const data = await res.json();
        showOutput(cmd, data.output);
        
        btnElement.innerHTML = `
            <svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16">
                <path fill="currentColor" d="M9,20.42L2.79,14.21L5.62,11.38L9,14.77L18.88,4.88L21.71,7.71L9,20.42Z" />
            </svg>
            <span>Success</span>
        `;
        
        setTimeout(() => {
            btnElement.innerHTML = originalHTML;
            btnElement.disabled = false;
        }, 2000);
    } catch (e) {
        showOutput(cmd, `Error: ${e.message}`);
        btnElement.innerHTML = `
            <svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16">
                <path fill="currentColor" d="M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z" />
            </svg>
            <span>Failed</span>
        `;
        btnElement.disabled = false;
    }
}

// ==========================================
// Output Modal
// ==========================================
function showOutput(cmd, output) {
    // Remove existing modal if any
    const existing = document.getElementById('omni-output-modal');
    if (existing) existing.remove();
    
    const modal = document.createElement('div');
    modal.id = 'omni-output-modal';
    modal.innerHTML = `
        <div class="omni-modal-backdrop" onclick="this.parentElement.remove()"></div>
        <div class="omni-modal-content">
            <div class="omni-modal-header">
                <h3>Command Output</h3>
                <button class="omni-modal-close" onclick="this.closest('.omni-output-modal').remove()">×</button>
            </div>
            <div class="omni-modal-body">
                <div class="omni-command">$ ${cmd}</div>
                <pre class="omni-output">${output}</pre>
            </div>
            <div class="omni-modal-footer">
                <button onclick="navigator.clipboard.writeText('${output.replace(/'/g, "\\'")}')">Copy Output</button>
                <button onclick="this.closest('#omni-output-modal').remove()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

// ==========================================
// Telemetry (Privacy-Safe)
// ==========================================
function sendTelemetry(eventType, details) {
    fetch(`${WORKER_URL}/analytics/track`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            event_type: eventType,
            event_name: details.command || details.path || 'unknown',
            page: window.location.pathname,
            metadata: details
        })
    }).catch(() => {
        // Silently fail - don't interrupt user experience
    });
}