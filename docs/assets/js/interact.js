/* docs/assets/js/interact.js - Secure Interactive Documentation */

const WORKER_URL = 'https://omnipkg.1minds3t.workers.dev';
let PORT = 5000;
let isConnected = false;
let checkInterval = null;

// XSS Protection: Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

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
    
    if (dot && text && btn) {
        dot.className = connected ? 'status-dot connected' : 'status-dot';
        text.textContent = message;
        btn.style.display = connected ? 'none' : 'inline-block';
        
        // Enable/disable all run buttons
        document.querySelectorAll('.omni-run-btn').forEach(btn => {
            btn.disabled = !connected;
        });
    }
}

function startHealthCheck() {
    checkHealth();
    checkInterval = setInterval(checkHealth, 5000);
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
}

// ==========================================
// Execute Command
// ==========================================
async function runCommand(cmd, btnElement) {
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
        
        const contentType = res.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await res.json();
        } else {
            data = { output: await res.text() };
        }
        
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
    cmdDiv.textContent = `$ ${cmd}`;  // Safe: textContent escapes HTML
    const outputPre = document.createElement('pre');
    outputPre.className = 'omni-output';
    outputPre.textContent = output;  // Safe: textContent escapes HTML
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
// Telemetry (Privacy-Safe)
// ==========================================
function sendTelemetry(eventType, details) {
    // Validate input before sending
    if (typeof eventType !== 'string' || !details || typeof details !== 'object') {
        return;
    }
    
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