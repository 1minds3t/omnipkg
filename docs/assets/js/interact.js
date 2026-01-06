/* docs/assets/js/interact.js - SECURITY HARDENED VERSION */

const WORKER_URL = 'https://omnipkg.1minds3t.workers.dev';
let PORT = 5000;
let isConnected = false;
let checkInterval = null;
const DEBUG = true;

// Allowlist of safe telemetry keys
const SAFE_TELEMETRY_KEYS = new Set([
    'command', 'path', 'title', 'port', 'package', 
    'method', 'error', 'duration', 'success'
]);

// Debug logger
function debug(...args) {
    if (DEBUG) {
        console.log('[OmniPkg Debug]', ...args);
    }
}

// XSS Protection: Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", function() {
    debug('DOM loaded, initializing...');
    
    // Get port from URL hash (e.g., #5000)
    if (window.location.hash) {
        const val = parseInt(window.location.hash.substring(1));
        if (!isNaN(val) && val > 1024 && val < 65536) {
            PORT = val;
            debug(`Port set from URL hash: ${PORT}`);
        }
    }
    
    debug(`Worker URL: ${WORKER_URL}`);
    debug(`Target Port: ${PORT}`);
    debug(`Current Origin: ${window.location.origin}`);

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
    debug('Creating status banner...');
    const banner = document.createElement('div');
    banner.id = 'omnipkg-status-banner';
    banner.className = 'omnipkg-status-banner';
    
    const content = document.createElement('div');
    content.className = 'status-content';
    
    const dot = document.createElement('span');
    dot.className = 'status-dot';
    dot.id = 'status-dot';
    
    const text = document.createElement('span');
    text.id = 'status-text';
    text.textContent = 'Checking connection...';
    
    const btn = document.createElement('button');
    btn.id = 'reconnect-btn';
    btn.textContent = 'Retry';
    btn.style.display = 'none';
    btn.onclick = () => {
        debug('Manual retry clicked');
        checkHealth();
    };
    
    content.appendChild(dot);
    content.appendChild(text);
    content.appendChild(btn);
    banner.appendChild(content);
    document.body.appendChild(banner);
    debug('Status banner created');
}

// ==========================================
// Health Check
// ==========================================
async function checkHealth() {
    debug(`Checking health: ${WORKER_URL}/proxy -> localhost:${PORT}/health`);
    
    try {
        const requestBody = {
            port: PORT,
            endpoint: '/health',
            method: 'GET'
        };
        
        debug('Sending health check request:', requestBody);
        
        const res = await fetch(`${WORKER_URL}/proxy`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestBody)
        });
        
        debug(`Health check response status: ${res.status}`);
        debug(`Response headers:`, Object.fromEntries(res.headers.entries()));
        
        if (res.ok) {
            const data = await res.json();
            debug('Health check data:', data);
            updateStatus(true, `Connected to OmniPkg v${data.version || 'unknown'} (Port ${PORT})`);
        } else {
            const errorText = await res.text();
            debug('Health check failed:', errorText);
            updateStatus(false, `Bridge error (HTTP ${res.status})`);
        }
    } catch (e) {
        debug('Health check exception:', e);
        console.error('[OmniPkg] Connection Error:', e);
        
        let errorMsg = 'Not connected';
        if (e.message.includes('fetch')) {
            errorMsg += ' - Network error';
        } else if (e.message.includes('CORS')) {
            errorMsg += ' - CORS issue';
        }
        errorMsg += ' | Run: omnipkg web start';
        
        updateStatus(false, errorMsg);
    }
}

function updateStatus(connected, message) {
    isConnected = connected;
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btn = document.getElementById('reconnect-btn');
    
    debug(`Status update: ${connected ? 'CONNECTED' : 'DISCONNECTED'} - ${message}`);
    
    if (dot && text && btn) {
        dot.className = connected ? 'status-dot connected' : 'status-dot';
        text.textContent = message;
        btn.style.display = connected ? 'none' : 'inline-block';
        
        // Enable/disable all run buttons
        document.querySelectorAll('.omni-run-btn').forEach(btn => {
            btn.disabled = !connected;
        });
        
        // Enable/disable install button if it exists
        const installBtn = document.getElementById('install-btn');
        if (installBtn) {
            installBtn.disabled = !connected;
        }
    }
}

function startHealthCheck() {
    debug('Starting health check interval (every 5s)');
    checkHealth();
    checkInterval = setInterval(checkHealth, 5000);
}

// ==========================================
// Inject Run Buttons
// ==========================================
function injectRunButtons() {
    const codeBlocks = document.querySelectorAll('pre > code');
    debug(`Found ${codeBlocks.length} code blocks, scanning for omnipkg commands...`);

    let buttonCount = 0;
    codeBlocks.forEach((block) => {
        const text = block.innerText.trim();
        
        // Only add buttons for omnipkg/8pkg commands
        if (text.startsWith("omnipkg") || text.startsWith("8pkg")) {
            buttonCount++;
            debug(`Adding run button for command: ${text.substring(0, 50)}...`);
            
            const button = document.createElement("button");
            button.className = "omni-run-btn";
            button.disabled = !isConnected;
            
            const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            icon.setAttribute("class", "btn-icon");
            icon.setAttribute("viewBox", "0 0 24 24");
            icon.setAttribute("width", "16");
            icon.setAttribute("height", "16");
            
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("fill", "currentColor");
            path.setAttribute("d", "M8 5v14l11-7z");
            icon.appendChild(path);
            
            const span = document.createElement("span");
            span.textContent = "Run Command";
            
            button.appendChild(icon);
            button.appendChild(span);
            button.onclick = () => runCommand(text, button);
            
            const preBlock = block.parentElement;
            if (preBlock && preBlock.parentElement) {
                preBlock.parentElement.insertBefore(button, preBlock.nextSibling);
            }
        }
    });
    
    debug(`Injected ${buttonCount} run buttons`);
}

// ==========================================
// 🔒 SECURE: Install OmniPkg via Proxy
// ==========================================
async function installOmnipkg() {
    debug('Installing OmniPkg via secure endpoint...');
    
    const button = document.getElementById('install-btn');
    const output = document.getElementById('install-output');
    
    if (!button || !output) {
        console.error('Install UI elements not found');
        return;
    }
    
    // Check connection first
    if (!isConnected) {
        output.textContent = '❌ Not connected to local bridge. Run: omnipkg web start';
        return;
    }
    
    // Disable button during install
    button.disabled = true;
    button.textContent = '⏳ Installing...';
    output.textContent = 'Installing OmniPkg from PyPI...';
    
    try {
        // 🔒 SECURITY: Use the Cloudflare Worker proxy
        const requestBody = {
            port: PORT,  // ✅ Fixed: Was 'localPort' (undefined)
            endpoint: '/install-omnipkg',
            method: 'POST',
            data: {}
        };
        
        debug('Sending install request:', requestBody);
        
        const response = await fetch(`${WORKER_URL}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });
        
        debug(`Install response status: ${response.status}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${await response.text()}`);
        }
        
        const data = await response.json();
        output.textContent = data.output || '✅ Installation complete';
        
        // Track successful install
        sendTelemetry("install", { package: "omnipkg", method: "web_button" });
        
        // Reset button
        button.disabled = false;
        button.textContent = '📦 Install OmniPkg';
        
    } catch (error) {
        debug('Install error:', error);
        output.textContent = `❌ Installation failed: ${error.message}`;
        button.disabled = false;
        button.textContent = '📦 Install OmniPkg (Retry)';
        
        sendTelemetry("install_failed", { package: "omnipkg", error: error.message });
    }
}

// Make installOmnipkg globally accessible for onclick handlers
window.installOmnipkg = installOmnipkg;

// ==========================================
// Execute Command
// ==========================================
async function runCommand(cmd, btnElement) {
    debug(`Running command: ${cmd}`);
    
    if (!isConnected) {
        alert('Not connected to local bridge. Run: omnipkg web start');
        return;
    }
    
    // Store original content
    const originalContent = Array.from(btnElement.childNodes);
    
    // Update button to loading state
    btnElement.innerHTML = '';
    const spinIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    spinIcon.setAttribute("class", "btn-icon spin");
    spinIcon.setAttribute("viewBox", "0 0 24 24");
    spinIcon.setAttribute("width", "16");
    spinIcon.setAttribute("height", "16");
    const spinPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    spinPath.setAttribute("fill", "currentColor");
    spinPath.setAttribute("d", "M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z");
    spinIcon.appendChild(spinPath);
    const loadingText = document.createElement("span");
    loadingText.textContent = "Running...";
    btnElement.appendChild(spinIcon);
    btnElement.appendChild(loadingText);
    btnElement.disabled = true;

    sendTelemetry("command_exec", { command: cmd.split(' ')[0] });

    try {
        const requestBody = {
            port: PORT,
            endpoint: '/run',
            method: 'POST',
            data: { command: cmd }
        };
        
        debug('Sending command request:', requestBody);
        
        const res = await fetch(`${WORKER_URL}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });
        
        debug(`Command response status: ${res.status}`);
        
        const contentType = res.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await res.json();
        } else {
            data = { output: await res.text() };
        }
        
        debug('Command response:', data);
        showOutput(cmd, data.output || 'No output');
        
        // Success state
        btnElement.innerHTML = '';
        const checkIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        checkIcon.setAttribute("class", "btn-icon");
        checkIcon.setAttribute("viewBox", "0 0 24 24");
        checkIcon.setAttribute("width", "16");
        checkIcon.setAttribute("height", "16");
        const checkPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        checkPath.setAttribute("fill", "currentColor");
        checkPath.setAttribute("d", "M9,20.42L2.79,14.21L5.62,11.38L9,14.77L18.88,4.88L21.71,7.71L9,20.42Z");
        checkIcon.appendChild(checkPath);
        const successText = document.createElement("span");
        successText.textContent = "Success";
        btnElement.appendChild(checkIcon);
        btnElement.appendChild(successText);
        
        setTimeout(() => {
            btnElement.innerHTML = '';
            originalContent.forEach(node => btnElement.appendChild(node.cloneNode(true)));
            btnElement.disabled = false;
        }, 2000);
    } catch (e) {
        debug('Command execution error:', e);
        console.error('[OmniPkg] Command Error:', e);
        showOutput(cmd, `Error: ${e.message}`);
        
        // Error state
        btnElement.innerHTML = '';
        const errorIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        errorIcon.setAttribute("class", "btn-icon");
        errorIcon.setAttribute("viewBox", "0 0 24 24");
        errorIcon.setAttribute("width", "16");
        errorIcon.setAttribute("height", "16");
        const errorPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        errorPath.setAttribute("fill", "currentColor");
        errorPath.setAttribute("d", "M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z");
        errorIcon.appendChild(errorPath);
        const errorText = document.createElement("span");
        errorText.textContent = "Failed";
        btnElement.appendChild(errorIcon);
        btnElement.appendChild(errorText);
        btnElement.disabled = false;
    }
}

// ==========================================
// Output Modal (XSS-Safe)
// ==========================================
function showOutput(cmd, output) {
    debug('Showing output modal');
    
    // Remove existing modal if any
    const existing = document.getElementById('omni-output-modal');
    if (existing) existing.remove();
    
    // Create modal structure with DOM methods (no innerHTML)
    const modal = document.createElement('div');
    modal.id = 'omni-output-modal';
    
    const backdrop = document.createElement('div');
    backdrop.className = 'omni-modal-backdrop';
    backdrop.onclick = () => modal.remove();
    
    const content = document.createElement('div');
    content.className = 'omni-modal-content';
    
    // Header
    const header = document.createElement('div');
    header.className = 'omni-modal-header';
    const title = document.createElement('h3');
    title.textContent = 'Command Output';
    const closeBtn = document.createElement('button');
    closeBtn.className = 'omni-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = () => modal.remove();
    header.appendChild(title);
    header.appendChild(closeBtn);
    
    // Body
    const body = document.createElement('div');
    body.className = 'omni-modal-body';
    const cmdDiv = document.createElement('div');
    cmdDiv.className = 'omni-command';
    cmdDiv.textContent = `$ ${cmd}`;
    const outputPre = document.createElement('pre');
    outputPre.className = 'omni-output';
    outputPre.textContent = output;
    body.appendChild(cmdDiv);
    body.appendChild(outputPre);
    
    // Footer
    const footer = document.createElement('div');
    footer.className = 'omni-modal-footer';
    const copyBtn = document.createElement('button');
    copyBtn.textContent = 'Copy Output';
    copyBtn.onclick = () => {
        navigator.clipboard.writeText(output).then(() => {
            copyBtn.textContent = 'Copied!';
            setTimeout(() => copyBtn.textContent = 'Copy Output', 2000);
        });
    };
    const closeBtn2 = document.createElement('button');
    closeBtn2.textContent = 'Close';
    closeBtn2.onclick = () => modal.remove();
    footer.appendChild(copyBtn);
    footer.appendChild(closeBtn2);
    
    // Assemble
    content.appendChild(header);
    content.appendChild(body);
    content.appendChild(footer);
    modal.appendChild(backdrop);
    modal.appendChild(content);
    document.body.appendChild(modal);
}

// ==========================================
// 🛡️ Telemetry Data Sanitization
// ==========================================
function sanitizeTelemetryData(details) {
    if (!details || typeof details !== 'object') {
        return {};
    }
    
    const sanitized = {};
    
    for (const [key, value] of Object.entries(details)) {
        // Only allow safe keys
        if (!SAFE_TELEMETRY_KEYS.has(key)) {
            debug(`Telemetry: Skipping unsafe key '${key}'`);
            continue;
        }
        
        let cleanValue = String(value);
        
        // Truncate long values
        if (cleanValue.length > 200) {
            cleanValue = cleanValue.substring(0, 200) + '...[truncated]';
        }
        
        // Remove file paths
        cleanValue = cleanValue.replace(/[A-Za-z]:\\Users\\[^\s]+/g, '[PATH]');
        cleanValue = cleanValue.replace(/\/home\/[^\s]+/g, '[PATH]');
        
        // Remove IP addresses
        cleanValue = cleanValue.replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, '[IP]');
        
        // Remove potential tokens/keys
        cleanValue = cleanValue.replace(/\b[A-Za-z0-9_-]{16,}\b/g, '[REDACTED]');
        
        sanitized[key] = cleanValue;
    }
    
    return sanitized;
}

// ==========================================
// Telemetry (Privacy-Safe)
// ==========================================
function sendTelemetry(eventType, details) {
    if (typeof eventType !== 'string') {
        debug('Telemetry: Invalid event type');
        return;
    }
    
    // Sanitize the metadata
    const safeMetadata = sanitizeTelemetryData(details);
    
    debug('Sending telemetry:', eventType, safeMetadata);
    
    const payload = {
        event_type: eventType,
        event_name: safeMetadata.command || safeMetadata.path || 'unknown',
        page: window.location.pathname,
        metadata: safeMetadata
    };
    
    fetch(`${WORKER_URL}/analytics/track`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch((e) => {
        debug('Telemetry failed:', e.message);
    });
}

// Add global error handler for debugging
window.addEventListener('error', (e) => {
    debug('Global error:', e.error);
});

debug('OmniPkg interactive docs initialized');