# Dashboard App panel: a small HTML page Chatwoot embeds as an iframe in the
# conversation view, plus a JSON endpoint it calls. The Twenty API key stays
# server-side here — the browser never sees it. This is the ONLY publicly exposed
# part of the bridge (Traefik ingress routes /panel only). The page entry
# (/panel) is gated by the shared secret in the Dashboard App URL; the page then
# mints a short-lived token so the JSON endpoint (/panel/api) is called with the
# token, not the durable secret. The API endpoint is also per-IP rate-limited to
# blunt id-enumeration.
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import db, deps
from app.config import get_settings
from app.ratelimit import RateLimiter
from app.security import mint_panel_token, verify_panel_token
from app.structured_log import log_event

logger = logging.getLogger(__name__)
router = APIRouter()

# Single-replica Deployment, so a process-local limiter is enough. Built lazily
# from config on first use (settings are lru_cached).
_rate_limiter: RateLimiter | None = None


def _limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(get_settings().panel_rate_limit_per_minute, 60.0)
    return _rate_limiter


# Pod-network hops we treat as trusted proxies in X-Forwarded-For (k3s/flannel pod CIDR
# 10.42.0.0/16 — Traefik runs here). PLAT-6 set Traefik to externalTrafficPolicy: Local +
# forwardedHeaders.trustedIPs=10.42.0.0/16, so the real external client IP is the hop
# Traefik appends; any client-supplied XFF prefix sits to its LEFT.
_TRUSTED_PROXY_PREFIXES = ("10.42.",)


def _client_key(request: Request) -> str:
    # Rate-limit key = the rightmost X-Forwarded-For entry that is NOT a trusted pod-network
    # hop, i.e. the client IP Traefik actually observed. Taking the LEFTMOST entry (the old
    # behaviour) trusts a client-controlled value: an attacker rotating a forged leftmost XFF
    # per request would get a fresh bucket each time and slip the id-enumeration brake (P2-7).
    # Walking from the right past trusted proxies defeats that — a forged prefix is ignored.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        for part in reversed([p.strip() for p in xff.split(",") if p.strip()]):
            if not part.startswith(_TRUSTED_PROXY_PREFIXES):
                return part
    return request.client.host if request.client else "unknown"


def _check_secret(secret: str) -> None:
    # Page entry gate (/panel). Fail-closed: the panel is the ONLY public surface,
    # so a missing/blank expected secret must NOT disable the gate (config makes
    # panel_shared_secret required). Constant-time compare to avoid leaking the
    # secret via response timing.
    expected = get_settings().panel_shared_secret
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="forbidden")


def _check_api_auth(token: str, secret: str) -> None:
    # API gate (/panel/api). Prefer the short-lived page token; accept the static
    # secret only as a fallback (e.g. an iframe page cached before this rollout).
    # Both checks are constant-time and fail-closed.
    expected = get_settings().panel_shared_secret
    if not expected:
        raise HTTPException(status_code=403, detail="forbidden")
    if token and verify_panel_token(expected, token):
        return
    if secret and hmac.compare_digest(secret, expected):
        return
    raise HTTPException(status_code=403, detail="forbidden")


@router.get("/panel/api/contact/{chatwoot_contact_id}")
async def panel_contact(request: Request, chatwoot_contact_id: int,
                        token: str = Query(default=""),
                        secret: str = Query(default="")) -> JSONResponse:
    if not _limiter().allow(_client_key(request)):
        log_event(logger, "panel_rate_limited", "panel api rate limit hit",
                  level=logging.WARNING, client=_client_key(request))
        raise HTTPException(status_code=429, detail="too many requests")
    _check_api_auth(token, secret)
    twenty_person_id = await db.get_twenty_person_id(chatwoot_contact_id)
    if not twenty_person_id:
        return JSONResponse({"linked": False})

    person = await deps.require_twenty().get_person(twenty_person_id)
    if not person:
        return JSONResponse({"linked": False})

    name = person.get("name") or {}
    emails = person.get("emails") or {}
    phones = person.get("phones") or {}
    settings = get_settings()
    return JSONResponse({
        "linked": True,
        "twentyPersonId": twenty_person_id,
        "firstName": name.get("firstName") or "",
        "lastName": name.get("lastName") or "",
        "primaryEmail": emails.get("primaryEmail") or "",
        "primaryPhone": phones.get("primaryPhoneNumber") or "",
        "jobTitle": person.get("jobTitle") or "",
        "companyId": person.get("companyId"),
        "openInCrmUrl": f"{settings.twenty_public_url}/object/person/{twenty_person_id}",
    })


@router.get("/panel", response_class=HTMLResponse)
async def panel_page(secret: str = Query(default="")) -> HTMLResponse:
    # Require a valid secret BEFORE serving the page. Then mint a SHORT-LIVED token
    # (signed with the same secret) and embed THAT — never the durable secret — so
    # the secret stops riding in the API calls / Referer / browser history.
    _check_secret(secret)
    settings = get_settings()
    token = mint_panel_token(settings.panel_shared_secret, settings.panel_token_ttl_seconds)
    # The page listens for Chatwoot's postMessage (appContext) to learn the current
    # contact id, then fetches the CRM card from this service.
    html = _PANEL_HTML.replace("__TOKEN__", token)
    log_event(logger, "panel_served", "panel page served")
    return HTMLResponse(html)


_PANEL_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<!-- Never send the panel URL as Referer when the agent clicks through to Twenty on
     another origin — the iframe entry URL carries the shared secret. -->
<meta name="referrer" content="no-referrer" />
<title>Twenty CRM</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 12px; color: #1a1a1a; }
  .muted { color: #888; font-size: 13px; }
  .row { margin: 6px 0; }
  .label { color: #888; font-size: 12px; }
  .value { font-size: 14px; }
  a.btn { display: inline-block; margin-top: 10px; padding: 8px 12px; background: #1456f0;
          color: #fff; border-radius: 6px; text-decoration: none; font-size: 13px; }
</style>
</head>
<body>
  <div id="root"><div class="muted">Загрузка карточки Twenty…</div></div>
<script>
  var TOKEN = "__TOKEN__";
  var contactId = null;
  var reloaded = false;

  function render(html) { document.getElementById("root").innerHTML = html; }

  // The token expires (~30 min). If the agent leaves the panel open past that,
  // the API returns 401/403 — re-fetch a fresh page once (the iframe entry URL
  // carries the secret, so a reload re-mints a token).
  function refreshOnce() {
    if (reloaded) { render('<div class="muted">Сессия панели истекла. Обновите вкладку.</div>'); return; }
    reloaded = true; location.reload();
  }

  function field(label, value) {
    if (!value) return "";
    return '<div class="row"><div class="label">' + label + '</div>' +
           '<div class="value">' + String(value).replace(/</g, "&lt;") + '</div></div>';
  }

  function load(id) {
    fetch("/panel/api/contact/" + id + "?token=" + encodeURIComponent(TOKEN))
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { refreshOnce(); return null; }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        if (!d.linked) { render('<div class="muted">Контакт ещё не связан с Twenty.</div>'); return; }
        var name = (d.firstName + " " + d.lastName).trim() || "Без имени";
        var html = "<div class=\\"row\\"><b>" + name.replace(/</g, "&lt;") + "</b></div>";
        html += field("Email", d.primaryEmail);
        html += field("Телефон", d.primaryPhone);
        html += field("Должность", d.jobTitle);
        if (d.openInCrmUrl) html += '<a class="btn" href="' + d.openInCrmUrl + '" target="_blank" rel="noopener noreferrer">Открыть в Twenty</a>';
        render(html);
      })
      .catch(function () { render('<div class="muted">Не удалось загрузить карточку.</div>'); });
  }

  // Chatwoot posts the app context to the iframe; shapes vary, so dig for the contact id.
  window.addEventListener("message", function (e) {
    var data = e.data;
    try { if (typeof data === "string") data = JSON.parse(data); } catch (_) { return; }
    var ctx = (data && data.data) ? data.data : data;
    if (!ctx) return;
    var id = (ctx.contact && ctx.contact.id) ||
             (ctx.conversation && ctx.conversation.meta && ctx.conversation.meta.sender && ctx.conversation.meta.sender.id);
    if (id && id !== contactId) { contactId = id; load(id); }
  });

  // Ask Chatwoot to send context (some versions wait for this handshake).
  if (window.parent) { window.parent.postMessage("chatwoot-dashboard-app:fetch-info", "*"); }
</script>
</body>
</html>
"""
