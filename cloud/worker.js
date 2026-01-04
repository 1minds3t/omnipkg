export default {
  async fetch(request) {
    const html = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniPkg | Cloud Controller</title>
    <style>
        :root {
            --bg: #0d1117;
            --card: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --accent: #58a6ff;
            --success: #2ea043;
            --error: #da3633;
            --dim: #8b949e;
        }
        * { box-sizing: border-box; }
        body {
            font-family: 'SF Mono', 'Segoe UI Mono', 'Roboto Mono', Menlo, Courier, monospace;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
        }
        .container {
            width: 100%;
            max-width: 900px;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
        }
        h1 { margin: 0; font-size: 1.5rem; color: #fff; }
        .tag {
            font-size: 0.8rem;
            padding: 4px 8px;
            border-radius: 4px;
            background: var(--card);
            border: 1px solid var(--border);
            color: var(--dim);
        }

        /* Status Indicator */
        .status-bar {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 15px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 15px;
        }
        .indicator {
            height: 12px;
            width: 12px;
            border-radius: 50%;
            background: var(--dim);
            box-shadow: 0 0 5px var(--dim);
            transition: all 0.3s ease;
        }
        .indicator.connected { background: var(--success); box-shadow: 0 0 8px var(--success); }
        .indicator.disconnected { background: var(--error); box-shadow: 0 0 8px var(--error); }
        
        .status-text { font-weight: bold; font-size: 0.9rem; }
        .port-info { font-size: 0.8rem; color: var(--dim); margin-left: auto; }

        /* Terminal Window */
        .terminal-window {
            background: #000;
            border: 1px solid var(--border);
            border-radius: 6px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            height: 500px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }
        .terminal-header {
            background: var(--card);
            padding: 8px 15px;
            border-bottom: 1px solid var(--border);
            font-size: 0.8rem;
            color: var(--dim);
            display: flex;
            gap: 6px;
        }
        .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--border); }
        .dot.red { background: #ff5f56; }
        .dot.yellow { background: #ffbd2e; }
        .dot.green { background: #27c93f; }

        .output-area {
            flex-grow: 1;
            padding: 15px;
            overflow-y: auto;
            white-space: pre-wrap;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #fff;
        }
        .cmd-line { color: var(--dim); }
        .response { color: var(--text); margin-bottom: 10px; }
        .error-msg { color: var(--error); }

        /* Input Area */
        .input-area {
            display: flex;
            border-top: 1px solid var(--border);
            background: var(--card);
        }
        .prompt {
            padding: 15px 0 15px 15px;
            color: var(--accent);
            font-weight: bold;
        }
        input {
            flex-grow: 1;
            background: transparent;
            border: none;
            color: #fff;
            font-family: inherit;
            font-size: 1rem;
            padding: 15px;
            outline: none;
        }
        button {
            background: var(--accent);
            color: #000;
            border: none;
            padding: 0 25px;
            font-weight: bold;
            font-family: inherit;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        button:hover { opacity: 0.9; }
        button:disabled { background: var(--border); color: var(--dim); cursor: not-allowed; }

    </style>
</head>
<body>

<div class="container">
    <header>
        <h1>OmniPkg Cloud Controller</h1>
        <div class="tag">v2.0.8</div>
    </header>

    <div class="status-bar">
        <div id="indicator" class="indicator disconnected"></div>
        <div id="statusText" class="status-text">Connecting to Local Bridge...</div>
        <div id="portInfo" class="port-info">Target: localhost:----</div>
    </div>

    <div class="terminal-window">
        <div class="terminal-header">
            <div class="dot red"></div>
            <div class="dot yellow"></div>
            <div class="dot green"></div>
            <span style="margin-left: 10px; opacity: 0.6;">local_bridge.py</span>
        </div>
        <div id="output" class="output-area"></div>
        
        <div class="input-area">
            <span class="prompt">omnipkg $</span>
            <input type="text" id="cmdInput" placeholder="Waiting for connection..." disabled autocomplete="off">
            <button id="runBtn" disabled>RUN</button>
        </div>
    </div>
</div>

<script>
    // --- 1. Dynamic Port Logic ---
    let PORT = 5000; // Default
    
    // Read the hash from URL (e.g. #5003)
    if (window.location.hash) {
        const hashVal = parseInt(window.location.hash.substring(1));
        if (!isNaN(hashVal) && hashVal > 1024) {
            PORT = hashVal;
        }
    }
    
    const API_URL = "http://127.0.0.1:" + PORT;
    const outputDiv = document.getElementById('output');
    const statusText = document.getElementById('statusText');
    const indicator = document.getElementById('indicator');
    const portInfo = document.getElementById('portInfo');
    const input = document.getElementById('cmdInput');
    const btn = document.getElementById('runBtn');

    // Update UI with target port
    portInfo.textContent = "Target: localhost:" + PORT;
    
    function log(text, type='normal') {
        const line = document.createElement('div');
        if (type === 'cmd') {
            line.className = 'cmd-line';
            line.textContent = '> omnipkg ' + text;
        } else if (type === 'error') {
            line.className = 'response error-msg';
            line.textContent = text;
        } else {
            line.className = 'response';
            line.textContent = text;
        }
        outputDiv.appendChild(line);
        outputDiv.scrollTop = outputDiv.scrollHeight;
    }

    log("Initializing Cloud Bridge...");
    log("Targeting local port: " + PORT);

    // --- 2. Connection Health Check ---
    let isConnected = false;

    async function checkHealth() {
        try {
            // We use a simple GET request to check if the python script is there
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 2000); // 2s timeout
            
            const res = await fetch(API_URL + "/health", { signal: controller.signal });
            clearTimeout(timeoutId);

            if (res.ok) {
                if (!isConnected) {
                    // State Change: Disconnected -> Connected
                    isConnected = true;
                    const data = await res.json();
                    
                    indicator.className = "indicator connected";
                    statusText.textContent = "Local Bridge Connected";
                    statusText.style.color = "#2ea043";
                    
                    input.disabled = false;
                    btn.disabled = false;
                    input.placeholder = "Enter command (e.g. version, list)";
                    input.focus();
                    
                    log("✅ Connection established with " + (data.version || "OmniPkg"));
                }
            } else {
                throw new Error("Health check failed");
            }
        } catch (e) {
            if (isConnected) {
                // State Change: Connected -> Disconnected
                isConnected = false;
                indicator.className = "indicator disconnected";
                statusText.textContent = "Local Bridge Disconnected";
                statusText.style.color = "#da3633";
                
                input.disabled = true;
                btn.disabled = true;
                input.placeholder = "Waiting for connection...";
                
                log("❌ Connection lost. Is 'omnipkg launch-web' running?", "error");
            }
        }
    }

    // --- 3. Command Execution ---
    async function runCommand() {
        const cmd = input.value.trim();
        if (!cmd) return;

        log(cmd, 'cmd');
        input.value = '';
        input.disabled = true; // Prevent double submit

        try {
            const res = await fetch(API_URL + "/run", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ command: cmd })
            });

            const data = await res.json();
            
            // Handle output lines
            if (data.output) {
                log(data.output);
            } else {
                log("(No output returned)");
            }

        } catch (e) {
            log("Error sending command: " + e.message, "error");
        } finally {
            input.disabled = false;
            input.focus();
        }
    }

    // Event Listeners
    btn.addEventListener('click', runCommand);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') runCommand();
    });

    // Start Polling
    checkHealth(); // Check immediately
    setInterval(checkHealth, 3000); // Check every 3 seconds

</script>
</body>
</html>
    `;

    return new Response(html, {
      headers: {
        'content-type': 'text/html;charset=UTF-8',
      },
    });
  },
};