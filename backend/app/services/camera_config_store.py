from __future__ import annotations

import threading
from typing import Any
from urllib.parse import quote, unquote, urlparse

from app.db.pool import db_pool


class CameraConfigStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._source_path = "db://cameras"

    @property
    def source_path(self) -> str:
        return self._source_path

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _to_int(value: Any, fallback: int) -> int:
        try:
            num = int(value)
        except Exception:
            return fallback
        return num

    @staticmethod
    def _parse_channel_id_from_rtsp(rtsp_url: str) -> int | None:
        try:
            parsed = urlparse(rtsp_url)
        except Exception:
            return None
        path = (parsed.path or "").strip()
        parts = [p for p in path.split("/") if p]
        if parts and parts[-1].isdigit():
            raw = int(parts[-1])
            if raw >= 100:
                return max(1, raw // 100)
            return max(1, raw)
        return None

    @staticmethod
    def _build_rtsp_url(host: str, username: str, password: str, channel_id: int) -> str:
        if not host or not username or not password:
            return ""
        channel_no = max(1, channel_id) * 100 + 1
        return (
            f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{host}:554/Streaming/Channels/{channel_no}"
        )

    @staticmethod
    def _parse_url_parts(url: str) -> dict[str, Any]:
        text = (url or "").strip()
        if not text:
            return {}
        try:
            parsed = urlparse(text)
        except Exception:
            return {}
        out: dict[str, Any] = {}
        if parsed.hostname:
            out["host"] = str(parsed.hostname).strip()
        if parsed.port:
            out["port"] = int(parsed.port)
        if parsed.scheme:
            out["scheme"] = str(parsed.scheme).strip()
        if parsed.username is not None:
            out["username"] = unquote(parsed.username).strip()
        if parsed.password is not None:
            out["password"] = unquote(parsed.password)
        return out

    @staticmethod
    def _columns(cur: Any) -> set[str]:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'cameras'
            """
        )
        return {str(row[0]).strip() for row in cur.fetchall()}

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            pool = None
            conn = None
            try:
                pool = db_pool.get_pool()
                conn = pool.getconn()
                with conn.cursor() as cur:
                    cols = self._columns(cur)
                    has_id = "id" in cols
                    has_legacy_id = "camera_id" in cols
                    if not has_id and not has_legacy_id:
                        return []
                    if has_id and has_legacy_id:
                        id_expr = "COALESCE(NULLIF(id::text, ''), camera_id::text)"
                        id_order = "COALESCE(NULLIF(id::text, ''), camera_id::text)"
                    elif has_id:
                        id_expr = "id::text"
                        id_order = "id::text"
                    else:
                        id_expr = "camera_id::text"
                        id_order = "camera_id::text"

                    has_name = "name" in cols
                    has_legacy_name = "camera_name" in cols
                    if has_name and has_legacy_name:
                        name_expr = f"COALESCE(NULLIF(name::text, ''), camera_name::text, {id_expr})"
                    elif has_name:
                        name_expr = f"COALESCE(NULLIF(name::text, ''), {id_expr})"
                    elif has_legacy_name:
                        name_expr = f"COALESCE(NULLIF(camera_name::text, ''), {id_expr})"
                    else:
                        name_expr = id_expr
                    rtsp_expr = "COALESCE(rtsp_url, '')" if "rtsp_url" in cols else "''"
                    event_expr = "COALESCE(event_api_url, '')" if "event_api_url" in cols else "''"
                    snapshot_expr = "COALESCE(snapshot_api_url, '')" if "snapshot_api_url" in cols else "''"
                    host_expr = "COALESCE(host, '')" if "host" in cols else "''"
                    port_expr = "COALESCE(port, 80)" if "port" in cols else "80"
                    scheme_expr = "COALESCE(scheme, 'http')" if "scheme" in cols else "'http'"
                    user_expr = "COALESCE(username, '')" if "username" in cols else "''"
                    pass_expr = "COALESCE(password, '')" if "password" in cols else "''"
                    channel_expr = "COALESCE(channel_id, 1)" if "channel_id" in cols else "1"
                    enabled_expr = "COALESCE(enabled, TRUE)" if "enabled" in cols else "TRUE"
                    vendor_expr = "COALESCE(vendor, 'custom')" if "vendor" in cols else "'custom'"
                    order_expr = "sort_order" if "sort_order" in cols else id_order
                    cur.execute(
                        f"""
                        SELECT
                            {id_expr} AS camera_id_value,
                            {name_expr} AS camera_name_value,
                            {rtsp_expr},
                            {event_expr},
                            {snapshot_expr},
                            {host_expr},
                            {port_expr},
                            {scheme_expr},
                            {user_expr},
                            {pass_expr},
                            {channel_expr},
                            {enabled_expr},
                            {vendor_expr}
                        FROM cameras
                        ORDER BY {order_expr} ASC, {id_order} ASC
                        """
                    )
                    rows = cur.fetchall()
                items: list[dict[str, Any]] = []
                for row in rows:
                    camera_id = str(row[0] or "").strip()
                    if not camera_id:
                        continue
                    name = str(row[1] or camera_id).strip() or camera_id
                    rtsp_url = str(row[2] or "").strip()
                    event_api_url = str(row[3] or "").strip()
                    snapshot_api_url = str(row[4] or "").strip()
                    host = str(row[5] or "").strip()
                    port = max(1, self._to_int(row[6], 80))
                    scheme = str(row[7] or "http").strip() or "http"
                    username = str(row[8] or "").strip()
                    password = "" if row[9] is None else str(row[9])
                    parsed = urlparse(rtsp_url) if rtsp_url else None
                    event_parts = self._parse_url_parts(event_api_url)
                    snapshot_parts = self._parse_url_parts(snapshot_api_url)
                    if not host:
                        host = str(event_parts.get("host") or snapshot_parts.get("host") or "").strip()
                    if not username:
                        username = str(event_parts.get("username") or snapshot_parts.get("username") or "").strip()
                    if not password:
                        password = "" if (event_parts.get("password") or snapshot_parts.get("password")) is None else str(
                            event_parts.get("password") or snapshot_parts.get("password") or ""
                        )
                    if port <= 0:
                        port = int(event_parts.get("port") or snapshot_parts.get("port") or 80)
                    if not scheme:
                        scheme = str(event_parts.get("scheme") or snapshot_parts.get("scheme") or "http").strip() or "http"
                    if not host and parsed and parsed.hostname:
                        host = str(parsed.hostname).strip()
                    if parsed and parsed.port:
                        port = int(parsed.port)
                    if not username and parsed and parsed.username is not None:
                        username = unquote(parsed.username).strip()
                    if not password and parsed and parsed.password is not None:
                        password = unquote(parsed.password)
                    channel_id = max(
                        1,
                        self._to_int(
                            row[10],
                            self._parse_channel_id_from_rtsp(rtsp_url) or 1,
                        ),
                    )
                    vendor = str(row[12] or "custom").strip() or "custom"
                    if not rtsp_url:
                        rtsp_url = self._build_rtsp_url(host, username, password, channel_id)
                    items.append(
                        {
                            "id": camera_id,
                            "name": name,
                            "vendor": vendor,
                            "rtsp_url": rtsp_url,
                            "event_api_url": event_api_url,
                            "snapshot_api_url": snapshot_api_url,
                            "host": host,
                            "port": port,
                            "scheme": scheme,
                            "username": username,
                            "password": password,
                            "channel_id": channel_id,
                            "enabled": bool(row[11]),
                        }
                    )
                return items
            finally:
                if pool is not None and conn is not None:
                    pool.putconn(conn)

    def save(self, cameras: list[dict[str, Any]]) -> None:
        with self._lock:
            pool = None
            conn = None
            try:
                pool = db_pool.get_pool()
                conn = pool.getconn()
                with conn.cursor() as cur:
                    cols = self._columns(cur)
                    if not cols:
                        return
                    has_id = "id" in cols
                    has_legacy_id = "camera_id" in cols
                    if not has_id and not has_legacy_id:
                        return
                    has_name = "name" in cols
                    has_legacy_name = "camera_name" in cols
                    has_rtsp = "rtsp_url" in cols
                    has_event = "event_api_url" in cols
                    has_snapshot = "snapshot_api_url" in cols
                    has_host = "host" in cols
                    has_port = "port" in cols
                    has_scheme = "scheme" in cols
                    has_username = "username" in cols
                    has_password = "password" in cols
                    has_channel = "channel_id" in cols
                    has_enabled = "enabled" in cols
                    has_vendor = "vendor" in cols
                    has_sort = "sort_order" in cols
                    has_updated = "updated_at" in cols
                    cur.execute("DELETE FROM cameras")
                    for index, camera in enumerate(cameras):
                        camera_id = str(camera.get("id", "")).strip()
                        if not camera_id:
                            continue
                        camera_name = str(camera.get("name", camera_id)).strip() or camera_id
                        raw_rtsp = str(camera.get("rtsp_url", "")).strip()
                        event_api_url = str(camera.get("event_api_url", "")).strip()
                        snapshot_api_url = str(camera.get("snapshot_api_url", "")).strip()
                        host = str(camera.get("host", "")).strip()
                        scheme = str(camera.get("scheme", "http")).strip() or "http"
                        vendor = str(camera.get("vendor", "custom")).strip() or "custom"
                        username = str(camera.get("username", "")).strip()
                        password = "" if camera.get("password") is None else str(camera.get("password", ""))
                        parsed = urlparse(raw_rtsp) if raw_rtsp else None
                        event_parts = self._parse_url_parts(event_api_url)
                        snapshot_parts = self._parse_url_parts(snapshot_api_url)
                        if not host:
                            host = str(event_parts.get("host") or snapshot_parts.get("host") or "").strip()
                        if not username:
                            username = str(event_parts.get("username") or snapshot_parts.get("username") or "").strip()
                        if not password:
                            password = "" if (event_parts.get("password") or snapshot_parts.get("password")) is None else str(
                                event_parts.get("password") or snapshot_parts.get("password") or ""
                            )
                        if not host and parsed and parsed.hostname:
                            host = str(parsed.hostname).strip()
                        if not username and parsed and parsed.username is not None:
                            username = unquote(parsed.username).strip()
                        if not password and parsed and parsed.password is not None:
                            password = unquote(parsed.password)
                        port = self._to_int(camera.get("port"), 80)
                        if port <= 0:
                            port = int(event_parts.get("port") or snapshot_parts.get("port") or 80)
                        channel_id = self._to_int(
                            camera.get("channel_id"),
                            self._parse_channel_id_from_rtsp(raw_rtsp) or 1,
                        )
                        channel_id = max(1, channel_id)
                        rtsp_url = raw_rtsp or self._build_rtsp_url(host, username, password, channel_id)
                        if not rtsp_url and not host:
                            continue
                        enabled = self._to_bool(camera.get("enabled", True))
                        insert_cols: list[str] = []
                        values: list[Any] = []
                        if has_id:
                            insert_cols.append("id")
                            values.append(camera_id)
                        if has_legacy_id:
                            insert_cols.append("camera_id")
                            values.append(camera_id)
                        if has_name:
                            insert_cols.append("name")
                            values.append(camera_name)
                        if has_legacy_name:
                            insert_cols.append("camera_name")
                            values.append(camera_name)
                        if has_rtsp:
                            insert_cols.append("rtsp_url")
                            values.append(rtsp_url)
                        if has_event:
                            insert_cols.append("event_api_url")
                            values.append(event_api_url)
                        if has_snapshot:
                            insert_cols.append("snapshot_api_url")
                            values.append(snapshot_api_url)
                        if has_host:
                            insert_cols.append("host")
                            values.append(host)
                        if has_port:
                            insert_cols.append("port")
                            values.append(port)
                        if has_scheme:
                            insert_cols.append("scheme")
                            values.append(scheme)
                        if has_username:
                            insert_cols.append("username")
                            values.append(username)
                        if has_password:
                            insert_cols.append("password")
                            values.append(password)
                        if has_channel:
                            insert_cols.append("channel_id")
                            values.append(channel_id)
                        if has_enabled:
                            insert_cols.append("enabled")
                            values.append(bool(enabled))
                        if has_vendor:
                            insert_cols.append("vendor")
                            values.append(vendor)
                        if has_sort:
                            insert_cols.append("sort_order")
                            values.append(index)
                        if has_updated:
                            insert_cols.append("updated_at")
                        placeholders = ["%s"] * len(values)
                        if has_updated:
                            placeholders.append("NOW()")
                        cur.execute(
                            f"INSERT INTO cameras({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})",
                            tuple(values),
                        )
                conn.commit()
            except Exception:
                if conn is not None:
                    conn.rollback()
                raise
            finally:
                if pool is not None and conn is not None:
                    pool.putconn(conn)


camera_config_store = CameraConfigStore()
