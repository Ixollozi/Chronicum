"""Living client thread: card + messages + summary + agent context (§9).

Trust contract:
- resolve never guesses between people (ambiguous -> candidates)
- every message knows speaker / event time / source channel
- summary states its coverage; staleness is computed, not hidden
- owner canon facts outrank LLM summary text
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import with_llm_fallback
from app.models import Client, ClientMessage, ClientSummaryVersion

BODY_CAP = 2000


def _norm(s: str) -> str:
    t = unicodedata.normalize("NFKC", (s or "").strip().lower()).replace("ё", "е")
    return re.sub(r"\s+", " ", t)


def _alias_list(client: Client) -> list[str]:
    raw = client.aliases or []
    out = [str(x).strip() for x in raw if str(x).strip()]
    if client.display_name and client.display_name.strip() not in out:
        out.insert(0, client.display_name.strip())
    return out


def _contacts_blob(client: Client) -> str:
    c = client.contacts or {}
    return " ".join(str(v) for v in c.values() if v)


def _candidate(c: Client, matched_on: str) -> dict:
    return {
        "id": c.id,
        "display_name": c.display_name,
        "aliases": c.aliases or [],
        "context_line": c.context_line,
        "message_count": len(c.messages or []),
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "matched_on": matched_on,
    }


class DuplicateClientError(ValueError):
    def __init__(self, candidates: list[dict]):
        super().__init__("possible duplicate client")
        self.candidates = candidates


class AmbiguousClientError(ValueError):
    def __init__(self, candidates: list[dict]):
        super().__init__("ambiguous client — уточните, кто именно")
        self.candidates = candidates


async def find_candidates(
    session: AsyncSession,
    query: str,
    *,
    user_id: str | None = None,
    scope_user: bool = False,
) -> tuple[list[Client], list[Client], str]:
    """Return (exact, partial, matched_on) without choosing."""
    q = (query or "").strip()
    needle = _norm(q)
    if not needle:
        return [], [], ""
    stmt = select(Client)
    if scope_user:
        if user_id:
            stmt = stmt.where(Client.user_id == user_id)
        else:
            stmt = stmt.where(Client.user_id.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    exact: list[Client] = []
    partial: list[Client] = []
    for c in rows:
        aliases = [_norm(a) for a in _alias_list(c)]
        contacts = _norm(_contacts_blob(c))
        if needle in aliases or needle == _norm(c.display_name or ""):
            exact.append(c)
        elif needle and needle in contacts:
            exact.append(c)
        elif len(needle) >= 3 and any(needle in a for a in aliases if a):
            partial.append(c)
    return exact, partial, "name/alias/contact"


async def resolve_client_v2(
    session: AsyncSession,
    query_or_id: str,
    *,
    user_id: str | None = None,
    scope_user: bool = False,
) -> dict:
    q = (query_or_id or "").strip()
    if not q:
        return {"status": "not_found", "client": None, "candidates": []}
    by_id = await get_client(session, q)
    if by_id and scope_user and user_id and by_id.user_id != user_id:
        by_id = None
    if by_id and scope_user and user_id is None and by_id.user_id is not None:
        # admin path uses scope_user=False; non-admin with no id sees nothing
        by_id = None
    if by_id:
        return {"status": "exact", "client": by_id, "candidates": []}
    exact, partial, _ = await find_candidates(session, q, user_id=user_id, scope_user=scope_user)
    pool = exact or partial
    if len(pool) == 1:
        client = await get_client(session, pool[0].id)
        return {"status": "exact", "client": client, "candidates": []}
    if len(pool) > 1:
        loaded = []
        for c in pool:
            full = await get_client(session, c.id)
            if full:
                loaded.append(_candidate(full, "exact" if exact else "partial"))
        return {"status": "ambiguous", "client": None, "candidates": loaded}
    return {"status": "not_found", "client": None, "candidates": []}


async def resolve_client(session: AsyncSession, query_or_id: str) -> Client | None:
    """Compat resolver: returns None when not found, raises on ambiguity."""
    res = await resolve_client_v2(session, query_or_id)
    if res["status"] == "ambiguous":
        raise AmbiguousClientError(res["candidates"])
    return res["client"]


async def create_client(
    session: AsyncSession,
    *,
    display_name: str,
    aliases: list[str] | None = None,
    contacts: dict | None = None,
    context_line: str | None = None,
    canon_facts: str | None = None,
    force: bool = False,
    user_id: str | None = None,
) -> Client:
    name = (display_name or "").strip()
    if not name:
        raise ValueError("display_name required")
    if not force:
        probes = [name, *[a for a in (aliases or []) if a]]
        seen: dict[str, dict] = {}
        for probe in probes:
            exact, _, _ = await find_candidates(
                session, str(probe), user_id=user_id, scope_user=True
            )
            for c in exact:
                full = await get_client(session, c.id)
                if full:
                    seen[c.id] = _candidate(full, f"probe:{probe}")
        contact_blob = " ".join(str(v) for v in (contacts or {}).values() if v)
        if contact_blob:
            exact, _, _ = await find_candidates(
                session, contact_blob, user_id=user_id, scope_user=True
            )
            for c in exact:
                full = await get_client(session, c.id)
                if full:
                    seen[c.id] = _candidate(full, "contact")
        if seen:
            raise DuplicateClientError(list(seen.values()))
    c = Client(
        id=str(uuid4()),
        user_id=user_id,
        display_name=name,
        aliases=[a.strip() for a in (aliases or []) if a and str(a).strip()],
        contacts=contacts or {},
        context_line=(context_line or "").strip() or None,
        canon_facts=(canon_facts or "").strip() or None,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


async def list_clients(
    session: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    user_id: str | None = None,
    scope_user: bool = False,
) -> list[Client]:
    stmt = select(Client).order_by(Client.updated_at.desc())
    if scope_user:
        if user_id:
            stmt = stmt.where(Client.user_id == user_id)
        else:
            stmt = stmt.where(Client.user_id.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    if q and q.strip():
        needle = _norm(q)
        rows = [
            c
            for c in rows
            if needle in _norm(" ".join([c.display_name or "", " ".join(_alias_list(c)), c.context_line or "", _contacts_blob(c)]))
            or needle in _norm(c.id)
        ]
    return list(rows)[: min(limit, 100)]


async def get_client(session: AsyncSession, client_id: str) -> Client | None:
    return (
        await session.execute(
            select(Client)
            .options(selectinload(Client.messages))
            .where(Client.id == client_id)
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def update_client(
    session: AsyncSession,
    client_id: str,
    *,
    display_name: str | None = None,
    aliases: list[str] | None = None,
    contacts: dict | None = None,
    context_line: str | None = None,
    canon_facts: str | None = None,
) -> Client:
    client = await get_client(session, client_id)
    if not client:
        raise ValueError("client not found")
    if display_name is not None and display_name.strip():
        client.display_name = display_name.strip()
    if aliases is not None:
        client.aliases = [a.strip() for a in aliases if a and str(a).strip()]
    if contacts is not None:
        client.contacts = contacts
    if context_line is not None:
        client.context_line = context_line.strip() or None
    if canon_facts is not None:
        client.canon_facts = canon_facts.strip() or None
    client.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return await get_client(session, client_id)  # type: ignore[return-value]


async def delete_client(session: AsyncSession, client_id: str) -> bool:
    client = await get_client(session, client_id)
    if not client:
        return False
    await session.delete(client)
    await session.commit()
    return True


async def merge_clients(session: AsyncSession, from_id: str, into_id: str) -> Client:
    src = await get_client(session, from_id)
    dst = await get_client(session, into_id)
    if not src or not dst or from_id == into_id:
        raise ValueError("merge requires two different existing clients")
    for m in list(src.messages or []):
        m.client_id = dst.id
    aliases = list(dst.aliases or [])
    for a in [src.display_name, *(src.aliases or [])]:
        if a and a not in aliases and a != dst.display_name:
            aliases.append(a)
    dst.aliases = aliases
    if not dst.context_line and src.context_line:
        dst.context_line = src.context_line
    dst.updated_at = datetime.now(timezone.utc)
    await session.flush()
    # Core delete avoids ORM delete-orphan cascade eating moved messages
    from sqlalchemy import delete as sa_delete

    await session.execute(sa_delete(Client).where(Client.id == src.id))
    await session.commit()
    return await get_client(session, into_id)  # type: ignore[return-value]


def _hash(body: str) -> str:
    return hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()


async def append_message(
    session: AsyncSession,
    client_id: str,
    body: str,
    *,
    source: str = "web",
    speaker: str = "unknown",
    speaker_name: str | None = None,
    kind: str = "text",
    event_at: datetime | None = None,
    refresh_summary: bool = False,
) -> tuple[Client, ClientMessage]:
    text = (body or "").strip()
    if not text:
        raise ValueError("empty message")
    client = await get_client(session, client_id)
    if not client:
        raise ValueError("client not found")
    msg = ClientMessage(
        id=str(uuid4()),
        client_id=client.id,
        body=text,
        source=(source or "web")[:20],
        speaker=(speaker or "unknown")[:20],
        speaker_name=(speaker_name or None),
        kind=(kind or "text")[:20],
        event_at=event_at,
        content_hash=_hash(text),
    )
    session.add(msg)
    client.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(msg)
    client = await get_client(session, client_id)
    assert client
    if refresh_summary:
        client = await refresh_client_summary(session, client.id)
    return client, msg


async def update_message(session: AsyncSession, client_id: str, message_id: str, body: str) -> ClientMessage:
    msg = (
        await session.execute(
            select(ClientMessage).where(ClientMessage.id == message_id, ClientMessage.client_id == client_id)
        )
    ).scalars().first()
    if not msg:
        raise ValueError("message not found")
    msg.body = (body or "").strip()
    msg.content_hash = _hash(msg.body)
    await session.commit()
    await session.refresh(msg)
    return msg


async def delete_message(session: AsyncSession, client_id: str, message_id: str) -> bool:
    msg = (
        await session.execute(
            select(ClientMessage).where(ClientMessage.id == message_id, ClientMessage.client_id == client_id)
        )
    ).scalars().first()
    if not msg:
        return False
    await session.delete(msg)
    await session.commit()
    return True


async def search_messages(session: AsyncSession, client_id: str, q: str, *, limit: int = 8) -> list[ClientMessage]:
    needle = (q or "").strip()
    if not needle:
        return []
    rows = (
        await session.execute(
            select(ClientMessage)
            .where(ClientMessage.client_id == client_id)
            .where(ClientMessage.body.ilike(f"%{needle}%"))
            .order_by(ClientMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


def _snippet(body: str, needle: str = "", cap: int = 400) -> str:
    text = body or ""
    if len(text) <= cap:
        return text
    if needle:
        i = text.lower().find(needle.lower())
        if i >= 0:
            start = max(0, i - 120)
            return ("…" if start else "") + text[start : start + cap] + "…"
    return text[:cap] + "…"


# ---------------------------------------------------------------- paste import

_WA_RE = re.compile(
    r"^\[(?P<d>\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+(?P<t>\d{1,2}:\d{2}(?::\d{2})?)\]\s+(?P<who>[^:]+):\s*(?P<body>.*)$"
)
_TG_RE = re.compile(
    r"^(?P<who>.+?),\s*\[(?P<d>\d{1,2}[./]\d{1,2}[./]\d{2,4})\s+(?P<t>\d{1,2}:\d{2})\]\s*$"
)
_NAME_RE = re.compile(r"^(?P<who>[A-ZА-ЯЁ][^:\n]{1,60}):\s+(?P<body>.+)$")


def _parse_dt(d: str, t: str) -> datetime | None:
    d = d.replace("/", ".")
    parts = d.split(".")
    try:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        if year < 100:
            year += 2000
        hh, mm = (t.split(":") + ["0"])[:2]
        return datetime(year, month, day, int(hh), int(mm), tzinfo=timezone.utc)
    except Exception:
        return None


def parse_paste(text: str) -> list[dict]:
    """Split a pasted WhatsApp/TG transcript into speaker messages (no DB)."""
    out: list[dict] = []
    pending_who: str | None = None
    pending_at: datetime | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _WA_RE.match(line.strip())
        if m:
            out.append(
                {
                    "speaker_name": m.group("who").strip(),
                    "body": m.group("body").strip(),
                    "event_at": _parse_dt(m.group("d"), m.group("t")),
                }
            )
            pending_who = None
            continue
        m = _TG_RE.match(line.strip())
        if m:
            pending_who = m.group("who").strip()
            pending_at = _parse_dt(m.group("d"), m.group("t"))
            continue
        if pending_who:
            out.append({"speaker_name": pending_who, "body": line.strip(), "event_at": pending_at})
            pending_who = None
            continue
        m = _NAME_RE.match(line.strip())
        if m:
            out.append({"speaker_name": m.group("who").strip(), "body": m.group("body").strip(), "event_at": None})
            continue
        if out:
            out[-1]["body"] = (out[-1]["body"] + "\n" + line.strip()).strip()
        else:
            out.append({"speaker_name": None, "body": line.strip(), "event_at": None})
    return [m for m in out if m["body"]]


def paste_preview(text: str) -> dict:
    msgs = parse_paste(text)
    speakers = sorted({m["speaker_name"] for m in msgs if m.get("speaker_name")})
    dates = [m["event_at"] for m in msgs if m.get("event_at")]
    return {
        "count": len(msgs),
        "speakers": speakers,
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "messages": msgs,
    }


async def commit_paste(
    session: AsyncSession,
    client_id: str,
    text: str,
    *,
    speaker_roles: dict[str, str] | None = None,
    source: str = "web",
) -> tuple[Client, int, int]:
    """Commit parsed paste; returns (client, added, skipped_duplicates)."""
    client = await get_client(session, client_id)
    if not client:
        raise ValueError("client not found")
    roles = speaker_roles or {}
    existing = {m.content_hash for m in (client.messages or []) if m.content_hash}
    added = skipped = 0
    for item in parse_paste(text):
        h = _hash(item["body"])
        if h in existing:
            skipped += 1
            continue
        existing.add(h)
        name = item.get("speaker_name")
        role = roles.get(name or "", "unknown")
        if name and _norm(name) == _norm(client.display_name):
            role = roles.get(name, "client")
        session.add(
            ClientMessage(
                id=str(uuid4()),
                client_id=client.id,
                body=item["body"],
                source=(source or "web")[:20],
                speaker=role,
                speaker_name=name,
                kind="transcript",
                event_at=item.get("event_at"),
                content_hash=h,
            )
        )
        added += 1
    client.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return await get_client(session, client_id), added, skipped  # type: ignore[return-value]


# ---------------------------------------------------------------- summary

def summary_staleness(client: Client) -> dict:
    msgs = list(client.messages or [])
    total = len(msgs)
    if not client.summary_up_to_message_id:
        since = total if client.summary else 0
        covered = 0
    else:
        idx = next((i for i, m in enumerate(msgs) if m.id == client.summary_up_to_message_id), -1)
        covered = idx + 1
        since = total - covered
    return {
        "summary_stale": bool(client.summary) and since > 0 or (not client.summary and total > 0),
        "messages_since_summary": since,
        "messages_covered": covered,
        "message_count": total,
    }


async def refresh_client_summary(session: AsyncSession, client_id: str) -> Client:
    client = await get_client(session, client_id)
    if not client:
        raise ValueError("client not found")
    msgs = list(client.messages or [])
    last = msgs[-80:]
    # Chunk long threads: fold in segments so one huge paste cannot blow context
    chunks: list[list[ClientMessage]] = []
    buf: list[ClientMessage] = []
    size = 0
    for m in last:
        buf.append(m)
        size += len(m.body or "")
        if size > 12000:
            chunks.append(buf)
            buf, size = [], 0
    if buf or not chunks:
        chunks.append(buf)

    summary_text = client.summary or ""
    for chunk in chunks:
        lines = []
        for m in chunk:
            who = m.speaker_name or m.speaker or "unknown"
            when = (m.event_at or m.created_at).isoformat() if (m.event_at or m.created_at) else ""
            lines.append(f"[msg:{m.id[:8]}] [{when}] ({who}/{m.speaker}) {m.body[:2000]}")
        transcript = "\n".join(lines)
        system = (
            "You maintain a living client-memory summary for a personal CRM-lite (not a sales funnel). "
            "Respond in the language of the messages (usually Russian). "
            "Always refer to the client by display name AND id in the first line. "
            "Do NOT invent facts. Owner canon facts below are highest priority and must be preserved verbatim in substance. "
            "Attribute statements only when speaker is known (client/me with a name); "
            "unknown-speaker material goes to Open questions, never to Agreements. "
            "Cite message ids like [msg:xxxxxxxx] after key facts. Structure:\n"
            f"Клиент: {client.display_name} (id={client.id})\n"
            "Контекст: …\n"
            "Хочет: …\n"
            "Договорённости: …\n"
            "Возражения / риски: …\n"
            "Следующий шаг: …\n"
            "Открытые вопросы: …\n"
            "Plain text, compact bullets OK."
        )
        user = (
            f"Context line: {client.context_line or '—'}\n"
            f"Owner canon facts (do not rewrite away):\n{client.canon_facts or '(none)'}\n\n"
            f"Previous summary:\n{summary_text or '(none)'}\n\n"
            f"Messages:\n{transcript or '(no messages yet)'}"
        )

        async def _run(llm, _s=system, _u=user):
            return await llm.complete_text(_s, _u)

        result = await with_llm_fallback(session, _run)
        summary_text = (result or "").strip()

    covered_id = last[-1].id if last else client.summary_up_to_message_id
    client.summary = summary_text
    client.summary_version = int(client.summary_version or 0) + 1
    client.summary_updated_at = datetime.now(timezone.utc)
    client.summary_up_to_message_id = covered_id
    client.updated_at = client.summary_updated_at
    session.add(
        ClientSummaryVersion(
            client_id=client.id,
            version=client.summary_version,
            text=summary_text,
            covered_up_to_message_id=covered_id,
        )
    )
    await session.commit()
    await session.refresh(client)
    return client


async def client_context_pack(
    session: AsyncSession,
    query_or_id: str,
    *,
    n: int = 20,
    search_q: str | None = None,
    user_id: str | None = None,
    scope_user: bool = False,
) -> dict:
    res = await resolve_client_v2(
        session, query_or_id, user_id=user_id, scope_user=scope_user
    )
    if res["status"] == "ambiguous":
        raise AmbiguousClientError(res["candidates"])
    client = res["client"]
    if not client:
        raise ValueError(f"client not found for {query_or_id!r}")
    msgs = list(client.messages or [])
    last = msgs[-max(1, min(n, 50)) :]
    hits: list[ClientMessage] = []
    if search_q:
        hits = await search_messages(session, client.id, search_q, limit=6)
    stale = summary_staleness(client)

    linked: list[dict] = []
    try:
        from app.models import Material, MaterialClientLink

        rows = (
            await session.execute(
                select(Material)
                .join(MaterialClientLink, MaterialClientLink.material_id == Material.id)
                .where(MaterialClientLink.client_id == client.id)
                .limit(10)
            )
        ).scalars().all()
        linked = [
            {"id": m.id, "title": m.title, "summary": (m.summary or "")[:400], "usefulness_score": m.usefulness_score}
            for m in rows
        ]
    except Exception:
        linked = []

    return {
        "client": {
            "id": client.id,
            "display_name": client.display_name,
            "aliases": client.aliases or [],
            "contacts": client.contacts or {},
            "context_line": client.context_line,
            "canon_facts": client.canon_facts,
            "summary": client.summary,
            "summary_version": client.summary_version,
            "summary_updated_at": client.summary_updated_at,
            "summary_up_to_message_id": client.summary_up_to_message_id,
            **stale,
        },
        "recent_messages": [
            {
                "id": m.id,
                "body": _snippet(m.body, cap=BODY_CAP),
                "truncated": len(m.body or "") > BODY_CAP,
                "source": m.source,
                "speaker": m.speaker,
                "speaker_name": m.speaker_name,
                "kind": m.kind,
                "event_at": m.event_at,
                "created_at": m.created_at,
            }
            for m in last
        ],
        "search_hits": [
            {"id": m.id, "body": _snippet(m.body, search_q or ""), "created_at": m.created_at} for m in hits
        ],
        "linked_materials": linked,
    }
