/**
 * Cloudflare Worker Backend for Flasher Builder Macan Telegram Mini App
 * 
 * Required Secrets (wrangler secret put <NAME>):
 * - GITHUB_PAT: GitHub Personal Access Token (Actions read/write)
 * - TELEGRAM_BOT_TOKEN: Bot token from @BotFather
 * 
 * Optional Environment Variables:
 * - GITHUB_REPO: "owner/repo" (default: "bt75gh48/Flasher_Builder")
 * - GITHUB_BRANCH: Target git branch (default: "main")
 * - ALLOWED_TELEGRAM_USER_IDS: Comma-separated list of authorized Telegram user IDs
 */

const BUILDER_MAP = {
  'Super Flasher|15R|EU': 'BUILD_SUPER',
  'Super Flasher|15R|GLO': 'BUILD_SUPER',
  'Super Flasher|15R|IN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|CN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|IN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|GLO': 'BUILD_SUPER',
  'Regional Flasher|15R|IN': 'BUILD_REGIONAL',
  'Regional Flasher|15R|GLO': 'BUILD_REGIONAL',
  'Regional Flasher|15R|EU': 'BUILD_REGIONAL',
};

const DEVICE_ALIAS = {
  'Oneplus 15R': '15R',
  'Oneplus Ace 6T': 'Ace6T'
};

const OTA_API = 'https://roms.danielspringer.at/api/ota.php';
const NA_API = 'https://roms.danielspringer.at/api/na.php';

export default {
  async fetch(request, env) {
    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: getCorsHeaders() });
    }

    const { pathname } = new URL(request.url);

    // API Routes
    switch (pathname) {
      case '/api/auth-status':
        if (request.method === 'GET') return handleAuthStatus(request, env);
        break;
      case '/api/catalog':
        if (request.method === 'GET') return handleGetCatalog(request);
        break;
      case '/api/trigger':
        if (request.method === 'POST') return handleTrigger(request, env);
        break;
      case '/api/runs':
        if (request.method === 'GET') return handleGetRuns(request, env);
        break;
      case '/api/run-details':
        if (request.method === 'GET') return handleGetRunDetails(request, env);
        break;
      case '/api/cancel-run':
        if (request.method === 'POST') return handleCancelRun(request, env);
        break;
      case '/api/telegram-webhook':
        if (request.method === 'POST') return handleTelegramWebhook(request, env);
        if (request.method === 'GET') return jsonResponse({ status: 'ok', message: 'Telegram Webhook Endpoint' });
        break;
      case '/api/set-webhook':
        return handleSetWebhook(request, env);
      case '/api/webhook-info':
        return handleGetWebhookInfo(request, env);
      case '/api/health':
        return jsonResponse({ status: 'ok', time: new Date().toISOString() });
    }

    // Serve static assets from public/ directory if ASSETS binding exists
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }

    return new Response('Not Found', { status: 404 });
  }
};

/**
 * Helper to check whether public action runs & build triggering is enabled
 */
function isPublicAccessEnabled(env) {
  const val = String(env.ENABLE_PUBLIC_ACTION_RUNS || '').trim().toLowerCase();
  return val === 'true' || val === '1' || val === 'yes';
}

/**
 * Check User Authentication, Whitelist Status & Permissions
 */
async function handleAuthStatus(request, env) {
  const auth = await authenticateTelegram(request, env);
  const whitelistConfigured = Boolean(
    (env.ALLOWED_TELEGRAM_USER_IDS && env.ALLOWED_TELEGRAM_USER_IDS.trim()) ||
    (env.OWNER_TELEGRAM_ID && env.OWNER_TELEGRAM_ID.trim())
  );
  const publicAccess = isPublicAccessEnabled(env);

  if (!auth.ok) {
    return jsonResponse({
      ok: false,
      user: null,
      whitelist_configured: whitelistConfigured,
      public_access: publicAccess,
      is_whitelisted: false,
      can_trigger: false,
      can_view_runs: false,
      can_cancel: false,
      can_view_github: false,
      error: auth.error
    });
  }

  return jsonResponse({
    ok: true,
    user: auth.user || null,
    whitelist_configured: whitelistConfigured,
    public_access: publicAccess,
    is_whitelisted: auth.isWhitelisted,
    can_trigger: auth.canTrigger,
    can_view_runs: auth.canViewRuns,
    can_cancel: auth.canCancel,
    can_view_github: auth.canViewGithub
  });
}

/**
 * Handle Workflow Dispatch Trigger
 */
async function handleTrigger(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({ error: auth.error }, 401);
  }

  if (!auth.canTrigger) {
    return jsonResponse({
      error: 'Access denied: Build triggering is restricted to authorized users.'
    }, 403);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON request body' }, 400);
  }

  const {
    device,
    flasher_type,
    region,
    ota_version,
    ota_url,
    bloat_static_odm,
    upload_to,
    gofile_api_key,
    notify_telegram
  } = body;

  const devShort = DEVICE_ALIAS[device];
  if (!devShort) {
    return jsonResponse({ error: `Unknown device: "${device}"` }, 400);
  }

  const comboKey = `${flasher_type}|${devShort}|${region}`;
  if (!BUILDER_MAP[comboKey]) {
    return jsonResponse({
      error: `Unsupported builder combination: ${device} • ${flasher_type} • ${region}`
    }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const branch = env.GITHUB_BRANCH || 'main';
  const pat = env.GITHUB_PAT;

  if (!pat) {
    return jsonResponse({ error: 'Server misconfiguration: GITHUB_PAT secret is missing' }, 500);
  }

  const inputs = {
    device: String(device),
    flasher_type: String(flasher_type),
    region: String(region),
    ota_version: ota_url ? '' : String(ota_version || 'latest').trim(),
    ota_url: String(ota_url || '').trim(),
    bloat_static_odm: String(bloat_static_odm === '1' ? '1' : '0'),
    upload_to: String(upload_to === 'Artifact Only' ? 'Artifact Only' : 'Gofile'),
    notify_telegram: notify_telegram === true || notify_telegram === 'true'
  };

  if (gofile_api_key && gofile_api_key.trim()) {
    inputs.gofile_api_key = gofile_api_key.trim();
  }

  // Dispatch workflow run to GitHub REST API
  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/workflows/build_macan.yml/dispatches`;
  const ghResp = await fetch(ghEndpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${pat}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'FlasherBuilder-MiniApp/2.0',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ ref: branch, inputs })
  });

  if (!ghResp.ok) {
    const errText = await ghResp.text();
    return jsonResponse({ error: `GitHub API error (${ghResp.status}): ${errText}` }, ghResp.status);
  }

  // Notify Owner on Telegram regarding the build submission
  try {
    const ownerId = getOwnerId(env);
    const botToken = env.TELEGRAM_BOT_TOKEN;
    if (ownerId && botToken) {
      const origin = new URL(request.url).origin;
      const isOwnerTrigger = isOwner(auth.user?.id, env);
      const title = isOwnerTrigger
        ? '🚀 <b>Build Action Dispatched (Owner)</b>'
        : '🔔 <b>New Build Action Dispatched by User</b>';

      const uFirst = auth.user?.first_name || '';
      const uLast = auth.user?.last_name || '';
      const uName = auth.user?.username ? ` (@${escapeHtml(auth.user.username)})` : '';
      const uId = auth.user?.id || 'Unknown';
      const userLabel = (uFirst || uLast) ? `${escapeHtml(uFirst)} ${escapeHtml(uLast)}`.trim() : (auth.user?.username ? `@${escapeHtml(auth.user.username)}` : 'User');

      const otaInfo = inputs.ota_url
        ? `🔗 <b>Custom OTA URL:</b> <code>${escapeHtml(inputs.ota_url)}</code>`
        : `📦 <b>OTA Version:</b> <code>${escapeHtml(inputs.ota_version || 'latest')}</code>`;

      const odmInfo = inputs.bloat_static_odm === '1' ? 'Enabled' : 'Disabled';

      const notifyMsg =
        `${title}\n\n` +
        `👤 <b>Triggered By:</b> ${userLabel}${uName}\n` +
        `🆔 <b>User ID:</b> <code>${uId}</code>\n` +
        `📱 <b>Device:</b> <code>${escapeHtml(inputs.device)}</code>\n` +
        `⚡ <b>Flasher:</b> <code>${escapeHtml(inputs.flasher_type)}</code>\n` +
        `🌍 <b>Region:</b> <code>${escapeHtml(inputs.region)}</code>\n` +
        `${otaInfo}\n` +
        `🧩 <b>Static ODM:</b> ${odmInfo}\n` +
        `☁️ <b>Destination:</b> ${escapeHtml(inputs.upload_to)}\n` +
        `⏰ <b>Time:</b> ${new Date().toUTCString()}`;

      await sendTelegramApi(botToken, 'sendMessage', {
        chat_id: ownerId,
        text: notifyMsg,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '📊 Open Mini App Monitor', web_app: { url: origin } }]
          ]
        }
      }).catch(err => console.error('Failed to send build notification to owner:', err));
    }
  } catch (notifyErr) {
    console.error('Owner notification error:', notifyErr);
  }

  return jsonResponse({
    ok: true,
    message: 'Build workflow triggered successfully',
    repo,
    branch,
    user: auth.user?.username || auth.user?.id
  });
}

/**
 * Handle Workflow Run Cancellation
 */
async function handleCancelRun(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({ error: auth.error }, 401);
  }

  if (!auth.canCancel && !auth.canTrigger) {
    return jsonResponse({
      error: 'Access denied: You do not have permission to cancel builds.'
    }, 403);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON request body' }, 400);
  }

  const runId = body.run_id || body.runId;
  if (!runId) {
    return jsonResponse({ error: 'Missing run_id parameter' }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  if (!pat) {
    return jsonResponse({ error: 'GitHub PAT is not configured on the server' }, 500);
  }

  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/runs/${runId}/cancel`;
  const headers = {
    'Authorization': `Bearer ${pat}`,
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  try {
    const ghResp = await fetch(ghEndpoint, {
      method: 'POST',
      headers
    });

    if (ghResp.status === 202) {
      // Notify owner of build cancellation
      try {
        const ownerId = getOwnerId(env);
        const botToken = env.TELEGRAM_BOT_TOKEN;
        if (ownerId && botToken) {
          const origin = new URL(request.url).origin;
          const isOwnerCancel = isOwner(auth.user?.id, env);
          const title = isOwnerCancel
            ? '🛑 <b>Build Run Canceled (Owner)</b>'
            : '⚠️ <b>Build Run Canceled by User</b>';

          const uFirst = auth.user?.first_name || '';
          const uLast = auth.user?.last_name || '';
          const uName = auth.user?.username ? ` (@${escapeHtml(auth.user.username)})` : '';
          const uId = auth.user?.id || 'Unknown';
          const userLabel = (uFirst || uLast) ? `${escapeHtml(uFirst)} ${escapeHtml(uLast)}`.trim() : (auth.user?.username ? `@${escapeHtml(auth.user.username)}` : 'User');

          const notifyMsg =
            `${title}\n\n` +
            `👤 <b>Canceled By:</b> ${userLabel}${uName}\n` +
            `🆔 <b>User ID:</b> <code>${uId}</code>\n` +
            `🔢 <b>Workflow Run:</b> <code>#${escapeHtml(runId)}</code>\n` +
            `⏰ <b>Time:</b> ${new Date().toUTCString()}`;

          await sendTelegramApi(botToken, 'sendMessage', {
            chat_id: ownerId,
            text: notifyMsg,
            parse_mode: 'HTML',
            reply_markup: {
              inline_keyboard: [
                [{ text: '📊 Open Mini App Monitor', web_app: { url: origin } }]
              ]
            }
          }).catch(err => console.error('Failed to send cancel notification to owner:', err));
        }
      } catch (_) {}

      return jsonResponse({
        ok: true,
        message: `Cancellation requested for build run #${runId}.`,
        run_id: runId
      });
    }

    if (ghResp.status === 409) {
      return jsonResponse({
        error: 'Run cannot be cancelled (it may have already completed or been cancelled).'
      }, 409);
    }

    const errText = await ghResp.text();
    return jsonResponse({
      error: `GitHub API error (${ghResp.status}): ${errText}`
    }, ghResp.status);
  } catch (err) {
    return jsonResponse({
      error: `Failed to communicate with GitHub API: ${err.message}`
    }, 502);
  }
}

// In-memory cache for resolved Gofile URLs to minimize GitHub API calls
const gofileCache = new Map();

/**
 * Handle Workflow Runs Query (Restricted to ALLOWED_TELEGRAM_USER_IDS or public when ENABLE_PUBLIC_ACTION_RUNS is true)
 */
async function handleGetRuns(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({
      error: auth.error || 'Unauthorized'
    }, 401);
  }

  if (!auth.canViewRuns) {
    return jsonResponse({
      error: 'Access denied: Action runs are restricted to authorized Telegram users.'
    }, 403);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/workflows/build_macan.yml/runs?per_page=12`;
  const headers = {
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  if (pat) {
    headers['Authorization'] = `Bearer ${pat}`;
  }

  const ghResp = await fetch(ghEndpoint, { headers });
  if (!ghResp.ok) {
    const errText = await ghResp.text();
    return jsonResponse({ error: `GitHub API error (${ghResp.status}): ${errText}` }, ghResp.status);
  }

  const data = await ghResp.json();
  const rawRuns = data.workflow_runs || [];

  // Concurrently enrich the top runs with live progress & Gofile URL
  const enrichedRuns = await Promise.all(
    rawRuns.map(async (r, index) => {
      const runItem = {
        id: r.id,
        name: r.name,
        display_title: r.display_title,
        run_number: r.run_number,
        status: r.status,
        conclusion: r.conclusion,
        html_url: auth.canViewGithub ? r.html_url : null,
        created_at: r.created_at,
        updated_at: r.updated_at,
        job_id: null,
        job_name: null,
        active_step: null,
        completed_steps: 0,
        total_steps: 0,
        progress_percent: r.status === 'completed' ? 100 : 0,
        gofile_url: null
      };

      // Enrich active runs, queued runs, and the latest 5 completed runs
      if (r.status === 'in_progress' || r.status === 'queued' || index < 5) {
        try {
          const jResp = await fetch(`https://api.github.com/repos/${repo}/actions/runs/${r.id}/jobs`, { headers });
          if (jResp.ok) {
            const jData = await jResp.json();
            const job = jData.jobs?.[0];
            if (job) {
              runItem.job_id = job.id;
              runItem.job_name = job.name || null;
              const steps = job.steps || [];
              runItem.total_steps = steps.length;
              runItem.completed_steps = steps.filter(s => s.status === 'completed').length;
              const inProg = steps.find(s => s.status === 'in_progress');

              if (inProg) {
                runItem.active_step = inProg.name;
              } else if (r.status === 'completed') {
                runItem.active_step = r.conclusion === 'success' ? 'Completed successfully' : `Completed with ${r.conclusion}`;
              } else {
                runItem.active_step = 'Queued on GitHub runner...';
              }

              if (r.status === 'in_progress' && runItem.total_steps > 0) {
                const rawPercent = Math.round((runItem.completed_steps / runItem.total_steps) * 100);
                runItem.progress_percent = inProg
                  ? Math.min(95, Math.max(5, rawPercent + Math.round(50 / runItem.total_steps)))
                  : rawPercent;
              }

              if (job.id) {
                runItem.gofile_url = await fetchGofileUrlForJob(repo, job.id, headers);
              }
            }
          }
        } catch (_) {}
      }

      return runItem;
    })
  );

  return jsonResponse({
    total_count: data.total_count,
    can_view_github: auth.canViewGithub,
    can_cancel: auth.canCancel,
    workflow_runs: enrichedRuns
  });
}

/**
 * Handle Detailed Run Steps & Real-Time Progress Query
 */
async function handleGetRunDetails(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({
      error: auth.error || 'Unauthorized'
    }, 401);
  }

  if (!auth.canViewRuns) {
    return jsonResponse({
      error: 'Access denied: Action runs are restricted to authorized users.'
    }, 403);
  }

  const url = new URL(request.url);
  const runId = url.searchParams.get('run_id');
  if (!runId) {
    return jsonResponse({ error: 'Missing run_id parameter' }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  const headers = {
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  if (pat) {
    headers['Authorization'] = `Bearer ${pat}`;
  }

  try {
    const jobsResp = await fetch(`https://api.github.com/repos/${repo}/actions/runs/${runId}/jobs`, { headers });
    if (!jobsResp.ok) {
      const errText = await jobsResp.text();
      return jsonResponse({ error: `GitHub API error (${jobsResp.status}): ${errText}` }, jobsResp.status);
    }

    const jobsData = await jobsResp.json();
    const job = jobsData.jobs?.[0];

    if (!job) {
      return jsonResponse({
        ok: true,
        can_view_github: auth.canViewGithub,
        run_id: runId,
        status: 'queued',
        conclusion: null,
        steps: [],
        active_step: 'Waiting for GitHub runner...',
        progress_percent: 0,
        gofile_url: null
      });
    }

    const steps = (job.steps || []).map(s => ({
      number: s.number,
      name: s.name,
      status: s.status, // 'queued', 'in_progress', 'completed'
      conclusion: s.conclusion, // 'success', 'failure', 'skipped', null
      started_at: s.started_at,
      completed_at: s.completed_at
    }));

    const totalSteps = steps.length;
    const completedSteps = steps.filter(s => s.status === 'completed').length;
    const inProgressStep = steps.find(s => s.status === 'in_progress');
    const activeStep = inProgressStep ? inProgressStep.name : (job.status === 'completed' ? (job.conclusion === 'success' ? 'Completed successfully' : `Finished (${job.conclusion})`) : 'Waiting for runner...');

    let progressPercent = 0;
    if (job.status === 'completed') {
      progressPercent = 100;
    } else if (totalSteps > 0) {
      const rawPercent = Math.round((completedSteps / totalSteps) * 100);
      progressPercent = inProgressStep ? Math.min(95, Math.max(5, rawPercent + Math.round(50 / totalSteps))) : rawPercent;
    }

    let gofileUrl = null;
    if (job.id) {
      gofileUrl = await fetchGofileUrlForJob(repo, job.id, headers);
    }

    return jsonResponse({
      ok: true,
      can_view_github: auth.canViewGithub,
      run_id: runId,
      job_id: job.id,
      job_name: job.name,
      status: job.status,
      conclusion: job.conclusion,
      steps,
      total_steps: totalSteps,
      completed_steps: completedSteps,
      active_step: activeStep,
      progress_percent: progressPercent,
      gofile_url: gofileUrl,
      started_at: job.started_at,
      completed_at: job.completed_at
    });
  } catch (err) {
    return jsonResponse({ error: `Failed to fetch run details: ${err.message}` }, 500);
  }
}

/**
 * Fetch Gofile Download URL from GitHub Check Run Annotations
 */
async function fetchGofileUrlForJob(repo, jobId, headers) {
  if (gofileCache.has(jobId)) {
    return gofileCache.get(jobId);
  }

  try {
    const annotResp = await fetch(`https://api.github.com/repos/${repo}/check-runs/${jobId}/annotations`, { headers });
    if (annotResp.ok) {
      const annotations = await annotResp.json();
      if (Array.isArray(annotations)) {
        for (const ann of annotations) {
          if (ann.title === 'gofile_download_url' && ann.message) {
            const url = ann.message.trim();
            gofileCache.set(jobId, url);
            return url;
          }
          // Also match regex pattern if formatted differently
          const match = (ann.message || '').match(/https:\/\/gofile\.io\/d\/[a-zA-Z0-9_-]+/);
          if (match) {
            const url = match[0];
            gofileCache.set(jobId, url);
            return url;
          }
        }
      }
    }
  } catch (err) {
    console.warn('Error querying annotations for Gofile link:', err);
  }

  return null;
}

/**
 * Handle OTA Catalog Feed Query (Cached for 15 minutes)
 */
async function handleGetCatalog(request) {
  const url = new URL(request.url);
  const isNa = url.searchParams.get('na') === '1' || url.searchParams.get('na') === 'true';
  const targetUrl = isNa ? NA_API : OTA_API;

  try {
    const resp = await fetch(targetUrl, {
      headers: {
        'User-Agent': 'FlasherBuilder/1.0',
        'Accept': 'application/json'
      },
      cf: {
        cacheTtl: 900,
        cacheEverything: true
      }
    });

    if (!resp.ok) {
      return jsonResponse({ error: `OTA catalog service returned HTTP ${resp.status}` }, resp.status);
    }

    const data = await resp.json();
    return jsonResponse(data, 200, {
      'Cache-Control': 'public, max-age=900, s-maxage=900'
    });
  } catch (err) {
    return jsonResponse({ error: `Could not reach OTA catalog service: ${err.message}` }, 502);
  }
}

function getOwnerId(env) {
  return String(env.OWNER_TELEGRAM_ID || '1593626807').trim();
}

function isOwner(userId, env) {
  const uIdStr = String(userId || '').trim();
  if (!uIdStr) return false;
  const ownerId = getOwnerId(env);
  return Boolean(ownerId && uIdStr === ownerId);
}

function getAdminIds(env) {
  const owner = getOwnerId(env);
  const raw = env.ALLOWED_TELEGRAM_USER_IDS || '';
  const ids = raw.split(',').map(s => s.trim()).filter(Boolean);
  if (owner && !ids.includes(owner)) {
    ids.push(owner);
  }
  return ids;
}

function isOwnerOrAdmin(userId, env) {
  const uIdStr = String(userId || '').trim();
  if (!uIdStr) return false;
  const adminIds = getAdminIds(env);
  return adminIds.includes(uIdStr);
}

async function isUserAuthorized(userId, env) {
  const uIdStr = String(userId || '').trim();
  if (!uIdStr) return false;

  // Pre-configured Admins & Owner are always authorized
  if (isOwnerOrAdmin(uIdStr, env)) {
    return true;
  }

  // Cloudflare KV storage check
  if (env.AUTH_KV) {
    try {
      const record = await env.AUTH_KV.get(`user:${uIdStr}`);
      return Boolean(record);
    } catch (err) {
      console.warn('KV read error:', err);
    }
  }

  return false;
}

async function saveAuthorizedUser(userObj, env, approvedBy = 'admin') {
  const uIdStr = String(userObj.id || userObj.userId || '').trim();
  if (!uIdStr) return false;

  if (!env.AUTH_KV) {
    console.error('AUTH_KV binding is missing in Cloudflare Worker environment');
    return false;
  }

  const data = {
    id: uIdStr,
    username: userObj.username || '',
    first_name: userObj.first_name || '',
    last_name: userObj.last_name || '',
    approved_by: String(approvedBy),
    approved_at: new Date().toISOString()
  };

  try {
    await env.AUTH_KV.put(`user:${uIdStr}`, JSON.stringify(data));
    await env.AUTH_KV.delete(`pending:${uIdStr}`).catch(() => {});
    return true;
  } catch (err) {
    console.error('KV write error:', err);
    return false;
  }
}

async function revokeAuthorizedUser(userId, env) {
  const uIdStr = String(userId || '').trim();
  if (!uIdStr) return false;

  if (!env.AUTH_KV) {
    console.error('AUTH_KV binding is missing in Cloudflare Worker environment');
    return false;
  }

  try {
    await env.AUTH_KV.delete(`user:${uIdStr}`);
    return true;
  } catch (err) {
    console.error('KV delete error:', err);
    return false;
  }
}

async function listAllAuthorizedUsers(env) {
  const list = [];
  if (!env.AUTH_KV) return list;

  try {
    const keysRes = await env.AUTH_KV.list({ prefix: 'user:' });
    if (keysRes && keysRes.keys) {
      for (const k of keysRes.keys) {
        const val = await env.AUTH_KV.get(k.name);
        if (val) {
          try {
            list.push(JSON.parse(val));
          } catch (_) {}
        }
      }
    }
  } catch (err) {
    console.warn('KV list error:', err);
  }

  return list;
}

/**
 * Determine granular permissions for a Telegram user
 */
async function checkUserPermissions(user, env, devMode = false) {
  const publicAccess = isPublicAccessEnabled(env);
  const userId = String(user?.id || '');

  let isWhitelisted = false;
  if (devMode) {
    isWhitelisted = true;
  } else if (userId) {
    isWhitelisted = await isUserAuthorized(userId, env);
  }

  // Universal Build & Monitoring Access when ENABLE_PUBLIC_ACTION_RUNS is true;
  // otherwise strictly restricted to authorized users.
  const canTrigger = isWhitelisted || publicAccess;
  const canViewRuns = isWhitelisted || publicAccess;
  const canCancel = isWhitelisted || publicAccess;

  // View GitHub link is strictly and ONLY visible for the Owner (OWNER_TELEGRAM_ID)
  const canViewGithub = devMode ? true : isOwner(userId, env);

  return {
    isWhitelisted,
    canTrigger,
    canViewRuns,
    canCancel,
    canViewGithub,
    publicAccess
  };
}

/**
 * Authenticate Telegram initData and calculate permissions
 */
async function authenticateTelegram(request, env) {
  const authHeader = request.headers.get('Authorization') || '';
  const initData = authHeader.startsWith('tma ') ? authHeader.slice(4).trim() : '';

  // Local development fallback if bot token is not configured
  if (!env.TELEGRAM_BOT_TOKEN) {
    const devUser = { id: 999999999, first_name: 'DevAdmin', username: 'devadmin' };
    const perms = await checkUserPermissions(devUser, env, true);
    return {
      ok: true,
      devMode: true,
      user: devUser,
      ...perms
    };
  }

  if (!initData) {
    const publicAccess = isPublicAccessEnabled(env);
    if (publicAccess) {
      // Allow anonymous web visitor when public access is enabled
      const webUser = { id: 'web_visitor', first_name: 'Web Visitor', username: 'guest' };
      const perms = await checkUserPermissions(webUser, env, false);
      return {
        ok: true,
        user: webUser,
        isWebGuest: true,
        ...perms
      };
    }
    return {
      ok: false,
      error: 'Access restricted: Direct web access is disabled. Please launch this app via the Telegram Bot.'
    };
  }

  const verification = await verifyTelegramWebAppData(initData, env.TELEGRAM_BOT_TOKEN);
  if (!verification.valid) {
    return { ok: false, error: `Unauthorized: ${verification.reason}` };
  }

  const perms = await checkUserPermissions(verification.user, env, false);

  return {
    ok: true,
    user: verification.user,
    ...perms
  };
}

/**
 * Verify Telegram WebApp initData HMAC-SHA256 signature
 */
async function verifyTelegramWebAppData(telegramInitData, botToken) {
  try {
    const params = new URLSearchParams(telegramInitData);
    const hash = params.get('hash');
    if (!hash) return { valid: false, reason: 'Missing hash parameter' };

    // Check expiration (24h)
    const authDate = parseInt(params.get('auth_date') || '0', 10);
    const now = Math.floor(Date.now() / 1000);
    if (authDate > 0 && now - authDate > 86400) {
      return { valid: false, reason: 'Session expired (older than 24 hours)' };
    }

    params.delete('hash');
    const keys = Array.from(params.keys()).sort();
    const dataCheckString = keys.map(k => `${k}=${params.get(k)}`).join('\n');

    const encoder = new TextEncoder();
    const secretKeyMaterial = await crypto.subtle.importKey(
      'raw',
      encoder.encode('WebAppData'),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const secretKeyBuffer = await crypto.subtle.sign(
      'HMAC',
      secretKeyMaterial,
      encoder.encode(botToken)
    );

    const secretKey = await crypto.subtle.importKey(
      'raw',
      secretKeyBuffer,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const signatureBuffer = await crypto.subtle.sign(
      'HMAC',
      secretKey,
      encoder.encode(dataCheckString)
    );

    const calculatedHash = Array.from(new Uint8Array(signatureBuffer))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');

    if (calculatedHash !== hash) {
      return { valid: false, reason: 'HMAC signature verification failed' };
    }

    let user = null;
    const userStr = params.get('user');
    if (userStr) {
      try {
        user = JSON.parse(userStr);
      } catch (_) {}
    }

    return { valid: true, user };
  } catch (err) {
    return { valid: false, reason: err.message };
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function sendTelegramApi(token, method, payload) {
  if (!token) return { ok: false, error: 'No Telegram bot token configured' };
  try {
    const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return await res.json();
  } catch (err) {
    console.error(`Telegram API error (${method}):`, err);
    return { ok: false, error: err.message };
  }
}

/**
 * Handle Telegram Bot Webhook Updates
 */
async function handleTelegramWebhook(request, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    return jsonResponse({ error: 'TELEGRAM_BOT_TOKEN not configured' }, 500);
  }

  let update;
  try {
    update = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON body' }, 400);
  }

  const origin = new URL(request.url).origin;

  try {
    if (update.message) {
      await handleWebhookMessage(update.message, env, token, origin);
    } else if (update.callback_query) {
      await handleWebhookCallbackQuery(update.callback_query, env, token, origin);
    }
  } catch (err) {
    console.error('Webhook processing error:', err);
  }

  return jsonResponse({ ok: true });
}

/**
 * Handle Incoming Telegram Messages (/start, /whitelist, /auth, /revoke)
 */
async function handleWebhookMessage(message, env, token, origin) {
  const chatId = message.chat?.id;
  const from = message.from;
  const text = (message.text || '').trim();
  if (!chatId || !from) return;

  const userId = String(from.id);

  // Command: /start
  if (text.startsWith('/start')) {
    const isAuthorized = await isUserAuthorized(userId, env);

    if (isAuthorized) {
      const welcomeText = 
        `⚡ <b>Flasher Builder-Macan</b>\n\n` +
        `Welcome back, <b>${escapeHtml(from.first_name || from.username || 'User')}</b>!\n` +
        `Your account is authorized and ready to build.\n\n` +
        `🆔 <b>Telegram ID:</b> <code>${userId}</code>\n` +
        `✅ <b>Status:</b> Authorized\n\n` +
        `Tap below to launch the Flasher Builder Mini App:`;

      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: welcomeText,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '🚀 Launch Flasher Builder', web_app: { url: origin } }],
            [{ text: '🔄 Check Status', callback_data: `check_status:${userId}` }]
          ]
        }
      });
    } else {
      const unauthText = 
        `🔒 <b>Authorization Required</b>\n\n` +
        `Welcome to <b>Flasher Builder Macan</b>.\n` +
        `This bot requires administrator authorization before you can trigger builds.\n\n` +
        `👤 <b>Name:</b> ${escapeHtml(from.first_name || 'User')}${from.username ? ` (@${escapeHtml(from.username)})` : ''}\n` +
        `🆔 <b>Your Telegram ID:</b> <code>${userId}</code>\n` +
        `⚠️ <b>Status:</b> Not Authorized\n\n` +
        `Tap <b>Request Authorization</b> below to send an access request to the administrator.`;

      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: unauthText,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '📩 Request Authorization', callback_data: `req_auth:${userId}` }],
            [{ text: '🔄 Check Status', callback_data: `check_status:${userId}` }]
          ]
        }
      });
    }
    return;
  }

  // Admin Commands (Only Owner / Admin)
  if (isOwnerOrAdmin(userId, env)) {
    if (text === '/whitelist') {
      await sendAdminWhitelist(chatId, env, token);
      return;
    }

    if (text.startsWith('/auth ')) {
      const targetId = text.replace('/auth', '').trim();
      if (!/^\d+$/.test(targetId)) {
        await sendTelegramApi(token, 'sendMessage', {
          chat_id: chatId,
          text: '❌ Invalid user ID. Usage: <code>/auth &lt;TELEGRAM_USER_ID&gt;</code>',
          parse_mode: 'HTML'
        });
        return;
      }

      await saveAuthorizedUser({ id: targetId, first_name: 'Manual Auth', username: '' }, env, userId);
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: `✅ <b>User Authorized</b>\nID: <code>${targetId}</code> has been approved.`,
        parse_mode: 'HTML'
      });

      // Try notifying the user
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: targetId,
        text: `🎉 <b>Access Granted!</b>\nAn administrator has authorized your account.\nYou can now launch Flasher Builder:`,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '🚀 Launch Flasher Builder', web_app: { url: origin } }]
          ]
        }
      }).catch(() => {});
      return;
    }

    if (text.startsWith('/revoke ')) {
      const targetId = text.replace('/revoke', '').trim();
      if (!/^\d+$/.test(targetId)) {
        await sendTelegramApi(token, 'sendMessage', {
          chat_id: chatId,
          text: '❌ Invalid user ID. Usage: <code>/revoke &lt;TELEGRAM_USER_ID&gt;</code>',
          parse_mode: 'HTML'
        });
        return;
      }

      await revokeAuthorizedUser(targetId, env);
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: `🗑 <b>User Revoked</b>\nID: <code>${targetId}</code> authorization has been removed.`,
        parse_mode: 'HTML'
      });

      // Try notifying the user
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: targetId,
        text: `⚠️ <b>Authorization Revoked</b>\nYour access to Flasher Builder has been revoked by an administrator.`,
        parse_mode: 'HTML'
      }).catch(() => {});
      return;
    }

    if (text === '/help') {
      const helpText = 
        `🛠 <b>Admin Commands:</b>\n\n` +
        `• <code>/whitelist</code> - View all authorized users & admins\n` +
        `• <code>/auth &lt;id&gt;</code> - Manually authorize a user ID\n` +
        `• <code>/revoke &lt;id&gt;</code> - Revoke a user ID\n` +
        `• <code>/start</code> - Check bot status & open Mini App`;

      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: helpText,
        parse_mode: 'HTML'
      });
      return;
    }
  } else {
    // Non-admin feedback for any unrecognized slash command
    if (text.startsWith('/')) {
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: chatId,
        text: `ℹ️ Send /start to check your authorization status or open the Mini App.`,
        parse_mode: 'HTML'
      });
    }
  }
}

/**
 * Handle Telegram Callback Queries (Request Auth, Approve, Reject, Check Status)
 */
async function handleWebhookCallbackQuery(callbackQuery, env, token, origin) {
  const queryId = callbackQuery.id;
  const from = callbackQuery.from;
  const data = callbackQuery.data || '';
  const message = callbackQuery.message;
  const chatId = message?.chat?.id;
  const messageId = message?.message_id;

  const senderId = String(from.id);

  // 1. User taps "Request Authorization"
  if (data.startsWith('req_auth:')) {
    const targetUserId = data.split(':')[1];
    const isAlreadyAuth = await isUserAuthorized(targetUserId, env);

    if (isAlreadyAuth) {
      await sendTelegramApi(token, 'answerCallbackQuery', {
        callback_query_id: queryId,
        text: '✅ You are already authorized!'
      });
      if (chatId && messageId) {
        await sendTelegramApi(token, 'editMessageText', {
          chat_id: chatId,
          message_id: messageId,
          text: `🎉 <b>You are Authorized!</b>\n\nYou can launch Flasher Builder right now:`,
          parse_mode: 'HTML',
          reply_markup: {
            inline_keyboard: [
              [{ text: '🚀 Launch Flasher Builder', web_app: { url: origin } }]
            ]
          }
        });
      }
      return;
    }

    // Save pending info to KV or memory so owner has details
    const pendingData = {
      id: targetUserId,
      first_name: from.first_name || '',
      last_name: from.last_name || '',
      username: from.username || '',
      requested_at: new Date().toISOString()
    };
    if (env.AUTH_KV) {
      try {
        await env.AUTH_KV.put(`pending:${targetUserId}`, JSON.stringify(pendingData));
      } catch (err) {
        console.warn('KV pending save error:', err);
      }
    }

    // Forward request to Owner / Admins
    const userDisplay = `${escapeHtml(from.first_name || '')} ${escapeHtml(from.last_name || '')}`.trim();
    const usernameTag = from.username ? ` (@${escapeHtml(from.username)})` : '';
    const nowStr = new Date().toUTCString();

    const ownerNotice = 
      `🔔 <b>Authorization Request</b>\n\n` +
      `A user is requesting access to Flasher Builder:\n\n` +
      `👤 <b>User:</b> ${userDisplay || 'Unknown'}${usernameTag}\n` +
      `🆔 <b>User ID:</b> <code>${targetUserId}</code>\n` +
      `⏰ <b>Requested:</b> ${nowStr}\n\n` +
      `Choose an action:`;

    const adminKeyboard = {
      inline_keyboard: [
        [
          { text: '✅ Authorize', callback_data: `auth_appr:${targetUserId}` },
          { text: '❌ Reject', callback_data: `auth_rejc:${targetUserId}` }
        ]
      ]
    };

    const adminIds = getAdminIds(env);
    for (const aId of adminIds) {
      await sendTelegramApi(token, 'sendMessage', {
        chat_id: aId,
        text: ownerNotice,
        parse_mode: 'HTML',
        reply_markup: adminKeyboard
      }).catch(err => console.error(`Failed sending request to admin ${aId}:`, err));
    }

    // Acknowledge callback
    await sendTelegramApi(token, 'answerCallbackQuery', {
      callback_query_id: queryId,
      text: '📨 Request forwarded to owner!'
    });

    // Update user's message
    if (chatId && messageId) {
      await sendTelegramApi(token, 'editMessageText', {
        chat_id: chatId,
        message_id: messageId,
        text: 
          `⏳ <b>Authorization Request Sent</b>\n\n` +
          `Your access request has been sent to the administrator.\n` +
          `🆔 <b>Your ID:</b> <code>${targetUserId}</code>\n\n` +
          `You will receive a notification as soon as it is approved.`,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '🔄 Check Status', callback_data: `check_status:${targetUserId}` }]
          ]
        }
      });
    }
    return;
  }

  // 2. Owner taps "Authorize"
  if (data.startsWith('auth_appr:')) {
    if (!isOwnerOrAdmin(senderId, env)) {
      await sendTelegramApi(token, 'answerCallbackQuery', {
        callback_query_id: queryId,
        text: '❌ Access Denied: Only administrators can approve requests.',
        show_alert: true
      });
      return;
    }

    const targetUserId = data.split(':')[1];

    let userDetails = { id: targetUserId };
    if (env.AUTH_KV) {
      try {
        const raw = await env.AUTH_KV.get(`pending:${targetUserId}`);
        if (raw) userDetails = JSON.parse(raw);
      } catch (err) {
        console.warn('KV pending get error:', err);
      }
    }

    await saveAuthorizedUser(userDetails, env, senderId);

    await sendTelegramApi(token, 'answerCallbackQuery', {
      callback_query_id: queryId,
      text: `✅ User ${targetUserId} approved!`
    });

    // Update admin's message
    if (chatId && messageId) {
      const uDisplay = userDetails.first_name ? `${escapeHtml(userDetails.first_name)} ` : '';
      const uName = userDetails.username ? `(@${escapeHtml(userDetails.username)}) ` : '';
      await sendTelegramApi(token, 'editMessageText', {
        chat_id: chatId,
        message_id: messageId,
        text: 
          `✅ <b>User Authorized!</b>\n\n` +
          `👤 <b>User:</b> ${uDisplay}${uName}\n` +
          `🆔 <b>User ID:</b> <code>${targetUserId}</code>\n` +
          `👑 <b>Approved By:</b> ${escapeHtml(from.first_name || 'Admin')} (<code>${senderId}</code>)\n` +
          `⏰ <b>Date:</b> ${new Date().toUTCString()}`,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '🗑 Revoke Access', callback_data: `auth_rejc:${targetUserId}` }]
          ]
        }
      });
    }

    // Notify the user directly
    await sendTelegramApi(token, 'sendMessage', {
      chat_id: targetUserId,
      text: 
        `🎉 <b>Access Granted!</b>\n\n` +
        `Your authorization request has been approved by the administrator.\n` +
        `You can now launch Flasher Builder and start builds!`,
      parse_mode: 'HTML',
      reply_markup: {
        inline_keyboard: [
          [{ text: '🚀 Launch Flasher Builder', web_app: { url: origin } }]
        ]
      }
    }).catch(err => console.warn(`Could not notify approved user ${targetUserId}:`, err));

    return;
  }

  // 3. Owner taps "Reject" or "Revoke"
  if (data.startsWith('auth_rejc:')) {
    if (!isOwnerOrAdmin(senderId, env)) {
      await sendTelegramApi(token, 'answerCallbackQuery', {
        callback_query_id: queryId,
        text: '❌ Access Denied: Only administrators can reject/revoke.',
        show_alert: true
      });
      return;
    }

    const targetUserId = data.split(':')[1];
    await revokeAuthorizedUser(targetUserId, env);

    await sendTelegramApi(token, 'answerCallbackQuery', {
      callback_query_id: queryId,
      text: `User ${targetUserId} rejected/revoked.`
    });

    if (chatId && messageId) {
      await sendTelegramApi(token, 'editMessageText', {
        chat_id: chatId,
        message_id: messageId,
        text: 
          `❌ <b>Authorization Rejected / Revoked</b>\n\n` +
          `🆔 <b>User ID:</b> <code>${targetUserId}</code>\n` +
          `👮 <b>Action By:</b> ${escapeHtml(from.first_name || 'Admin')} (<code>${senderId}</code>)\n` +
          `⏰ <b>Date:</b> ${new Date().toUTCString()}`,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '✅ Re-Authorize', callback_data: `auth_appr:${targetUserId}` }]
          ]
        }
      });
    }

    // Notify user
    await sendTelegramApi(token, 'sendMessage', {
      chat_id: targetUserId,
      text: `❌ <b>Access Denied</b>\n\nYour authorization request was rejected or revoked by the administrator.`,
      parse_mode: 'HTML'
    }).catch(() => {});

    return;
  }

  // 4. User taps "Check Status"
  if (data.startsWith('check_status:')) {
    const targetUserId = data.split(':')[1];
    const isAuthorized = await isUserAuthorized(targetUserId, env);

    if (isAuthorized) {
      await sendTelegramApi(token, 'answerCallbackQuery', {
        callback_query_id: queryId,
        text: '✅ You are authorized!'
      });

      if (chatId && messageId) {
        await sendTelegramApi(token, 'editMessageText', {
          chat_id: chatId,
          message_id: messageId,
          text: 
            `⚡ <b>Access Approved!</b>\n\n` +
            `Your account (ID: <code>${targetUserId}</code>) is authorized.\n` +
            `Tap the button below to launch the Mini App:`,
          parse_mode: 'HTML',
          reply_markup: {
            inline_keyboard: [
              [{ text: '🚀 Launch Flasher Builder', web_app: { url: origin } }]
            ]
          }
        });
      }
    } else {
      await sendTelegramApi(token, 'answerCallbackQuery', {
        callback_query_id: queryId,
        text: '⏳ Still pending or not authorized.',
        show_alert: true
      });
    }
    return;
  }
}

/**
 * Send Whitelist & Admin Overview to Administrator
 */
async function sendAdminWhitelist(chatId, env, token) {
  const adminIds = getAdminIds(env);
  const authorizedUsers = await listAllAuthorizedUsers(env);

  let text = `📋 <b>Authorized Users & Admins</b>\n\n`;
  text += `👑 <b>Administrators:</b>\n`;
  for (const aId of adminIds) {
    text += ` • <code>${aId}</code>\n`;
  }

  text += `\n👥 <b>Approved Users (${authorizedUsers.length}):</b>\n`;
  if (authorizedUsers.length === 0) {
    text += ` <i>No additional users authorized yet.</i>\n`;
  } else {
    for (const u of authorizedUsers) {
      const uName = u.username ? ` (@${escapeHtml(u.username)})` : '';
      const fName = u.first_name ? `${escapeHtml(u.first_name)} ` : '';
      text += ` • <code>${u.id}</code> - ${fName}${uName}(by ${u.approved_by || 'admin'})\n`;
    }
  }

  text += `\n<i>Commands: /auth &lt;id&gt; | /revoke &lt;id&gt;</i>`;

  await sendTelegramApi(token, 'sendMessage', {
    chat_id: chatId,
    text,
    parse_mode: 'HTML'
  });
}

/**
 * Webhook Management Helpers
 */
async function handleSetWebhook(request, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    return jsonResponse({ error: 'TELEGRAM_BOT_TOKEN secret is missing' }, 500);
  }

  const origin = new URL(request.url).origin;
  const webhookUrl = `${origin}/api/telegram-webhook`;

  const telegramEndpoint = `https://api.telegram.org/bot${token}/setWebhook`;
  try {
    const resp = await fetch(telegramEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: webhookUrl,
        drop_pending_updates: true,
        allowed_updates: ['message', 'callback_query']
      })
    });

    const result = await resp.json();
    return jsonResponse({
      registered_url: webhookUrl,
      telegram_response: result
    });
  } catch (err) {
    return jsonResponse({ error: `Failed to set webhook: ${err.message}` }, 500);
  }
}

async function handleGetWebhookInfo(request, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    return jsonResponse({ error: 'TELEGRAM_BOT_TOKEN secret is missing' }, 500);
  }

  try {
    const resp = await fetch(`https://api.telegram.org/bot${token}/getWebhookInfo`);
    const data = await resp.json();
    return jsonResponse(data);
  } catch (err) {
    return jsonResponse({ error: err.message }, 500);
  }
}

function jsonResponse(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...getCorsHeaders(),
      ...extraHeaders
    }
  });
}

function getCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
  };
}
