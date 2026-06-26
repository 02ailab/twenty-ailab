# Dashboard App panel: a small HTML page Chatwoot embeds as an iframe in the
# conversation view, plus a JSON endpoint it calls. The Twenty API key stays
# server-side here — the browser never sees it. This is the ONLY publicly exposed
# part of the bridge (Traefik ingress routes /panel only); access is gated by a
# shared secret carried in the Dashboard App URL.
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from app import db, deps
from app.config import get_settings
from app.structured_log import log_event

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_secret(secret: str) -> None:
    # Fail-closed: the panel is the ONLY public surface, so a missing/blank expected
    # secret must NOT disable the gate (config makes panel_shared_secret required).
    expected = get_settings().panel_shared_secret
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/panel/api/contact/{chatwoot_contact_id}")
async def panel_contact(chatwoot_contact_id: int, secret: str = Query(default="")) -> JSONResponse:
    _check_secret(secret)
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
    # Require a valid secret BEFORE serving the page, and echo back ONLY the
    # caller-supplied secret — never fall back to the configured secret, or an
    # unauthenticated GET /panel would leak it into the page source.
    _check_secret(secret)
    # The page listens for Chatwoot's postMessage (appContext) to learn the current
    # contact id, then fetches the CRM card from this service.
    html = _PANEL_HTML.replace("__SECRET__", secret)
    log_event(logger, "panel_served", "panel page served")
    return HTMLResponse(html)


_PANEL_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
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
  var SECRET = "__SECRET__";
  var contactId = null;

  function render(html) { document.getElementById("root").innerHTML = html; }

  function field(label, value) {
    if (!value) return "";
    return '<div class="row"><div class="label">' + label + '</div>' +
           '<div class="value">' + String(value).replace(/</g, "&lt;") + '</div></div>';
  }

  function load(id) {
    fetch("/panel/api/contact/" + id + "?secret=" + encodeURIComponent(SECRET))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.linked) { render('<div class="muted">Контакт ещё не связан с Twenty.</div>'); return; }
        var name = (d.firstName + " " + d.lastName).trim() || "Без имени";
        var html = "<div class=\\"row\\"><b>" + name.replace(/</g, "&lt;") + "</b></div>";
        html += field("Email", d.primaryEmail);
        html += field("Телефон", d.primaryPhone);
        html += field("Должность", d.jobTitle);
        if (d.openInCrmUrl) html += '<a class="btn" href="' + d.openInCrmUrl + '" target="_blank">Открыть в Twenty</a>';
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
