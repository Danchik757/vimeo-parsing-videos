"""Notification transports for the Vimeo downloader."""

import argparse
import atexit
import html
import json
import logging
import queue
import re
import shutil
import smtplib
import socket
import ssl
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
import urllib3.util.connection

from config_utils import load_json_config_with_optional_secrets


logger = logging.getLogger(__name__)
_FORCED_IPV4 = False
_HTML_BREAK_RE = re.compile(r"<(?:br\s*/?|/p|/div|/li|/pre|/tr|/h[1-6])>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _force_requests_ipv4():
    global _FORCED_IPV4
    if _FORCED_IPV4:
        return
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
    _FORCED_IPV4 = True


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_or_default(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _cfg_value(primary_cfg, fallback_cfg, key, default=None):
    if key in primary_cfg:
        return primary_cfg.get(key)
    return fallback_cfg.get(key, default)


def _normalize_transport(config):
    notifications_cfg = config.get("notifications", {}) or {}
    telegram_cfg = config.get("telegram", {}) or {}
    email_cfg = config.get("email", {}) or {}

    transport = str(notifications_cfg.get("transport", "")).strip().lower()
    if transport in {"telegram", "email", "both", "none"}:
        return transport

    telegram_enabled = bool(telegram_cfg.get("enabled", False))
    email_enabled = _bool_or_default(email_cfg.get("enabled"), False) or any(
        str(email_cfg.get(key, "")).strip()
        for key in ("smtp_host", "smtp_user", "smtp_pass", "mail_addr")
    )

    if telegram_enabled:
        return "telegram"
    if email_enabled:
        return "email"
    return "none"


def _transport_uses_telegram(transport):
    return transport in {"telegram", "both"}


def _transport_uses_email(transport):
    return transport in {"email", "both"}


def _html_to_plain_text(text):
    if not text:
        return ""
    plain = _HTML_BREAK_RE.sub("\n", str(text))
    plain = _HTML_TAG_RE.sub("", plain)
    plain = html.unescape(plain)
    plain = plain.replace("\r\n", "\n").replace("\r", "\n")
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip()


def _disk_check_candidate(config):
    settings_cfg = config.get("settings", {}) or {}
    files_cfg = config.get("files", {}) or {}
    candidate = Path(
        settings_cfg.get("disk_space_check_path")
        or files_cfg.get("videos_dir")
        or files_cfg.get("logs_dir")
        or "."
    )
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        candidate = candidate.parent if candidate.parent != candidate else Path.cwd()
    if not candidate.exists():
        candidate = Path.cwd()
    return candidate


def get_local_disk_snapshot(config):
    try:
        candidate = _disk_check_candidate(config)
        usage = shutil.disk_usage(candidate)
    except Exception:
        return None

    gib = float(1024 ** 3)
    return {
        "path": str(candidate),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "total_gb": round(usage.total / gib, 2),
        "used_gb": round(usage.used / gib, 2),
        "free_gb": round(usage.free / gib, 2),
    }


def build_local_disk_lines(config, include_path=False):
    snapshot = get_local_disk_snapshot(config)
    if not snapshot:
        return []

    lines = [
        (
            "local_disk: "
            f"<code>free={snapshot['free_gb']:.2f} GB "
            f"used={snapshot['used_gb']:.2f} GB "
            f"total={snapshot['total_gb']:.2f} GB</code>"
        )
    ]
    if include_path:
        lines.append(f"disk_path: <code>{html.escape(snapshot['path'])}</code>")
    return lines


class SourceAddressAdapter(HTTPAdapter):
    """Bind Telegram HTTP sockets to a specific local source IP."""

    def __init__(self, source_address=None, **kwargs):
        self.source_address = source_address
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        if self.source_address:
            pool_kwargs["source_address"] = (self.source_address, 0)
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        if self.source_address:
            proxy_kwargs["source_address"] = (self.source_address, 0)
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def build_bounded_http_adapter(source_address=None, pool_connections=1, pool_maxsize=1):
    kwargs = {
        "pool_connections": max(1, int(pool_connections or 1)),
        "pool_maxsize": max(1, int(pool_maxsize or 1)),
        "max_retries": 0,
        "pool_block": True,
    }
    if source_address:
        return SourceAddressAdapter(source_address=source_address, **kwargs)
    return HTTPAdapter(**kwargs)


class TelegramNotifier:
    """Send progress and alert notifications via Telegram, email, or both."""

    def __init__(self, config, worker_name=None, job_name=None):
        self.config = config
        telegram_cfg = config.get("telegram", {}) or {}
        email_cfg = config.get("email", {}) or {}
        notifications_cfg = config.get("notifications", {}) or {}
        runtime_cfg = config.get("runtime", {}) or {}

        self.transport = _normalize_transport(config)
        self.bot_token = str(telegram_cfg.get("bot_token", "")).strip()
        self.chat_id = str(telegram_cfg.get("chat_id", "")).strip()
        self.api_base_url = str(
            telegram_cfg.get("api_base_url", "https://api.telegram.org")
        ).rstrip("/")
        self.source_address = str(telegram_cfg.get("source_address", "")).strip() or None

        legacy_every_n = max(1, _int_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_every_n_videos", 1), 1))
        self.notify_download_every_n = max(
            0,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "notify_download_every_n_successes", legacy_every_n),
                legacy_every_n,
            ),
        )
        self.notify_coordinator_progress_every_seconds = max(
            0,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "notify_coordinator_progress_every_seconds", 0),
                0,
            ),
        )
        self.notify_skip_every_n = max(
            0,
            _int_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_skip_every_n_processed", 0), 0),
        )
        self.notify_progress_every_n = max(
            0,
            _int_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_progress_every_n_processed", 5), 5),
        )
        self.notify_progress_min_interval_seconds = max(
            0,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "notify_progress_min_interval_seconds", 0),
                0,
            ),
        )
        self.request_timeout_seconds = max(
            5,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "request_timeout_seconds", 30),
                30,
            ),
        )
        self.retry_attempts = max(
            1,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "retry_attempts", 4),
                4,
            ),
        )
        self.retry_delay_seconds = max(
            0,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "retry_delay_seconds", 3),
                3,
            ),
        )
        self.http_pool_connections = max(
            1,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "http_pool_connections", 1),
                1,
            ),
        )
        self.http_pool_maxsize = max(
            1,
            _int_or_default(
                _cfg_value(notifications_cfg, telegram_cfg, "http_pool_maxsize", 1),
                1,
            ),
        )
        self.force_ipv4 = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "force_ipv4", False), False)

        self.notify_on_start = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_start", True), True)
        self.notify_on_finish = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_finish", True), True)
        self.notify_on_error = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_error", True), True)
        self.notify_on_ip_block = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_ip_block", True), True)
        self.notify_on_stall = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_stall", True), True)
        self.notify_on_heartbeat = _bool_or_default(_cfg_value(notifications_cfg, telegram_cfg, "notify_on_heartbeat", True), True)
        self.separator_before_worker_messages = _bool_or_default(
            _cfg_value(notifications_cfg, telegram_cfg, "separator_before_worker_messages", False),
            False,
        )
        self.separator_text = str(
            _cfg_value(
                notifications_cfg,
                telegram_cfg,
                "separator_text",
                "------------------------------",
            )
        )
        separator_types = _cfg_value(
            notifications_cfg,
            telegram_cfg,
            "separator_message_types",
            [
                "start",
                "downloaded",
                "skipped",
                "error",
                "api_error",
                "ip_blocked",
                "progress",
                "stall",
                "heartbeat",
                "finish",
                "finish_with_error",
            ],
        )
        if not isinstance(separator_types, list):
            separator_types = []
        self.separator_message_types = {str(item) for item in separator_types}

        self.job_name = job_name or runtime_cfg.get("job_name") or "Vimeo Downloader"
        self.worker_name = worker_name or runtime_cfg.get("worker_name") or ""
        self._last_progress_message_ts = 0.0
        self._session = None
        self._message_queue = None
        self._sender_thread = None
        self._shutdown_started = False

        self.telegram_enabled = _transport_uses_telegram(self.transport)
        self.email_enabled = _transport_uses_email(self.transport)

        if self.telegram_enabled and (not self.bot_token or not self.chat_id):
            logger.warning(
                "Telegram transport requested but bot_token or chat_id is missing. Disabling Telegram transport."
            )
            self.telegram_enabled = False

        self.smtp_host = str(email_cfg.get("smtp_host", "")).strip()
        self.smtp_port = max(1, _int_or_default(email_cfg.get("smtp_port", 465), 465))
        self.smtp_ssl = _bool_or_default(email_cfg.get("smtp_ssl"), True)
        self.smtp_user = str(email_cfg.get("smtp_user", "")).strip()
        self.smtp_pass = str(email_cfg.get("smtp_pass", "")).strip()
        self.mail_addr = str(email_cfg.get("mail_addr") or self.smtp_user or "").strip()
        self.email_subject_prefix = str(email_cfg.get("subject_prefix", "")).strip()

        if self.email_enabled:
            missing_email_fields = [
                key
                for key, value in (
                    ("smtp_host", self.smtp_host),
                    ("smtp_port", self.smtp_port),
                    ("smtp_user", self.smtp_user),
                    ("smtp_pass", self.smtp_pass),
                    ("mail_addr", self.mail_addr),
                )
                if not value
            ]
            if missing_email_fields:
                logger.warning(
                    "Email transport requested but config is incomplete (%s). Disabling Email transport.",
                    ", ".join(missing_email_fields),
                )
                self.email_enabled = False

        self.enabled = self.telegram_enabled or self.email_enabled
        if not self.enabled:
            return

        if self.telegram_enabled:
            if self.force_ipv4:
                _force_requests_ipv4()
            self._session = requests.Session()
            adapter = build_bounded_http_adapter(
                source_address=self.source_address,
                pool_connections=self.http_pool_connections,
                pool_maxsize=self.http_pool_maxsize,
            )
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

        self._message_queue = queue.Queue()
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            name=f"notifier-sender-{self.worker_name or 'main'}",
            daemon=True,
        )
        self._sender_thread.start()
        atexit.register(self.shutdown)
        logger.info(
            "Notifications enabled for %s (transport=%s%s%s)",
            self.label,
            self.transport,
            f", chat_id={self.chat_id}" if self.telegram_enabled else "",
            f", mail_addr={self.mail_addr}" if self.email_enabled else "",
        )

    @property
    def label(self):
        if self.worker_name:
            return f"{self.job_name} | {self.worker_name}"
        return self.job_name

    def _deliver_telegram_message(self, text, parse_mode="HTML"):
        url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        last_error = None
        for attempt in range(1, self.retry_attempts + 1):
            response = None
            try:
                response = self._session.post(
                    url,
                    json=payload,
                    timeout=self.request_timeout_seconds,
                )
                if response.status_code == 200:
                    logger.debug("Telegram message sent: %s", text[:80])
                    return True
                last_error = f"status={response.status_code} body={response.text[:300]}"
                logger.warning(
                    "Telegram send attempt %d/%d failed: %s",
                    attempt,
                    self.retry_attempts,
                    last_error,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Telegram send attempt %d/%d error: %s",
                    attempt,
                    self.retry_attempts,
                    exc,
                )
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

            if attempt < self.retry_attempts and self.retry_delay_seconds > 0:
                time.sleep(self.retry_delay_seconds)

        logger.error("Telegram message was not delivered after retries: %s", last_error)
        return False

    def _deliver_email_message(self, text, parse_mode="HTML"):
        plain = _html_to_plain_text(text)
        if not plain:
            plain = self.label
        lines = [line.strip() for line in plain.splitlines() if line.strip()]
        subject = lines[0][:140] if lines else self.label
        if self.email_subject_prefix:
            subject = f"{self.email_subject_prefix} {subject}".strip()

        msg = MIMEText(plain, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.mail_addr
        msg["To"] = self.mail_addr

        ctx = ssl.create_default_context()
        last_error = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                if self.smtp_ssl:
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx, timeout=self.request_timeout_seconds) as smtp:
                        smtp.login(self.smtp_user, self.smtp_pass)
                        smtp.send_message(msg)
                else:
                    with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.request_timeout_seconds) as smtp:
                        smtp.starttls(context=ctx)
                        smtp.login(self.smtp_user, self.smtp_pass)
                        smtp.send_message(msg)
                logger.debug("Email notification sent: %s", subject)
                return True
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Email send attempt %d/%d error: %s",
                    attempt,
                    self.retry_attempts,
                    exc,
                )
                if attempt < self.retry_attempts and self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)

        logger.error("Email notification was not delivered after retries: %s", last_error)
        return False

    def _deliver_message(self, text, parse_mode="HTML"):
        if not self.enabled:
            return False

        ok = True
        delivered = False
        if self.telegram_enabled:
            delivered = True
            ok = self._deliver_telegram_message(text, parse_mode=parse_mode) and ok
        if self.email_enabled:
            delivered = True
            ok = self._deliver_email_message(text, parse_mode=parse_mode) and ok
        return delivered and ok

    def _sender_loop(self):
        while True:
            item = self._message_queue.get()
            if item is None:
                self._message_queue.task_done()
                break

            text, parse_mode, done_event, result_holder = item
            result = self._deliver_message(text, parse_mode=parse_mode)
            if result_holder is not None:
                result_holder["ok"] = result
            if done_event is not None:
                done_event.set()
            self._message_queue.task_done()

    def send_message(self, text, parse_mode="HTML", wait=False):
        if not self.enabled:
            return False

        if self._sender_thread is None or not self._sender_thread.is_alive():
            return self._deliver_message(text, parse_mode=parse_mode)

        done_event = None
        result_holder = None
        if wait:
            done_event = threading.Event()
            result_holder = {"ok": False}

        self._message_queue.put((text, parse_mode, done_event, result_holder))

        if not wait:
            return True

        max_wait = (
            self.retry_attempts * self.request_timeout_seconds
            + max(0, self.retry_attempts - 1) * self.retry_delay_seconds
            + 5
        )
        completed = done_event.wait(timeout=max_wait)
        if not completed:
            logger.error("Timed out waiting for notifier sender thread to finish")
            return False
        return bool(result_holder["ok"])

    def shutdown(self, timeout=None):
        if not self.enabled:
            return True
        if self._sender_thread is None:
            if self._session is not None:
                self._session.close()
            return True
        if self._shutdown_started:
            return not self._sender_thread.is_alive()

        self._shutdown_started = True
        self._message_queue.put(None)
        self._sender_thread.join(timeout=timeout)
        if not self._sender_thread.is_alive() and self._session is not None:
            self._session.close()
        return not self._sender_thread.is_alive()

    def _compose_message(self, title, lines):
        body = "\n".join(lines)
        return (
            f"<b>{html.escape(self.label)}</b>\n"
            f"{html.escape(title)}\n\n"
            f"{body}\n\n"
            f"time: <code>{_now()}</code>"
        )


    def _should_send_separator(self, title):
        if not self.separator_before_worker_messages:
            return False
        if not self.worker_name or self.worker_name == "coordinator":
            return False
        return title in self.separator_message_types

    def _send_separator(self):
        separator_line = html.escape(self.separator_text)
        return self.send_message(f"<code>{separator_line}</code>")

    def notify_custom(self, title, lines, icon=None, wait=False):
        if self._should_send_separator(title):
            self._send_separator()
        return self.send_message(self._compose_message(title, lines), wait=wait)

    def should_notify_download(self, downloaded_count):
        if self.notify_download_every_n <= 0:
            return False
        return downloaded_count > 0 and downloaded_count % self.notify_download_every_n == 0

    def should_notify_skip(self, processed_num):
        if self.notify_skip_every_n <= 0:
            return False
        return processed_num > 0 and processed_num % self.notify_skip_every_n == 0

    def should_notify_progress(self, processed_num):
        if self.notify_progress_every_n <= 0:
            return False
        if processed_num <= 0 or processed_num % self.notify_progress_every_n != 0:
            return False
        if self.notify_progress_min_interval_seconds <= 0:
            return True
        now = time.time()
        if now - self._last_progress_message_ts < self.notify_progress_min_interval_seconds:
            return False
        self._last_progress_message_ts = now
        return True

    def notify_start(self, total_videos, test_mode=False, browser_mode=None):
        if not self.enabled or not self.notify_on_start:
            return

        mode = "test" if test_mode else "full"
        lines = [
            f"mode: <code>{html.escape(mode)}</code>",
            f"total: <code>{total_videos}</code>",
        ]
        if browser_mode:
            lines.append(f"browser: <code>{html.escape(browser_mode)}</code>")
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("start", lines)

    def notify_video_downloaded(
        self,
        video_id,
        filename,
        file_size_mb,
        processed_num,
        total_videos,
        downloaded_count,
    ):
        if not self.enabled or not self.should_notify_download(downloaded_count):
            return

        progress_pct = (processed_num / total_videos * 100) if total_videos else 0
        lines = [
            f"video_id: <code>{html.escape(str(video_id))}</code>",
            f"file: <code>{html.escape(str(filename))}</code>",
            f"size_mb: <code>{file_size_mb:.1f}</code>",
            f"downloaded: <code>{downloaded_count}</code>",
            f"progress: <code>{processed_num}/{total_videos} ({progress_pct:.1f}%)</code>",
        ]
        self.notify_custom("downloaded", lines)

    def notify_video_skipped(self, video_id, reason, processed_num, total_videos):
        if not self.enabled or not self.should_notify_skip(processed_num):
            return

        lines = [
            f"video_id: <code>{html.escape(str(video_id))}</code>",
            f"reason: <code>{html.escape(str(reason))}</code>",
            f"progress: <code>{processed_num}/{total_videos}</code>",
        ]
        self.notify_custom("skipped", lines)

    def notify_error(self, video_id, error_message, processed_num, total_videos, stage="download"):
        if not self.enabled or not self.notify_on_error:
            return

        lines = [
            f"video_id: <code>{html.escape(str(video_id))}</code>",
            f"stage: <code>{html.escape(str(stage))}</code>",
            f"error: <code>{html.escape(str(error_message)[:350])}</code>",
            f"progress: <code>{processed_num}/{total_videos}</code>",
        ]
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("error", lines)

    def notify_ip_blocked(self, ip_address, reason):
        if not self.enabled or not self.notify_on_ip_block:
            return

        lines = [
            f"ip: <code>{html.escape(str(ip_address))}</code>",
            f"reason: <code>{html.escape(str(reason))}</code>",
        ]
        return self.notify_custom("ip_blocked", lines)

    def notify_progress(
        self,
        processed_num,
        total_videos,
        downloaded,
        skipped,
        failed,
        current_video_id=None,
        current_stage=None,
    ):
        if not self.enabled or not self.should_notify_progress(processed_num):
            return

        progress_pct = (processed_num / total_videos * 100) if total_videos else 0
        lines = [
            f"processed: <code>{processed_num}/{total_videos} ({progress_pct:.1f}%)</code>",
            f"downloaded: <code>{downloaded}</code>",
            f"skipped: <code>{skipped}</code>",
            f"failed: <code>{failed}</code>",
        ]
        if current_video_id:
            lines.append(f"current_video: <code>{html.escape(str(current_video_id))}</code>")
        if current_stage:
            lines.append(f"stage: <code>{html.escape(str(current_stage))}</code>")
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("progress", lines)

    def notify_finish(self, stats, wait=False):
        if not self.enabled or not self.notify_on_finish:
            return

        total = stats.get("total", 0)
        downloaded = stats.get("downloaded", 0)
        skipped = stats.get("skipped", 0)
        failed = stats.get("failed", 0)
        exit_code = stats.get("exit_code", 0)
        fatal_error = stats.get("fatal_error")
        restart_requested = bool(stats.get("restart_requested"))

        if restart_requested:
            return

        title = "finish" if exit_code == 0 else "finish_with_error"
        lines = [
            f"total: <code>{total}</code>",
            f"downloaded: <code>{downloaded}</code>",
            f"skipped: <code>{skipped}</code>",
            f"failed: <code>{failed}</code>",
            f"exit_code: <code>{exit_code}</code>",
        ]
        if fatal_error:
            lines.append(f"fatal: <code>{html.escape(str(fatal_error)[:350])}</code>")
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom(title, lines, wait=wait)

    def notify_api_error(self, error_code, error_message):
        if not self.enabled or not self.notify_on_error:
            return

        lines = [
            f"code: <code>{error_code}</code>",
            f"message: <code>{html.escape(str(error_message))}</code>",
        ]
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("api_error", lines)

    def notify_stall(
        self,
        video_id,
        stage,
        stalled_seconds,
        processed_num,
        total_videos,
        downloaded,
        skipped,
        failed,
    ):
        if not self.enabled or not self.notify_on_stall:
            return

        lines = [
            f"video_id: <code>{html.escape(str(video_id or '-'))}</code>",
            f"stage: <code>{html.escape(str(stage or 'unknown'))}</code>",
            f"idle_seconds: <code>{int(stalled_seconds)}</code>",
            f"processed: <code>{processed_num}/{total_videos}</code>",
            f"downloaded: <code>{downloaded}</code>",
            f"skipped: <code>{skipped}</code>",
            f"failed: <code>{failed}</code>",
        ]
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("stall", lines)

    def notify_heartbeat(
        self,
        processed_num,
        total_videos,
        downloaded,
        skipped,
        failed,
        stage,
        video_id,
    ):
        if not self.enabled or not self.notify_on_heartbeat:
            return

        lines = [
            f"processed: <code>{processed_num}/{total_videos}</code>",
            f"downloaded: <code>{downloaded}</code>",
            f"skipped: <code>{skipped}</code>",
            f"failed: <code>{failed}</code>",
            f"stage: <code>{html.escape(str(stage or 'idle'))}</code>",
            f"video_id: <code>{html.escape(str(video_id or '-'))}</code>",
        ]
        lines.extend(build_local_disk_lines(self.config))
        self.notify_custom("heartbeat", lines)


def test_telegram_connection(config, worker_name=None, job_name=None):
    notifier = TelegramNotifier(config, worker_name=worker_name, job_name=job_name)
    if not notifier.enabled:
        print("❌ Notifications disabled in config")
        return False

    try:
        return notifier.notify_custom(
            "test_message",
            [
                f"transport: <code>{html.escape(notifier.transport)}</code>",
                "status: <code>ok</code>",
                "details: <code>notification configuration works</code>",
            ],
            wait=True,
        )
    finally:
        notifier.shutdown(timeout=10)


def main():
    parser = argparse.ArgumentParser(description="Test parser notifications.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--worker-name", default="", help="Worker label for test message")
    parser.add_argument("--job-name", default="", help="Job label for test message")
    args = parser.parse_args()

    config, _, _, _ = load_json_config_with_optional_secrets(args.config)

    success = test_telegram_connection(
        config,
        worker_name=args.worker_name or None,
        job_name=args.job_name or None,
    )
    if success:
        print("✅ Тестовое сообщение отправлено успешно")
    else:
        print("❌ Не удалось отправить тестовое сообщение")


if __name__ == "__main__":
    main()
