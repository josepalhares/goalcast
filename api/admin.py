"""Admin panel routes for GoalCast."""
import logging
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db import (
    get_pending_requests, get_all_requests, get_all_users,
    approve_access_request, deny_access_request, add_email_to_whitelist,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _require_admin(request: Request):
    user = request.session.get("user")
    if not user or user.get("role") != "admin":
        return None
    return user


def _render_page(pending: list, users: list, all_requests: list, msg: str = "") -> str:
    # Flash message
    flash = ""
    if msg == "approved":
        flash = '<div class="bg-green-50 border border-green-200 text-green-700 rounded-xl px-4 py-2.5 text-sm">User approved and added to whitelist.</div>'
    elif msg == "denied":
        flash = '<div class="bg-amber-50 border border-amber-200 text-amber-700 rounded-xl px-4 py-2.5 text-sm">Request denied.</div>'
    elif msg == "added":
        flash = '<div class="bg-blue-50 border border-blue-200 text-blue-700 rounded-xl px-4 py-2.5 text-sm">User added to whitelist.</div>'
    elif msg == "exists":
        flash = '<div class="bg-gray-50 border border-gray-200 text-gray-600 rounded-xl px-4 py-2.5 text-sm">That email is already on the whitelist.</div>'

    # Pending requests section
    if pending:
        rows = ""
        for r in pending:
            date = r["requested_at"][:10] if r["requested_at"] else "—"
            rows += f"""
            <div class="bg-white rounded-xl border border-[#E5E7EB] px-4 py-3 flex items-center justify-between gap-4">
                <div class="min-w-0">
                    <p class="text-[13px] font-semibold text-[#1D1D1F] truncate">{r['name'] or '—'}</p>
                    <p class="text-[11px] text-[#6E6E73] truncate">{r['email']} &middot; requested {date}</p>
                </div>
                <div class="flex gap-2 shrink-0">
                    <form method="POST" action="/admin/approve">
                        <input type="hidden" name="email" value="{r['email']}">
                        <button class="h-8 px-3 rounded-lg bg-[#22C55E]/10 text-[#16A34A] text-[11px] font-semibold hover:bg-[#22C55E]/20 transition">Approve</button>
                    </form>
                    <form method="POST" action="/admin/deny">
                        <input type="hidden" name="email" value="{r['email']}">
                        <button class="h-8 px-3 rounded-lg bg-[#EF4444]/10 text-[#DC2626] text-[11px] font-semibold hover:bg-[#EF4444]/20 transition">Deny</button>
                    </form>
                </div>
            </div>"""
        pending_section = f'<div class="space-y-2">{rows}</div>'
    else:
        pending_section = '<p class="text-sm text-[#6E6E73]">No pending requests.</p>'

    # Active users section
    if users:
        user_rows = ""
        for u in users:
            last = u["last_login"][:16].replace("T", " ") if u.get("last_login") else "Never"
            role_cls = "bg-[#007AFF]/10 text-[#007AFF]" if u["role"] == "admin" else "bg-gray-100 text-[#6E6E73]"
            user_rows += f"""
            <div class="bg-white rounded-xl border border-[#E5E7EB] px-4 py-3 flex items-center justify-between gap-4">
                <div class="min-w-0">
                    <p class="text-[13px] font-semibold text-[#1D1D1F] truncate">{u['name'] or '—'}</p>
                    <p class="text-[11px] text-[#6E6E73] truncate">{u['email']}</p>
                </div>
                <div class="text-right shrink-0">
                    <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full {role_cls}">{u['role']}</span>
                    <p class="text-[11px] text-[#6E6E73] mt-1">{last}</p>
                </div>
            </div>"""
        users_section = f'<div class="space-y-2">{user_rows}</div>'
    else:
        users_section = '<p class="text-sm text-[#6E6E73]">No users yet.</p>'

    # Recent denied requests (collapsed)
    denied = [r for r in all_requests if r["status"] == "denied"]
    denied_section = ""
    if denied:
        denied_rows = ""
        for r in denied:
            date = r["requested_at"][:10] if r["requested_at"] else "—"
            denied_rows += f"""
            <div class="flex items-center justify-between px-4 py-2.5 border-b border-gray-50 last:border-0">
                <div>
                    <p class="text-[13px] text-[#1D1D1F]">{r['name'] or '—'}</p>
                    <p class="text-[11px] text-[#6E6E73]">{r['email']} &middot; {date}</p>
                </div>
                <form method="POST" action="/admin/approve">
                    <input type="hidden" name="email" value="{r['email']}">
                    <button class="h-7 px-2.5 rounded-lg bg-[#22C55E]/10 text-[#16A34A] text-[10px] font-semibold hover:bg-[#22C55E]/20 transition">Approve</button>
                </form>
            </div>"""
        denied_section = f"""
        <section>
            <details class="group">
                <summary class="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-[#6E6E73] mb-3 list-none flex items-center gap-1.5">
                    <svg class="w-3 h-3 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                    Denied Requests ({len(denied)})
                </summary>
                <div class="bg-white rounded-xl border border-[#E5E7EB] overflow-hidden mt-2">{denied_rows}</div>
            </details>
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GoalCast — Admin</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>body {{ font-family: 'Inter', system-ui, sans-serif; }}</style>
</head>
<body class="bg-[#F5F5F7] min-h-screen">
    <header class="bg-white border-b border-[#E5E7EB] h-14 flex items-center sticky top-0 z-10">
        <div class="max-w-3xl w-full mx-auto px-6 flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <div class="w-8 h-8 rounded-lg bg-[#007AFF] flex items-center justify-center text-white font-bold text-sm">G</div>
                <span class="text-[15px] font-bold text-[#1D1D1F]">GoalCast</span>
                <span class="text-[#D1D5DB] font-light text-lg">/</span>
                <span class="text-[15px] font-semibold text-[#6E6E73]">Admin</span>
            </div>
            <a href="/" class="text-[13px] text-[#007AFF] font-medium hover:underline">← Back to app</a>
        </div>
    </header>

    <main class="max-w-3xl mx-auto px-6 py-8 space-y-8">
        {flash}

        <section>
            <h2 class="text-[11px] font-semibold uppercase tracking-wider text-[#6E6E73] mb-3">
                Pending Requests ({len(pending)})
            </h2>
            {pending_section}
        </section>

        <section>
            <h2 class="text-[11px] font-semibold uppercase tracking-wider text-[#6E6E73] mb-3">
                Active Users ({len(users)})
            </h2>
            {users_section}
        </section>

        <section>
            <h2 class="text-[11px] font-semibold uppercase tracking-wider text-[#6E6E73] mb-3">Add User Manually</h2>
            <div class="bg-white rounded-xl border border-[#E5E7EB] p-4">
                <form method="POST" action="/admin/add-user" class="flex gap-2">
                    <input type="email" name="email" placeholder="email@example.com" required
                           class="flex-1 h-9 px-3 rounded-lg border border-[#E5E7EB] text-sm focus:outline-none focus:border-[#007AFF] transition bg-white">
                    <button type="submit"
                            class="h-9 px-4 rounded-lg bg-[#007AFF] text-white text-sm font-semibold hover:bg-blue-600 transition">
                        Add
                    </button>
                </form>
            </div>
        </section>

        {denied_section}
    </main>
</body>
</html>"""


# ─── Routes ──────────────────────────────────────────────────

@router.get("")
async def admin_panel(request: Request, msg: str = ""):
    if not _require_admin(request):
        return RedirectResponse("/")
    pending = get_pending_requests()
    users = get_all_users()
    all_req = get_all_requests()
    return HTMLResponse(_render_page(pending, users, all_req, msg))


@router.post("/approve")
async def approve_user(request: Request, email: str = Form(...)):
    if not _require_admin(request):
        return RedirectResponse("/")
    approve_access_request(email.strip().lower())
    logger.info(f"Admin approved access for: {email}")
    return RedirectResponse("/admin?msg=approved", status_code=303)


@router.post("/deny")
async def deny_user(request: Request, email: str = Form(...)):
    if not _require_admin(request):
        return RedirectResponse("/")
    deny_access_request(email.strip().lower())
    logger.info(f"Admin denied access for: {email}")
    return RedirectResponse("/admin?msg=denied", status_code=303)


@router.post("/add-user")
async def add_user(request: Request, email: str = Form(...)):
    if not _require_admin(request):
        return RedirectResponse("/")
    add_email_to_whitelist(email.strip().lower())
    logger.info(f"Admin added user to whitelist: {email}")
    return RedirectResponse("/admin?msg=added", status_code=303)
