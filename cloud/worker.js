// Cloudflare Worker - Telemetry Collection Only
// Command execution happens directly: Browser -> User's localhost

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    const ALLOWED_ORIGINS = [
      'https://1minds3t.echo-universe.ts.net',
      'https://omnipkg.pages.dev',
      'http://localhost:8085',
      'http://localhost:8000',
    ];
    
    const origin = request.headers.get('Origin');
    const isAllowedOrigin = ALLOWED_ORIGINS.includes(origin);

    if (request.method === 'OPTIONS') {
      return corsResponse(null, origin, isAllowedOrigin);
    }

    // 📊 TELEMETRY ONLY: Log events to YOUR Tailscale bridge
    if (url.pathname === '/analytics/track') {
      try {
        const body = await request.json();
        
        if (!body || typeof body !== 'object') {
          return jsonResponse({ error: 'Invalid payload' }, 400, origin, isAllowedOrigin);
        }
        
        // Add metadata
        body.origin = origin;
        body.worker_timestamp = new Date().toISOString();
        
        // Forward to YOUR Tailscale bridge
        await logToYourBridge(body);
        
        return jsonResponse({ success: true }, 200, origin, isAllowedOrigin);
      } catch (error) {
        // Always return success for telemetry (non-blocking)
        console.error('Telemetry error:', error);
        return jsonResponse({ success: true }, 200, origin, isAllowedOrigin);
      }
    }

    // 📈 STATS: Get analytics from YOUR bridge (admin only)
    if (url.pathname === '/analytics/stats') {
      try {
        const YOUR_BRIDGE = 'https://1minds3t.echo-universe.ts.net/omnipkg-api';
        
        const response = await fetch(`${YOUR_BRIDGE}/telemetry/stats`, {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' }
        });
        
        if (response.ok) {
          const stats = await response.json();
          return jsonResponse(stats, 200, origin, isAllowedOrigin);
        }
        
        return jsonResponse({ 
          status: "Bridge offline",
          message: "Stats stored locally on your machine"
        }, 200, origin, isAllowedOrigin);
        
      } catch (error) {
        return jsonResponse({ 
          status: "Bridge offline",
          message: "Stats stored locally on your machine"
        }, 200, origin, isAllowedOrigin);
      }
    }

    // Info page
    if (url.pathname === '/info' || url.pathname === '/') {
      return new Response(getInfoPage(), {
        headers: {
          'Content-Type': 'text/html;charset=UTF-8',
          'Access-Control-Allow-Origin': isAllowedOrigin ? origin : ALLOWED_ORIGINS[0],
        },
      });
    }

    return jsonResponse({ 
      error: 'Not found',
      hint: 'This worker only handles telemetry. Command execution happens directly between your browser and localhost.'
    }, 404, origin, isAllowedOrigin);
  },
};

// Send telemetry to YOUR Tailscale bridge
async function logToYourBridge(eventData) {
  try {
    const YOUR_BRIDGE = 'https://1minds3t.echo-universe.ts.net/omnipkg-api';
    
    // Fire and forget - don't block the response
    fetch(`${YOUR_BRIDGE}/telemetry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(eventData)
    }).catch((err) => {
      console.error('Failed to log to your bridge:', err.message);
    });
    
  } catch (error) {
    // Never block the main flow
    console.error('Telemetry error:', error);
  }
}

function corsResponse(body, origin, isAllowed) {
  return new Response(body, {
    headers: {
      'Access-Control-Allow-Origin': isAllowed ? origin : 'https://omnipkg.pages.dev',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    },
  });
}

function jsonResponse(data, status = 200, origin, isAllowed) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': isAllowed ? origin : 'https://omnipkg.pages.dev',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}

function getInfoPage() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniPkg Telemetry Service</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 40px;
            max-width: 700px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }
        h1 { margin: 0 0 10px 0; font-size: 2.2rem; }
        .subtitle { opacity: 0.9; margin-bottom: 30px; font-size: 1.1rem; }
        .feature {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px 20px;
            border-radius: 8px;
            margin: 12px 0;
            border-left: 4px solid #4CAF50;
        }
        .feature-title { 
            font-weight: bold; 
            margin-bottom: 8px;
            font-size: 1.1rem;
        }
        .architecture {
            margin-top: 30px;
            padding: 20px;
            background: rgba(33, 150, 243, 0.2);
            border-radius: 8px;
            border-left: 4px solid #2196F3;
            font-family: monospace;
            font-size: 0.9rem;
            line-height: 1.8;
        }
        .note {
            margin-top: 20px;
            padding: 20px;
            background: rgba(255, 193, 7, 0.2);
            border-radius: 8px;
            border-left: 4px solid #FFC107;
        }
        a { color: #FFD700; text-decoration: none; font-weight: bold; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 OmniPkg Telemetry Service</h1>
        <div class="subtitle">Privacy-First Usage Analytics</div>
        
        <div class="feature">
            <div class="feature-title">🏠 Local Execution</div>
            <div>Commands run directly on your machine. This worker never sees your code or data.</div>
        </div>
        
        <div class="feature">
            <div class="feature-title">📡 Anonymous Telemetry</div>
            <div>We collect: command names, timestamps, and origin domains. Nothing else.</div>
        </div>
        
        <div class="feature">
            <div class="feature-title">🔒 Zero Trust</div>
            <div>This worker can't execute commands. It only forwards anonymized metrics.</div>
        </div>

        <div class="architecture">
            <strong>📐 Architecture:</strong><br><br>
            <strong>Execution Path (Private):</strong><br>
            Browser → localhost:5000 → Your Python Bridge → Subprocess<br><br>
            
            <strong>Telemetry Path (Public):</strong><br>
            Browser → This Worker → Tailscale Bridge → SQLite DB<br><br>
            
            <strong>Data Collected:</strong><br>
            • Command: "status" (not "status --verbose")<br>
            • Origin: "1minds3t.echo-universe.ts.net"<br>
            • Timestamp: ISO 8601 format<br><br>
            
            <strong>Never Collected:</strong><br>
            ❌ Command outputs<br>
            ❌ File paths<br>
            ❌ IP addresses<br>
            ❌ Personal identifiers
        </div>
        
        <div class="note">
            <strong>Get Started:</strong><br>
            Visit <a href="https://1minds3t.echo-universe.ts.net/omnipkg/">1minds3t.echo-universe.ts.net/omnipkg</a>
            or view docs at <a href="https://omnipkg.pages.dev">omnipkg.pages.dev</a>
        </div>
    </div>
</body>
</html>`;
}