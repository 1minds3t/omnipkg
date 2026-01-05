// Cloudflare Worker - Production Ready with Tailscale Integration

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    // CORS Configuration
    const ALLOWED_ORIGINS = [
      'https://1minds3t.echo-universe.ts.net',
      'https://omnipkg.pages.dev',
      'http://localhost:8085',
      'http://localhost:8000',
    ];
    
    const origin = request.headers.get('Origin');
    const isAllowedOrigin = ALLOWED_ORIGINS.includes(origin);

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return corsResponse(null, origin, isAllowedOrigin);
    }

    // Route: /proxy - Forward to Tailscale bridge + collect analytics
    if (url.pathname === '/proxy') {
      try {
        const body = await request.json();
        const { port, endpoint, method = 'GET', data } = body;

        // Use Tailscale URL instead of localhost
        const BRIDGE_BASE = 'https://1minds3t.echo-universe.ts.net/omnipkg-api';
        const targetUrl = `${BRIDGE_BASE}${endpoint}`;
        
        console.log(`Proxying to: ${targetUrl}`);
        
        // Forward the request
        const fetchOptions = {
          method: method,
          headers: { 'Content-Type': 'application/json' },
        };

        if (data && (method === 'POST' || method === 'PUT')) {
          fetchOptions.body = JSON.stringify(data);
        }

        const response = await fetch(targetUrl, fetchOptions);
        
        // Handle non-JSON responses safely
        let result;
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          result = await response.json();
        } else {
          result = { output: await response.text() };
        }

        // 📊 ANALYTICS: Log command usage (privacy-safe)
        if (endpoint === '/run' && data?.command) {
          await logCommandUsage(env, data.command);
        }

        return jsonResponse(result, response.status, origin, isAllowedOrigin);

      } catch (error) {
        console.error('Proxy error:', error);
        return jsonResponse({ 
          error: 'Proxy failed', 
          details: error.message 
        }, 500, origin, isAllowedOrigin);
      }
    }

    // Route: /analytics/track - Frontend events (button clicks, page views)
    if (url.pathname === '/analytics/track') {
      try {
        const body = await request.json();
        
        if (!body || typeof body !== 'object') {
          return jsonResponse({ error: 'Invalid payload' }, 400, origin, isAllowedOrigin);
        }
        
        await logFrontendEvent(env, body);
        return jsonResponse({ success: true }, 200, origin, isAllowedOrigin);
      } catch (error) {
        return jsonResponse({ error: 'Tracking failed' }, 500, origin, isAllowedOrigin);
      }
    }

    // Route: /analytics/stats - Get usage statistics
    if (url.pathname === '/analytics/stats') {
      try {
        const stats = await getAnalyticsStats(env);
        return jsonResponse(stats, 200, origin, isAllowedOrigin);
      } catch (error) {
        return jsonResponse({ error: 'Failed to fetch stats' }, 500, origin, isAllowedOrigin);
      }
    }

    // Route: /info - Display bridge information
    if (url.pathname === '/info' || url.pathname === '/') {
      return new Response(getInfoPage(), {
        headers: {
          'Content-Type': 'text/html;charset=UTF-8',
          'Access-Control-Allow-Origin': isAllowedOrigin ? origin : ALLOWED_ORIGINS[0],
        },
      });
    }

    return jsonResponse({ error: 'Not found' }, 404, origin, isAllowedOrigin);
  },
};

// 📊 Analytics Functions (Privacy-Safe)

async function logCommandUsage(env, commandString) {
  try {
    const cmdName = commandString.trim().split(' ')[0].toLowerCase();
    const dateKey = new Date().toISOString().split('T')[0];
    const kvKey = `cmd:${dateKey}:${cmdName}`;
    
    if (env.ANALYTICS) {
      const current = await env.ANALYTICS.get(kvKey);
      const count = current ? parseInt(current) : 0;
      await env.ANALYTICS.put(kvKey, (count + 1).toString());
    }
    
    console.log(`Command: ${cmdName} | Date: ${dateKey}`);
    
  } catch (error) {
    console.error('Analytics error:', error);
  }
}

async function logFrontendEvent(env, eventData) {
  try {
    const { event_type, event_name, page, metadata } = eventData;
    const dateKey = new Date().toISOString().split('T')[0];
    
    if (event_type === 'button_click') {
      const kvKey = `btn:${dateKey}:${event_name}`;
      if (env.ANALYTICS) {
        const current = await env.ANALYTICS.get(kvKey);
        const count = current ? parseInt(current) : 0;
        await env.ANALYTICS.put(kvKey, (count + 1).toString());
      }
    } else if (event_type === 'page_view') {
      const kvKey = `page:${dateKey}:${page}`;
      if (env.ANALYTICS) {
        const current = await env.ANALYTICS.get(kvKey);
        const count = current ? parseInt(current) : 0;
        await env.ANALYTICS.put(kvKey, (count + 1).toString());
      }
    } else if (event_type === 'feedback') {
      const feedbackKey = `feedback:${Date.now()}`;
      if (env.ANALYTICS) {
        await env.ANALYTICS.put(feedbackKey, JSON.stringify({
          message: metadata?.message,
          rating: metadata?.rating,
          date: dateKey,
        }));
      }
    }
    
    console.log(`Event: ${event_type} - ${event_name}`);
    
  } catch (error) {
    console.error('Event tracking error:', error);
  }
}

async function getAnalyticsStats(env) {
  try {
    if (!env.ANALYTICS) {
      return { error: 'Analytics not configured' };
    }
    
    const stats = {
      commands: {},
      buttons: {},
      pages: {},
      total_commands: 0,
      total_button_clicks: 0,
      total_page_views: 0,
    };
    
    let cursor;
    let listComplete = false;
    
    while (!listComplete) {
      const listOptions = cursor ? { cursor } : {};
      const list = await env.ANALYTICS.list(listOptions);
      
      for (const key of list.keys) {
        const value = await env.ANALYTICS.get(key.name);
        const count = parseInt(value) || 0;
        
        if (key.name.startsWith('cmd:')) {
          const cmdName = key.name.split(':')[2];
          stats.commands[cmdName] = (stats.commands[cmdName] || 0) + count;
          stats.total_commands += count;
        } else if (key.name.startsWith('btn:')) {
          const btnName = key.name.split(':')[2];
          stats.buttons[btnName] = (stats.buttons[btnName] || 0) + count;
          stats.total_button_clicks += count;
        } else if (key.name.startsWith('page:')) {
          const pageName = key.name.split(':')[2];
          stats.pages[pageName] = (stats.pages[pageName] || 0) + count;
          stats.total_page_views += count;
        }
      }
      
      listComplete = list.list_complete;
      cursor = list.cursor;
    }
    
    return stats;
    
  } catch (error) {
    console.error('Stats fetch error:', error);
    return { error: error.message };
  }
}

// Helper Functions

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
    <title>OmniPkg API Bridge</title>
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
            max-width: 600px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }
        h1 { margin: 0 0 10px 0; font-size: 2rem; }
        .subtitle { opacity: 0.9; margin-bottom: 30px; font-size: 1.1rem; }
        .feature {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 8px;
            margin: 10px 0;
            border-left: 4px solid #4CAF50;
        }
        .feature-title { font-weight: bold; margin-bottom: 5px; }
        .note {
            margin-top: 30px;
            padding: 15px;
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
        <h1>📦 OmniPkg API Bridge</h1>
        <div class="subtitle">Secure Proxy via Tailscale</div>
        
        <div class="feature">
            <div class="feature-title">🛡️ Privacy-First Analytics</div>
            <div>We only track command names and button clicks. No IPs, no personal data.</div>
        </div>
        
        <div class="feature">
            <div class="feature-title">🔄 Tailscale Integration</div>
            <div>Commands route through your secure Tailscale network.</div>
        </div>
        
        <div class="feature">
            <div class="feature-title">⚡ Always Online</div>
            <div>Cloudflare's edge network ensures 24/7 availability.</div>
        </div>
        
        <div class="note">
            <strong>For Users:</strong> Access the docs at 
            <a href="https://omnipkg.pages.dev">omnipkg.pages.dev</a> or
            <a href="https://1minds3t.echo-universe.ts.net/omnipkg">via Tailscale</a>
        </div>
    </div>
</body>
</html>`;
}