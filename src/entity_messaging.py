"""
Inter-entity messaging over the local network.

TCP for reliable message/file transfer; UDP broadcast for discovery.
Auth: shared secret (never sent on wire in plaintext); discovery uses HMAC.

Every message (including the first TCP ``auth`` frame) carries ``sender_type``:
``entity`` (Andrew/Nova/…) or ``human`` (owner, friends, web UI). Receivers
reject frames that omit or mis-declare ``sender_type`` so entity traffic is
never confused with human-originated traffic.

Environment (optional):
  ENTITY_SHARED_SECRET   — required for real use; demo defaults to "dev-local-secret"
  ENTITY_DISCOVERY_PORT  — UDP, default 38470
  ENTITY_TCP_PORT        — TCP listen port; 0 = pick ephemeral
  ENTITY_INBOX_DIR       — where received files land, default data/entity_inbox
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal

from config.settings import DATA_DIR, LOGS_DIR

DISCOVERY_PORT = int(os.environ.get("ENTITY_DISCOVERY_PORT", "38470"))
PROTOCOL_VERSION = 2

SENDER_ENTITY: Literal["entity"] = "entity"
SENDER_HUMAN: Literal["human"] = "human"
SenderType = Literal["entity", "human"]
MSG_SCHEMA = "entity_msg/2"
INBOX_DEFAULT = DATA_DIR / "entity_inbox"
LOG_PATH = LOGS_DIR / "entity_messaging.log"


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("entity_messaging")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    log.addHandler(fh)
    return log


_log = _setup_logger()


def _utc_ts() -> int:
    return int(time.time())


def discovery_hmac(
    secret: str, entity_id: str, entity_type: str, entity_name: str, tcp_port: int, ts: int
) -> str:
    msg = f"{PROTOCOL_VERSION}|{entity_id}|{entity_type}|{entity_name}|{tcp_port}|{ts}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_discovery_packet(secret: str, pkt: dict[str, Any]) -> bool:
    try:
        if int(pkt.get("v", 0)) != PROTOCOL_VERSION:
            return False
        if str(pkt.get("sender_type", "")).strip().lower() != SENDER_ENTITY:
            return False
        eid = str(pkt.get("entity_id", ""))
        etype = str(pkt.get("entity_type", ""))
        ename = str(pkt.get("entity_name", "")).strip()
        port = int(pkt.get("tcp_port", 0))
        ts = int(pkt.get("ts", 0))
        mac = str(pkt.get("hmac", ""))
        if not eid or not etype or not ename or port <= 0 or abs(_utc_ts() - ts) > 120:
            return False
        expect = discovery_hmac(secret, eid, etype, ename, port, ts)
        return hmac.compare_digest(expect, mac)
    except (TypeError, ValueError):
        return False


def _is_sender_type(v: Any) -> bool:
    s = str(v).strip().lower()
    return s in (SENDER_ENTITY, SENDER_HUMAN)


def _envelope_sender_error(env: dict[str, Any]) -> str | None:
    """Return an error string if envelope violates sender_type rules; else None."""
    if str(env.get("schema", "")) != MSG_SCHEMA:
        return "bad_schema"
    if not _is_sender_type(env.get("sender_type")):
        return "missing_or_invalid_sender_type"
    st = str(env.get("sender_type", "")).strip().lower()
    frm = env.get("from")
    if not isinstance(frm, dict):
        return "missing_from"
    fid = str(frm.get("id", "")).strip()
    ftype = str(frm.get("type", "")).strip()
    fname = str(frm.get("name", "")).strip()
    if not fid or not ftype or not fname:
        return "from_requires_id_type_name"
    if st == SENDER_ENTITY and ftype.lower() == "human":
        return "entity_sender_must_not_use_type_human"
    return None


def _auth_announce_error(auth: dict[str, Any]) -> str | None:
    """Validate first-frame auth + entity/human announcement (not the shared secret)."""
    if auth.get("op") != "auth":
        return "expected_auth"
    if not _is_sender_type(auth.get("sender_type")):
        return "missing_or_invalid_sender_type"
    eid = str(auth.get("entity_id", "")).strip()
    ename = str(auth.get("entity_name", "")).strip()
    if not eid or not ename:
        return "auth_requires_entity_id_and_entity_name"
    return None


def _secret_match(provided: str, expected: str) -> bool:
    """Compare secrets without leaking length via :func:`hmac.compare_digest` on raw strings."""
    dp = hashlib.sha256(provided.encode("utf-8")).digest()
    de = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(dp, de)


async def _read_exact_async(reader: asyncio.StreamReader, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            raise ConnectionError("EOF while reading frame")
        buf.extend(chunk)
    return bytes(buf)


async def _write_frame(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("JSON frame too large")
    writer.write(len(raw).to_bytes(4, "big") + raw)
    await writer.drain()


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    hdr = await _read_exact_async(reader, 4)
    n = int.from_bytes(hdr, "big")
    if n <= 0 or n > 16 * 1024 * 1024:
        raise ValueError("invalid frame length")
    body = await _read_exact_async(reader, n)
    return json.loads(body.decode("utf-8"))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class PeerInfo:
    host: str
    tcp_port: int
    entity_id: str
    entity_type: str
    entity_name: str = ""
    last_seen: float = field(default_factory=time.time)


OrchestrateHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, str | None]]
TextHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EntityMessaging:
    """
    One node: announces on UDP, accepts TCP connections, sends to peers.

    ``entity_type`` is an open string (andrew, nova, …) so new types need no core rewrite.
    """

    def __init__(
        self,
        *,
        entity_id: str | None = None,
        entity_name: str | None = None,
        entity_type: str = "andrew",
        shared_secret: str | None = None,
        tcp_port: int | None = None,
        inbox_dir: Path | None = None,
        on_text: TextHandler | None = None,
        on_orchestrate: OrchestrateHandler | None = None,
        discovery: bool = True,
    ):
        self.entity_id = entity_id or str(uuid.uuid4())
        self.entity_type = (entity_type or "unknown").strip().lower()
        self.entity_name = (entity_name or self.entity_type.capitalize() or "Entity").strip()
        self.shared_secret = (shared_secret or os.environ.get("ENTITY_SHARED_SECRET") or "dev-local-secret").strip()
        self.tcp_port_requested = int(tcp_port or int(os.environ.get("ENTITY_TCP_PORT", "0")))
        self.inbox_dir = Path(inbox_dir or os.environ.get("ENTITY_INBOX_DIR", str(INBOX_DEFAULT))).resolve()
        self.on_text = on_text
        self.on_orchestrate = on_orchestrate
        self.discovery = discovery
        self.peers: dict[str, PeerInfo] = {}
        self._server: asyncio.AbstractServer | None = None
        self._transport: asyncio.BaseTransport | None = None
        self._announce_task: asyncio.Task | None = None
        self._actual_tcp_port: int = 0

    def peer_list(self) -> list[PeerInfo]:
        return list(self.peers.values())

    async def start(self) -> int:
        """Start UDP discovery + TCP server. Returns bound TCP port."""
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_server(
            self._handle_client,
            host="0.0.0.0",
            port=self.tcp_port_requested,
        )
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("no server socket")
        self._actual_tcp_port = sockets[0].getsockname()[1]
        _log.info(
            "ENTITY_START | id=%s name=%s type=%s tcp=%s",
            self.entity_id,
            self.entity_name,
            self.entity_type,
            self._actual_tcp_port,
        )

        if self.discovery:
            loop = asyncio.get_running_loop()
            try:
                dsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    dsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except OSError:
                    pass
                dsock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                dsock.bind(("0.0.0.0", DISCOVERY_PORT))
                dsock.setblocking(False)
                self._transport, _ = await loop.create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self),
                    sock=dsock,
                )
            except OSError as e:
                _log.warning("ENTITY_DISCOVERY_BIND_FAIL | %s — continuing TCP-only", e)
                self._transport = None
            else:
                self._announce_task = asyncio.create_task(self._announce_loop())
        return self._actual_tcp_port

    async def stop(self) -> None:
        if self._announce_task:
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
            self._announce_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        _log.info("ENTITY_STOP | id=%s", self.entity_id)

    async def _announce_loop(self) -> None:
        assert self._transport is not None
        while True:
            try:
                ts = _utc_ts()
                pkt = {
                    "v": PROTOCOL_VERSION,
                    "sender_type": SENDER_ENTITY,
                    "entity_id": self.entity_id,
                    "entity_name": self.entity_name,
                    "entity_type": self.entity_type,
                    "tcp_port": self._actual_tcp_port,
                    "ts": ts,
                    "hmac": discovery_hmac(
                        self.shared_secret,
                        self.entity_id,
                        self.entity_type,
                        self.entity_name,
                        self._actual_tcp_port,
                        ts,
                    ),
                }
                raw = json.dumps(pkt, separators=(",", ":")).encode("utf-8")
                if len(raw) > 1300:
                    _log.warning("ENTITY_DISCOVERY | packet too large for UDP")
                else:
                    for dest in ("<broadcast>", "255.255.255.255"):
                        try:
                            self._transport.sendto(raw, (dest, DISCOVERY_PORT))
                            break
                        except OSError:
                            continue
                    _log.debug("ENTITY_DISCOVERY_OUT | %s", pkt["entity_id"])
            except Exception as e:
                _log.warning("ENTITY_DISCOVERY_ERR | %s", e)
            await asyncio.sleep(5.0)

    def _remember_peer(self, host: str, pkt: dict[str, Any]) -> None:
        eid = str(pkt.get("entity_id", ""))
        if eid == self.entity_id:
            return
        self.peers[eid] = PeerInfo(
            host=host,
            tcp_port=int(pkt["tcp_port"]),
            entity_id=eid,
            entity_type=str(pkt.get("entity_type", "")),
            entity_name=str(pkt.get("entity_name", "")).strip(),
        )
        _log.info(
            "ENTITY_PEER | %s @ %s:%s type=%s name=%s",
            eid,
            host,
            pkt["tcp_port"],
            pkt.get("entity_type"),
            pkt.get("entity_name"),
        )

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_s = f"{peer[0]}:{peer[1]}" if peer else "?"
        try:
            auth = await _read_frame(reader)
            ann_err = _auth_announce_error(auth)
            if ann_err:
                _log.warning("ENTITY_AUTH_ANNOUNCE_FAIL | peer=%s err=%s", peer_s, ann_err)
                await _write_frame(writer, {"ok": False, "error": ann_err})
                return
            if not _secret_match(str(auth.get("secret", "")), self.shared_secret):
                _log.warning("ENTITY_AUTH_FAIL | peer=%s", peer_s)
                await _write_frame(writer, {"ok": False, "error": "auth"})
                return
            await _write_frame(
                writer,
                {
                    "ok": True,
                    "entity_id": self.entity_id,
                    "entity_name": self.entity_name,
                    "entity_type": self.entity_type,
                    "sender_type": SENDER_ENTITY,
                },
            )
            peer_session_sender_type = str(auth.get("sender_type", "")).strip().lower()
            while True:
                env = await _read_frame(reader)
                env_err = _envelope_sender_error(env)
                if env_err:
                    _log.warning("ENTITY_ENVELOPE_REJECT | peer=%s err=%s", peer_s, env_err)
                    await _write_frame(writer, {"ok": False, "error": env_err})
                    continue
                env_st = str(env.get("sender_type", "")).strip().lower()
                if env_st != peer_session_sender_type:
                    _log.warning(
                        "ENTITY_SENDER_SESSION_MISMATCH | peer=%s auth=%s env=%s",
                        peer_s,
                        peer_session_sender_type,
                        env_st,
                    )
                    await _write_frame(writer, {"ok": False, "error": "sender_type_mismatch_session"})
                    continue
                await self._dispatch_envelope(reader, writer, env, peer_s)
        except (ConnectionError, asyncio.IncompleteReadError, json.JSONDecodeError, ValueError) as e:
            _log.debug("ENTITY_CLIENT_END | peer=%s err=%s", peer_s, e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch_envelope(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        env: dict[str, Any],
        peer_s: str,
    ) -> None:
        kind = str(env.get("kind", ""))
        st = str(env.get("sender_type", "")).strip().lower()
        _log.info(
            "ENTITY_IN | peer=%s sender_type=%s kind=%s id=%s",
            peer_s,
            st,
            kind,
            env.get("id", ""),
        )
        if kind == "text":
            if self.on_text:
                await self.on_text(env)
            await _write_frame(writer, {"ok": True, "received": "text"})
            return
        if kind == "orchestrate":
            cmd = str(env.get("cmd", ""))
            payload = env.get("payload") if isinstance(env.get("payload"), dict) else {}
            result = None
            if self.on_orchestrate:
                result = await self.on_orchestrate(cmd, payload)
            await _write_frame(writer, {"ok": True, "result": result})
            return
        if kind == "file_offer":
            meta = env.get("meta") or {}
            name = str(meta.get("filename", "incoming.bin"))
            size = int(meta.get("size", 0))
            sha_expect = str(meta.get("sha256", ""))
            if size < 0 or size > 512 * 1024 * 1024:
                await _write_frame(writer, {"ok": False, "error": "bad size"})
                return
            await _write_frame(writer, {"ok": True, "ready": True})
            data = await _read_exact_async(reader, size)
            digest = hashlib.sha256(data).hexdigest()
            if sha_expect and not hmac.compare_digest(digest, sha_expect):
                _log.warning("ENTITY_FILE_BAD_HASH | peer=%s", peer_s)
                await _write_frame(writer, {"ok": False, "error": "checksum"})
                return
            safe = Path(name).name
            dest = self.inbox_dir / f"{int(time.time())}_{safe}"
            dest.write_bytes(data)
            _log.info("ENTITY_FILE_IN | saved=%s bytes=%s", dest, size)
            await _write_frame(writer, {"ok": True, "path": str(dest), "sha256": digest})
            return
        await _write_frame(writer, {"ok": False, "error": f"unknown kind: {kind}"})


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, node: EntityMessaging):
        self.node = node

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if isinstance(addr, tuple) and len(addr) >= 2:
            host = str(addr[0])
        else:
            return
        try:
            pkt = json.loads(data.decode("utf-8"))
            if not isinstance(pkt, dict):
                return
            if not verify_discovery_packet(self.node.shared_secret, pkt):
                return
            self.node._remember_peer(host, pkt)
        except Exception as e:
            _log.debug("ENTITY_DISCOVERY_PARSE | %s", e)


async def send_auth_and_envelope(
    host: str,
    port: int,
    *,
    secret: str,
    auth_entity_id: str,
    auth_entity_name: str,
    auth_sender_type: SenderType,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Connect, authenticate with announcement, send one envelope; read one JSON response."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        await _write_frame(
            writer,
            {
                "op": "auth",
                "secret": secret,
                "entity_id": auth_entity_id.strip(),
                "entity_name": auth_entity_name.strip(),
                "sender_type": auth_sender_type,
            },
        )
        ack = await _read_frame(reader)
        if not ack.get("ok"):
            raise RuntimeError(ack.get("error", "auth failed"))
        if str(ack.get("sender_type", "")).strip().lower() != SENDER_ENTITY:
            raise RuntimeError("peer_ack_missing_entity_sender_type")
        await _write_frame(writer, envelope)
        resp = await _read_frame(reader)
        _log.info("ENTITY_SEND_REPLY | host=%s port=%s ok=%s", host, port, resp.get("ok"))
        return resp
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def send_text(
    host: str,
    port: int,
    *,
    secret: str,
    sender_type: SenderType,
    from_id: str,
    from_type: str,
    from_name: str,
    text: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    env = {
        "schema": MSG_SCHEMA,
        "sender_type": sender_type,
        "kind": "text",
        "id": message_id or str(uuid.uuid4()),
        "from": {"id": from_id, "type": from_type, "name": from_name},
        "body": {"text": text},
    }
    return await send_auth_and_envelope(
        host,
        port,
        secret=secret,
        auth_entity_id=from_id,
        auth_entity_name=from_name,
        auth_sender_type=sender_type,
        envelope=env,
    )


async def send_file(
    host: str,
    port: int,
    *,
    secret: str,
    sender_type: SenderType,
    from_id: str,
    from_type: str,
    from_name: str,
    file_path: Path,
    message_id: str | None = None,
) -> dict[str, Any]:
    path = Path(file_path).resolve()
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    env = {
        "schema": MSG_SCHEMA,
        "sender_type": sender_type,
        "kind": "file_offer",
        "id": message_id or str(uuid.uuid4()),
        "from": {"id": from_id, "type": from_type, "name": from_name},
        "meta": {
            "filename": path.name,
            "size": len(data),
            "sha256": sha,
        },
    }
    reader, writer = await asyncio.open_connection(host, port)
    try:
        await _write_frame(
            writer,
            {
                "op": "auth",
                "secret": secret,
                "entity_id": from_id.strip(),
                "entity_name": from_name.strip(),
                "sender_type": sender_type,
            },
        )
        ack = await _read_frame(reader)
        if not ack.get("ok"):
            raise RuntimeError(ack.get("error", "auth failed"))
        if str(ack.get("sender_type", "")).strip().lower() != SENDER_ENTITY:
            raise RuntimeError("peer_ack_missing_entity_sender_type")
        await _write_frame(writer, env)
        ready = await _read_frame(reader)
        if not ready.get("ok"):
            raise RuntimeError(ready.get("error", "offer rejected"))
        writer.write(data)
        await writer.drain()
        resp = await _read_frame(reader)
        _log.info("ENTITY_FILE_OUT | ok=%s", resp.get("ok"))
        return resp
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def send_orchestrate(
    host: str,
    port: int,
    *,
    secret: str,
    sender_type: SenderType,
    from_id: str,
    from_type: str,
    from_name: str,
    cmd: str,
    payload: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    env = {
        "schema": MSG_SCHEMA,
        "sender_type": sender_type,
        "kind": "orchestrate",
        "id": message_id or str(uuid.uuid4()),
        "from": {"id": from_id, "type": from_type, "name": from_name},
        "cmd": cmd,
        "payload": payload or {},
    }
    return await send_auth_and_envelope(
        host,
        port,
        secret=secret,
        auth_entity_id=from_id,
        auth_entity_name=from_name,
        auth_sender_type=sender_type,
        envelope=env,
    )
