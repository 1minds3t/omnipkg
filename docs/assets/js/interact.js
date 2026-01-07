/* docs/assets/js/interact.js - Smart Domain Detection */

const WORKER_URL = 'https://omnipkg.1minds3t.workers.dev';
let PORT = 5000;
let isConnected = false;
let checkInterval = null;
const DEBUG = true;

// 🎯 BUSINESS LOGIC: Detect if we're on Tailnet or localhost
const currentDomain = window.location.hostname;
const IS_INTERACTIVE_DOMAIN = currentDomain === '1minds3t.echo-universe.ts.net' || currentDomain === 'localhost' || currentDomain === '127.0.0.1';

// Use Tailscale proxy when accessing remotely
const BRIDGE_URL = currentDomain === '1minds3t.echo-universe.ts.net' 
    ? 'https://1minds3t.echo-universe.ts.net/omnipkg-api'  // Tailscale proxy
    : `http://127.0.0.1:${PORT}`;  // Direct localhost

const SAFE_TELEMETRY_KEYS = new Set([
    'command', 'path', 'title', 'port', 'package', 
    'method', 'error', 'duration', 'success'
]);

function debug(...args) {
    if (DEBUG) {
        console.log('[OmniPkg]', ...args);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", function() {
    debug(`Domain: ${currentDomain} | Interactive: ${IS_INTERACTIVE_DOMAIN}`);
    
    if (window.location.hash) {
        const val = parseInt(window.location.hash.substring(1));
        if (!isNaN(val) && val > 1024 && val < 65536) {
            PORT = val;
        }
    }

    // Track page view (works on all domains)
    sendTelemetry("page_view", {
        path: window.location.pathname,
        title: document.title,
        domain: currentDomain
    });

    if (IS_INTERACTIVE_DOMAIN) {
        // Full interactive experience
        debug('✅ Interactive mode enabled');
        createStatusBanner();
        injectRunButtons();
        startHealthCheck();
    } else {
        // Static documentation mode
        debug('📚 Static documentation mode');
        createUpgradeNotice();
        injectStaticButtons();
    }
});

// ==========================================
// Upgrade Notice (Cloudflare Pages)
// ==========================================
function createUpgradeNotice() {
    const banner = document.createElement('div');
    banner.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 16px 20px;
        text-align: center;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 10000;
        font-size: 15px;
        line-height: 1.6;
    `;
    
    banner.innerHTML = `
        <div style="max-width: 1000px; margin: 0 auto;">
            <strong style="font-size: 16px;">📚 Static Documentation</strong>
            <span style="opacity: 0.95; display: block; margin-top: 6px;">
                For interactive command execution, visit 
                <a href="https://1minds3t.echo-universe.ts.net/omnipkg${window.location.pathname}" 
                   style="color: #FFD700; text-decoration: underline; font-weight: 600;">
                    1minds3t.echo-universe.ts.net/omnipkg/
                </a>
                ${window.location.hash ? window.location.hash : ''}
            </span>
        </div>
    `;
    
    document.body.appendChild(banner);
    document.body.style.paddingTop = '90px';
}

// ==========================================
// Status Banner (Interactive Sites Only)
// ==========================================
function createStatusBanner() {
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
    text.textContent = 'Connecting to cloud bridge...';
    
    const btn = document.createElement('button');
    btn.id = 'reconnect-btn';
    btn.textContent = 'Retry';
    btn.style.display = 'none';
    btn.onclick = () => checkHealth();
    
    content.appendChild(dot);
    content.appendChild(text);
    content.appendChild(btn);
    banner.appendChild(content);
    document.body.appendChild(banner);
}

// ==========================================
// Health Check
// ==========================================
async function checkHealth() {
    if (!IS_INTERACTIVE_DOMAIN) return;
    
    debug(`Health check: ${BRIDGE_URL}`);
    
    // 📊 TELEMETRY: Always notify Cloudflare of health checks
    sendTelemetry("health_check", { method: currentDomain === '1minds3t.echo-universe.ts.net' ? 'tailnet' : 'direct' });
    
    try {
        const res = await fetch(`${BRIDGE_URL}/health`, {  // ← USE BRIDGE_URL
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (res.ok) {
            const data = await res.json();
            updateStatus(true, `Connected to Local Bridge v${data.version || '2.1.0'} (Port ${PORT})`);
            // Track successful connection
            sendTelemetry("bridge_connected", { port: PORT, version: data.version });
        } else {
            updateStatus(false, 'Local bridge not running | Run: 8pkg web start');
            sendTelemetry("bridge_failed", { port: PORT, status: res.status });
        }
    } catch (e) {
        debug('Health check failed:', e);
        updateStatus(false, 'Local bridge not running | Run: 8pkg web start');
        sendTelemetry("bridge_offline", { port: PORT, error: e.message });
    }
}


function updateStatus(connected, message) {
    isConnected = connected;
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btn = document.getElementById('reconnect-btn');
    
    if (dot && text && btn) {
        dot.className = connected ? 'status-dot connected' : 'status-dot';
        text.textContent = message;
        btn.style.display = connected ? 'none' : 'inline-block';
        
        document.querySelectorAll('.omni-run-btn').forEach(btn => {
            btn.disabled = !connected;
        });
    }
}

function startHealthCheck() {
    if (!IS_INTERACTIVE_DOMAIN) return;
    checkHealth();
    checkInterval = setInterval(checkHealth, 10000); // Every 10 seconds
}

// ==========================================
// Inject Buttons
// ==========================================
function injectRunButtons() {
    const codeBlocks = document.querySelectorAll('pre > code');
    debug(`Found ${codeBlocks.length} code blocks`);

    codeBlocks.forEach((block) => {
        const text = block.innerText.trim();
        
        if (text.startsWith("omnipkg") || text.startsWith("8pkg")) {
            const button = createRunButton(text);
            const preBlock = block.parentElement;
            if (preBlock && preBlock.parentElement) {
                preBlock.parentElement.insertBefore(button, preBlock.nextSibling);
            }
        }
    });
}

function injectStaticButtons() {
    const codeBlocks = document.querySelectorAll('pre > code');

    codeBlocks.forEach((block) => {
        const text = block.innerText.trim();
        
        if (text.startsWith("omnipkg") || text.startsWith("8pkg")) {
            const button = createStaticButton();
            const preBlock = block.parentElement;
            if (preBlock && preBlock.parentElement) {
                preBlock.parentElement.insertBefore(button, preBlock.nextSibling);
            }
        }
    });
}

function createRunButton(command) {
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
    button.onclick = () => runCommand(command, button);
    
    return button;
}

function createStaticButton() {
    const button = document.createElement("button");
    button.className = "omni-run-btn";
    button.disabled = true;
    button.style.opacity = "0.6";
    button.style.cursor = "not-allowed";
    
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("class", "btn-icon");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("width", "16");
    icon.setAttribute("height", "16");
    
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute("d", "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z");
    icon.appendChild(path);
    
    const span = document.createElement("span");
    span.textContent = "Interactive Mode Required";
    
    button.appendChild(icon);
    button.appendChild(span);
    
    return button;
}

// ==========================================
// Execute Command
// ==========================================

async function runCommand(cmd, btnElement) {
    if (!isConnected) {
        alert('Local bridge not running. Start with: 8pkg web start');
        sendTelemetry("command_blocked", { reason: "bridge_offline" });
        return;
    }
    
    const originalContent = Array.from(btnElement.childNodes);
    
    // Loading state
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

    // Send telemetry (fire and forget)
    sendTelemetry("command_exec", { command: cmd.split(' ')[0] });

    const startTime = Date.now();

    try {
        // Open modal immediately to show streaming output
        const modal = createStreamingModal(cmd);
        const outputPre = modal.querySelector('.omni-output');
        let fullOutput = '';
        
        // 🔥 CONNECTION with streaming
        const res = await fetch(`${BRIDGE_URL}/run`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ command: cmd })
        });
        
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, {stream: true});
            const lines = chunk.split('\n\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const jsonStr = line.substring(6);
                    try {
                        const data = JSON.parse(jsonStr);
                        if (data.done) break;
                        if (data.line) {
                            fullOutput += data.line;
                            outputPre.textContent = fullOutput;
                            outputPre.scrollTop = outputPre.scrollHeight;
                        }
                    } catch (e) {
                        debug('Parse error:', e);
                    }
                }
            }
        }
        
        // Track successful completion
        const duration = Date.now() - startTime;
        sendTelemetry("command_complete", {
            command: cmd.split(' ')[0],
            duration_ms: duration,
            success: true
        });
        
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
        debug('Command error:', e);
        
        // Track error
        const duration = Date.now() - startTime;
        sendTelemetry("command_error", {
            command: cmd.split(' ')[0],
            error: e.message,
            duration_ms: duration
        });
        
        showOutput(cmd, `Error: ${e.message}`);
        
        btnElement.innerHTML = '';
        originalContent.forEach(node => btnElement.appendChild(node.cloneNode(true)));
        btnElement.disabled = false;
    }
}

// ==========================================
// Output Modal
// ==========================================
function showOutput(cmd, output) {
    const existing = document.getElementById('omni-output-modal');
    if (existing) existing.remove();
    
    const modal = document.createElement('div');
    modal.id = 'omni-output-modal';
    
    const backdrop = document.createElement('div');
    backdrop.className = 'omni-modal-backdrop';
    backdrop.onclick = () => modal.remove();
    
    const content = document.createElement('div');
    content.className = 'omni-modal-content';
    
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
    
    content.appendChild(header);
    content.appendChild(body);
    content.appendChild(footer);
    modal.appendChild(backdrop);
    modal.appendChild(content);
    document.body.appendChild(modal);
}

function createStreamingModal(cmd) {
    const existing = document.getElementById('omni-output-modal');
    if (existing) existing.remove();
    
    const modal = document.createElement('div');
    modal.id = 'omni-output-modal';
    
    const backdrop = document.createElement('div');
    backdrop.className = 'omni-modal-backdrop';
    
    const content = document.createElement('div');
    content.className = 'omni-modal-content';
    
    const header = document.createElement('div');
    header.className = 'omni-modal-header';
    const title = document.createElement('h3');
    title.textContent = 'Command Output (Live)';
    const closeBtn = document.createElement('button');
    closeBtn.className = 'omni-modal-close';
    closeBtn.textContent = '×';
    closeBtn.onclick = () => modal.remove();
    header.appendChild(title);
    header.appendChild(closeBtn);
    
    const body = document.createElement('div');
    body.className = 'omni-modal-body';
    const cmdDiv = document.createElement('div');
    cmdDiv.className = 'omni-command';
    cmdDiv.textContent = `$ ${cmd}`;
    const outputPre = document.createElement('pre');
    outputPre.className = 'omni-output';
    outputPre.textContent = 'Executing...\n';
    body.appendChild(cmdDiv);
    body.appendChild(outputPre);
    
    const footer = document.createElement('div');
    footer.className = 'omni-modal-footer';
    const closeBtn2 = document.createElement('button');
    closeBtn2.textContent = 'Close';
    closeBtn2.onclick = () => modal.remove();
    footer.appendChild(closeBtn2);
    
    content.appendChild(header);
    content.appendChild(body);
    content.appendChild(footer);
    modal.appendChild(backdrop);
    modal.appendChild(content);
    document.body.appendChild(modal);
    
    return modal;
}

// ==========================================
// Telemetry
// ==========================================
function sanitizeTelemetryData(details) {
    if (!details || typeof details !== 'object') return {};
    
    const sanitized = {};
    for (const [key, value] of Object.entries(details)) {
        if (!SAFE_TELEMETRY_KEYS.has(key)) continue;
        
        let cleanValue = String(value);
        if (cleanValue.length > 200) {
            cleanValue = cleanValue.substring(0, 200) + '...[truncated]';
        }
        
        cleanValue = cleanValue.replace(/[A-Za-z]:\\Users\\[^\s]+/g, '[PATH]');
        cleanValue = cleanValue.replace(/\/home\/[^\s]+/g, '[PATH]');
        cleanValue = cleanValue.replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, '[IP]');
        
        sanitized[key] = cleanValue;
    }
    
    return sanitized;
}

function sendTelemetry(eventType, details) {
    if (typeof eventType !== 'string') return;
    
    const safeMetadata = sanitizeTelemetryData(details);
    
    fetch(`${WORKER_URL}/analytics/track`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            event_type: eventType,
            event_name: safeMetadata.command || safeMetadata.path || 'unknown',
            page: window.location.pathname,
            domain: currentDomain,
            metadata: safeMetadata
        })
    }).catch(() => {
        debug('Telemetry failed (non-blocking)');
    });
}

debug('OmniPkg initialized');