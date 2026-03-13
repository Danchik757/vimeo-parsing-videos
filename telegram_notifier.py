"""Telegram notifications for the Vimeo downloader."""

import argparse
import html
import json
import logging
import time
from datetime import datetime

import requests


logger = logging.getLogger(__name__)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class TelegramNotifier:
    """Send progress and alert notifications via Telegram Bot API."""

    def __init__(self, config, worker_name=None, job_name=None):
        telegram_cfg = config.get("telegram", {})
        runtime_cfg = config.get("runtime", {})

        self.enabled = telegram_cfg.get("enabled", False)
        self.bot_token = telegram_cfg.get("bot_token", "")
        self.chat_id = telegram_cfg.get("chat_id", "")

        legacy_every_n = max(1, _int_or_default(telegram_cfg.get("notify_every_n_videos", 1), 1))
        self.notify_download_every_n = max(
            0,
            _int_or_default(
                telegram_cfg.get("notify_download_every_n_successes", legacy_every_n),
                legacy_every_n,
            ),
        )
        self.notify_coordinator_progress_every_seconds = max(
            0,
            _int_or_default(
                telegram_cfg.get("notify_coordinator_progress_every_seconds", 0),
                0,
            ),
        )
        self.notify_skip_every_n = max(
            0,
            _int_or_default(telegram_cfg.get("notify_skip_every_n_processed", 0), 0),
        )
        self.notify_progress_every_n = max(
            0,
            _int_or_default(telegram_cfg.get("notify_progress_every_n_processed", 5), 5),
        )
        self.notify_progress_min_interval_seconds = max(
            0,
            _int_or_default(
                telegram_cfg.get("notify_progress_min_interval_seconds", 0),
                0,
            ),
        )

        self.notify_on_start = telegram_cfg.get("notify_on_start", True)
        self.notify_on_finish = telegram_cfg.get("notify_on_finish", True)
        self.notify_on_error = telegram_cfg.get("notify_on_error", True)
        self.notify_on_ip_block = telegram_cfg.get("notify_on_ip_block", True)
        self.notify_on_stall = telegram_cfg.get("notify_on_stall", True)
        self.notify_on_heartbeat = telegram_cfg.get("notify_on_heartbeat", True)
        self.separator_before_worker_messages = telegram_cfg.get(
            "separator_before_worker_messages",
            False,
        )
        self.separator_text = str(
            telegram_cfg.get(
                "separator_text",
                "------------------------------",
            )
        )
        separator_types = telegram_cfg.get(
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

        if self.enabled and (not self.bot_token or not self.chat_id):
            logger.warning(
                "Telegram enabled but bot_token or chat_id is missing. Disabling notifications."
            )
            self.enabled = False

        if self.enabled:
            logger.info(
                "Telegram notifications enabled for %s (chat_id: %s)",
                self.label,
                self.chat_id,
            )

    @property
    def label(self):
        if self.worker_name:
            return f"{self.job_name} | {self.worker_name}"
        return self.job_name

    def send_message(self, text, parse_mode="HTML"):
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                logger.debug("Telegram message sent: %s", text[:80])
                return True
            logger.error(
                "Failed to send Telegram message: %s - %s",
                response.status_code,
                response.text,
            )
            return False
        except Exception as exc:
            logger.error("Error sending Telegram message: %s", exc)
            return False

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

    def notify_custom(self, title, lines, icon=None):
        if self._should_send_separator(title):
            self._send_separator()
        return self.send_message(self._compose_message(title, lines))

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
        self.notify_custom("progress", lines)

    def notify_finish(self, stats):
        if not self.enabled or not self.notify_on_finish:
            return

        total = stats.get("total", 0)
        downloaded = stats.get("downloaded", 0)
        skipped = stats.get("skipped", 0)
        failed = stats.get("failed", 0)
        exit_code = stats.get("exit_code", 0)
        fatal_error = stats.get("fatal_error")

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
        self.notify_custom(title, lines)

    def notify_api_error(self, error_code, error_message):
        if not self.enabled or not self.notify_on_error:
            return

        lines = [
            f"code: <code>{error_code}</code>",
            f"message: <code>{html.escape(str(error_message))}</code>",
        ]
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
        self.notify_custom("heartbeat", lines)


def test_telegram_connection(config, worker_name=None, job_name=None):
    notifier = TelegramNotifier(config, worker_name=worker_name, job_name=job_name)
    if not notifier.enabled:
        print("❌ Telegram notifications disabled in config")
        return False

    return notifier.notify_custom(
        "test_message",
        [
            "status: <code>ok</code>",
            "details: <code>telegram bot configuration works</code>",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="Test Telegram notifications.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--worker-name", default="", help="Worker label for test message")
    parser.add_argument("--job-name", default="", help="Job label for test message")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

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
