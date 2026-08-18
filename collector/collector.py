from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import ddddocr
import requests
from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from temperature_parser import extract_temperature_records


LOGIN_URL = "https://new.e-elitech.cn/user/login"
DEVICE_LIST_URL = "https://new.e-elitech.cn/device/list"
GITHUB_API_VERSION = "2022-11-28"


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("temperature-collector")


# ddddocr 1.0.6 still references Pillow's removed Image.ANTIALIAS alias.
# Keep current Pillow versions and restore the alias to its equivalent filter.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


def first_env(*names, default=None, required=False):
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    if required:
        raise RuntimeError(f"缺少必要环境变量（可用名称：{', '.join(names)}）")
    return default


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"环境变量 {name} 应为 true 或 false")


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not config.get("devices"):
        raise RuntimeError("config.json 中没有配置设备")
    return config


def xpath_literal(value):
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


class ReadingStore:
    def __init__(self, path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                device_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                temperature REAL NOT NULL,
                inserted_at TEXT NOT NULL,
                PRIMARY KEY (device_id, recorded_at)
            )
            """
        )
        self.connection.commit()

    def latest_time(self, device_id):
        row = self.connection.execute(
            "SELECT MAX(recorded_at) FROM readings WHERE device_id = ?", (device_id,)
        ).fetchone()
        return row[0] if row and row[0] else None

    def upsert(self, device_id, records):
        changed = 0
        inserted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connection:
            for record in records:
                before = self.connection.total_changes
                self.connection.execute(
                    """
                    INSERT INTO readings (device_id, recorded_at, temperature, inserted_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(device_id, recorded_at) DO UPDATE SET
                        temperature = excluded.temperature,
                        inserted_at = excluded.inserted_at
                    WHERE readings.temperature != excluded.temperature
                    """,
                    (device_id, record["time"], float(record["temperature"]), inserted_at),
                )
                changed += self.connection.total_changes - before
        return changed

    def records_since(self, device_id, cutoff):
        rows = self.connection.execute(
            """
            SELECT recorded_at, temperature
            FROM readings
            WHERE device_id = ? AND recorded_at >= ?
            ORDER BY recorded_at
            """,
            (device_id, cutoff),
        ).fetchall()
        return [{"time": row[0], "temperature": row[1]} for row in rows]


class GitHubPublisher:
    def __init__(self, token, repository, branch, data_path):
        self.repository = repository
        self.branch = branch
        self.data_path = data_path
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "temperature-collector/2",
            }
        )

    def _content_url(self, path):
        return f"https://api.github.com/repos/{self.repository}/contents/{path}"

    def get_file(self, path):
        response = self.session.get(
            self._content_url(path), params={"ref": self.branch}, timeout=30
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        info = response.json()
        content = info.get("content")
        if content:
            raw = base64.b64decode(content)
        elif info.get("download_url"):
            raw_response = requests.get(info["download_url"], timeout=30)
            raw_response.raise_for_status()
            raw = raw_response.content
        else:
            raise RuntimeError(f"GitHub未返回 {path} 的文件内容")
        return {"sha": info["sha"], "content": raw}

    def put_file(self, path, content, message, sha=None):
        body = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        response = self.session.put(self._content_url(path), json=body, timeout=60)
        response.raise_for_status()
        return response.json()

    def archive_legacy(self, existing, system_id):
        if not existing:
            return
        try:
            old_payload = json.loads(existing["content"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("现有GitHub数据不是有效JSON，跳过自动归档")
            return
        if old_payload.get("systemId") == system_id:
            return

        old_date = str(old_payload.get("lastUpdated") or datetime.now().date())[:10]
        old_date = re.sub(r"[^0-9-]", "", old_date) or datetime.now().strftime("%Y-%m-%d")
        archive_path = f"archive/legacy_temperature_data_{old_date}.json"
        if self.get_file(archive_path):
            LOGGER.info("旧数据归档已存在：%s", archive_path)
            return
        self.put_file(
            archive_path,
            existing["content"],
            f"archive legacy temperature data from {old_date}",
        )
        LOGGER.info("旧设备数据已归档到 %s", archive_path)

    def publish(self, payload, archive_legacy=False):
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False
        ).encode("utf-8")
        existing = self.get_file(self.data_path)
        if archive_legacy:
            self.archive_legacy(existing, payload["systemId"])
        if existing and existing["content"] == encoded:
            LOGGER.info("GitHub数据未变化，跳过提交")
            return False
        self.put_file(
            self.data_path,
            encoded,
            f"update {payload['systemName']} temperature data",
            sha=existing["sha"] if existing else None,
        )
        LOGGER.info("GitHub温度数据已发布，共 %.1f KiB", len(encoded) / 1024)
        return True


class TemperatureCollector:
    def __init__(self):
        self.config = load_config(os.getenv("CONFIG_PATH", "/app/config.json"))
        self.timezone = ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))
        self.data_dir = Path(os.getenv("DATA_DIR", "/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "debug").mkdir(exist_ok=True)
        self.store = ReadingStore(self.data_dir / "temperature.sqlite3")
        self.username = first_env(
            "COLD_CLOUD_USERNAME", "ELITECH_USERNAME", required=True
        )
        self.password = first_env(
            "COLD_CLOUD_PASSWORD", "ELITECH_PASSWORD", required=True
        )
        self.publish_enabled = env_flag("PUBLISH_ENABLED", default=False)
        self.publisher = None
        if self.publish_enabled:
            repository = first_env("GITHUB_REPOSITORY")
            if not repository:
                owner = first_env("GITHUB_OWNER", default="gzt2003")
                repository = f"{owner}/{first_env('GITHUB_REPO', default='document-temperature')}"
            self.publisher = GitHubPublisher(
                first_env("GITHUB_TOKEN", required=True),
                repository,
                first_env("GITHUB_BRANCH", default="main"),
                first_env("GITHUB_DATA_PATH", default="temperature_data.json"),
            )
        try:
            self.ocr = ddddocr.DdddOcr(show_ad=False)
        except TypeError:
            self.ocr = ddddocr.DdddOcr()
        self.driver = None
        self.api_urls = {}
        self.cycle_number = 0
        self.legacy_archive_pending = bool(
            self.config.get("archive_legacy_on_first_publish", True)
        )

    def create_driver(self):
        options = Options()
        options.binary_location = "/usr/bin/chromium"
        for argument in (
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--lang=zh-CN",
        ):
            options.add_argument(argument)
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"), options=options
        )
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(45)
        return driver

    def close_driver(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self.api_urls.clear()

    def close_popups(self):
        selectors = (
            ".ant-modal-close",
            ".ant-drawer-close",
            ".ant-notification-notice-close",
            "[aria-label='Close']",
        )
        for selector in selectors:
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if element.is_displayed():
                        self.driver.execute_script("arguments[0].click();", element)
                        time.sleep(0.3)
                except Exception:
                    continue
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

    def login(self):
        attempts = int(self.config.get("login_attempts", 5))
        for attempt in range(1, attempts + 1):
            LOGGER.info("登录精创冷云（第 %s/%s 次）", attempt, attempts)
            self.driver.get(LOGIN_URL)
            wait = WebDriverWait(self.driver, 12)
            username_input = wait.until(EC.presence_of_element_located((By.ID, "userName")))
            password_input = self.driver.find_element(By.ID, "accountPassword")
            username_input.clear()
            username_input.send_keys(self.username)
            password_input.clear()
            password_input.send_keys(self.password)

            captcha_image = wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "verify-code"))
            )
            image_base64 = self.driver.execute_script(
                """
                const image = arguments[0];
                const canvas = document.createElement('canvas');
                canvas.width = image.width;
                canvas.height = image.height;
                canvas.getContext('2d').drawImage(image, 0, 0, image.width, image.height);
                return canvas.toDataURL('image/jpeg').split(',')[1];
                """,
                captcha_image,
            )
            captcha = re.sub(
                r"[^0-9A-Za-z]", "", self.ocr.classification(base64.b64decode(image_base64))
            )
            captcha_input = self.driver.find_element(By.ID, "verCode")
            captcha_input.clear()
            captcha_input.send_keys(captcha)

            agree = self.driver.find_element(By.ID, "agree")
            if not agree.is_selected():
                self.driver.execute_script("arguments[0].click();", agree)
            login_button = self.driver.find_element(
                By.XPATH, "//button[.//span[contains(normalize-space(), '登')]]"
            )
            self.driver.execute_script("arguments[0].click();", login_button)
            try:
                WebDriverWait(self.driver, 12).until(
                    lambda current: "/user/login" not in current.current_url
                )
                time.sleep(2)
                self.close_popups()
                LOGGER.info("精创冷云登录成功")
                return
            except TimeoutException:
                LOGGER.warning("本次验证码或登录校验未通过")
        raise RuntimeError("多次尝试后仍无法登录精创冷云")

    def ensure_session(self):
        if self.driver is None:
            self.driver = self.create_driver()
            self.login()
            return
        try:
            self.driver.get(DEVICE_LIST_URL)
            time.sleep(2)
            if "/user/login" in self.driver.current_url:
                self.login()
        except WebDriverException:
            self.close_driver()
            self.driver = self.create_driver()
            self.login()

    def _drain_network_log(self):
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

    def _click_device(self, cloud_name):
        literal = xpath_literal(cloud_name)
        selectors = (
            f"//a[normalize-space()={literal}]",
            f"//span[contains(@class,'device-name') and normalize-space()={literal}]",
            f"//*[normalize-space()={literal} and (self::a or self::span or self::p)]",
        )
        for selector in selectors:
            for element in self.driver.find_elements(By.XPATH, selector):
                try:
                    if element.is_displayed():
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", element
                        )
                        self.driver.execute_script("arguments[0].click();", element)
                        return
                except Exception:
                    continue
        raise RuntimeError(f"设备列表中未找到 {cloud_name}")

    def capture_api_payloads(self, device):
        self.driver.get(DEVICE_LIST_URL)
        WebDriverWait(self.driver, 15).until(
            lambda current: current.execute_script("return document.readyState") == "complete"
        )
        time.sleep(2)
        self.close_popups()
        self._drain_network_log()
        self._click_device(device["cloud_name"])
        time.sleep(3)

        tabs = self.driver.find_elements(
            By.XPATH,
            "//div[@role='tab' and contains(normalize-space(), '数据图表')]",
        )
        if tabs:
            self.driver.execute_script("arguments[0].click();", tabs[0])
        time.sleep(5)
        self.close_popups()

        responses = []
        for entry in self.driver.get_log("performance"):
            try:
                message = json.loads(entry["message"])["message"]
                if message.get("method") != "Network.responseReceived":
                    continue
                params = message.get("params", {})
                if params.get("type") not in ("XHR", "Fetch"):
                    continue
                response = params.get("response", {})
                url = response.get("url", "")
                request_id = params.get("requestId")
            except Exception:
                continue
            if "/api/data/" not in url or not request_id:
                continue
            if "chartData" in url:
                priority = 0
            elif "realtimeData" in url:
                priority = 1
            else:
                continue
            responses.append((priority, url, request_id))

        candidates = sorted(responses, key=lambda item: item[0])
        if not candidates:
            raise RuntimeError(f"未捕获到 {device['cloud_name']} 的温度数据接口")

        payloads = []
        body_errors = []
        for _, url, request_id in candidates:
            try:
                body_info = self.driver.execute_cdp_cmd(
                    "Network.getResponseBody", {"requestId": request_id}
                )
                body = body_info.get("body", "")
                if body_info.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8")
                payloads.append({"url": url, "payload": json.loads(body or "{}")})
            except Exception as exc:
                body_errors.append(f"{urlparse(url).path}: {exc}")

        if not payloads:
            details = "; ".join(body_errors[:3])
            raise RuntimeError(
                f"已捕获 {device['cloud_name']} 数据接口，但无法读取浏览器响应体：{details}"
            )

        paths = sorted({urlparse(item["url"]).path for item in payloads})
        LOGGER.info(
            "%s 已读取网页原始响应 %s（%s 个）",
            device["display_name"],
            ", ".join(paths),
            len(payloads),
        )
        return payloads

    def fetch_json(self, url):
        result = self.driver.execute_async_script(
            """
            const url = arguments[0];
            const done = arguments[arguments.length - 1];
            fetch(url, {credentials: 'include'})
              .then(response => response.text().then(text => done({
                ok: response.ok, status: response.status, text
              })))
              .catch(error => done({ok: false, status: 0, text: String(error)}));
            """,
            url,
        )
        if not result.get("ok"):
            raise RuntimeError(
                f"冷云接口请求失败 HTTP {result.get('status')}: {result.get('text', '')[:200]}"
            )
        return json.loads(result.get("text") or "{}")

    @staticmethod
    def url_with_range(url, start_time, end_time):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["startTime"] = str(int(start_time.timestamp()))
        query["endTime"] = str(int(end_time.timestamp()))
        return urlunparse(parsed._replace(query=urlencode(query)))

    def query_windows(self, device_id):
        now = datetime.now(self.timezone)
        cutoff = now - timedelta(days=int(self.config.get("history_days", 14)))
        latest = self.store.latest_time(device_id)
        if latest:
            latest_time = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=self.timezone
            )
            start = max(cutoff, latest_time - timedelta(days=1))
        else:
            start = cutoff

        cursor = start
        while cursor < now:
            next_midnight = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = min(now, next_midnight - timedelta(seconds=1))
            if end < cursor:
                end = min(now, cursor + timedelta(days=1))
            yield cursor, end
            cursor = end + timedelta(seconds=1)

    def collect_device(self, device):
        payloads = self.capture_api_payloads(device)
        all_records = {}
        for captured in payloads:
            payload = captured["payload"]
            records = extract_temperature_records(
                payload,
                self.config.get("temperature_probe_names", ["探头2"]),
                self.config.get("timezone", "Asia/Shanghai"),
            )
            for record in records:
                all_records[record["time"]] = record

        debug_path = self.data_dir / "debug" / f"{device['id']}_latest.json"
        debug_path.write_text(
            json.dumps(
                {
                    "responses": [
                        {
                            "path": urlparse(captured["url"]).path,
                            "payload": captured["payload"],
                        }
                        for captured in payloads
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        records = [all_records[key] for key in sorted(all_records)]
        if not records:
            api_messages = []
            for captured in payloads:
                payload = captured["payload"]
                if isinstance(payload, dict):
                    code = payload.get("code")
                    message = payload.get("message")
                    if code is not None or message:
                        api_messages.append(f"code={code}, message={message}")
            detail = f"；接口状态：{' | '.join(api_messages)}" if api_messages else ""
            raise RuntimeError(
                f"{device['display_name']} 网页接口有响应，但未解析出探头2/摄氏度记录{detail}"
            )
        changed = self.store.upsert(device["id"], records)
        LOGGER.info(
            "%s 读取 %s 条，新增或修正 %s 条",
            device["display_name"],
            len(records),
            changed,
        )
        return changed

    def build_payload(self, device_status):
        now = datetime.now(self.timezone)
        cutoff = (now - timedelta(days=int(self.config.get("history_days", 14)))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        devices = {}
        latest_values = []
        for device in self.config["devices"]:
            history = {}
            records = self.store.records_since(device["id"], cutoff)
            for record in records:
                date, clock = record["time"].split(" ", 1)
                history.setdefault(date, []).append([clock, record["temperature"]])
                latest_values.append(record["time"])
            devices[device["id"]] = {
                "name": device["display_name"],
                "cloudName": device["cloud_name"],
                "unit": "℃",
                "history": history,
                "status": device_status.get(device["id"], "unknown"),
            }
        return {
            "schemaVersion": 2,
            "systemId": self.config["system_id"],
            "systemName": self.config["system_name"],
            "lastUpdated": max(latest_values) if latest_values else None,
            "timezone": self.config.get("timezone", "Asia/Shanghai"),
            "historyDays": int(self.config.get("history_days", 14)),
            "devices": devices,
        }

    def write_health(self, status, device_status, error=None, published=False):
        content = {
            "status": status,
            "checkedAt": datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S"),
            "publishEnabled": self.publish_enabled,
            "published": published,
            "devices": device_status,
            "error": str(error)[:1000] if error else None,
        }
        target = self.data_dir / "health.json"
        temporary = self.data_dir / "health.json.tmp"
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def run_cycle(self):
        self.ensure_session()
        device_status = {}
        changed = 0
        successes = 0
        for device in self.config["devices"]:
            try:
                changed += self.collect_device(device)
                device_status[device["id"]] = "online"
                successes += 1
            except Exception as exc:
                device_status[device["id"]] = "error"
                LOGGER.exception("采集 %s 失败：%s", device["display_name"], exc)

        if not successes:
            raise RuntimeError("本轮六个设备均采集失败")

        payload = self.build_payload(device_status)
        preview_target = self.data_dir / "temperature_data_preview.json"
        preview_temporary = self.data_dir / "temperature_data_preview.json.tmp"
        preview_temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(preview_temporary, preview_target)

        published = False
        if self.publish_enabled and (changed or self.legacy_archive_pending):
            published = self.publisher.publish(
                payload, archive_legacy=self.legacy_archive_pending
            )
            self.legacy_archive_pending = False
        elif not self.publish_enabled:
            LOGGER.info("试运行模式：数据仅写入 %s，不上传GitHub", preview_target)
        self.write_health("ok" if successes == len(self.config["devices"]) else "partial", device_status, published=published)
        return changed, published

    def run_forever(self):
        poll_seconds = int(self.config.get("poll_seconds", 300))
        retry_seconds = int(self.config.get("retry_seconds", 60))
        while True:
            started = time.monotonic()
            self.cycle_number += 1
            try:
                changed, published = self.run_cycle()
                LOGGER.info("本轮完成：变化 %s，发布 %s", changed, published)
                if self.cycle_number % 24 == 0:
                    LOGGER.info("定期重启浏览器以释放资源")
                    self.close_driver()
                wait_seconds = max(1, poll_seconds - int(time.monotonic() - started))
            except Exception as exc:
                LOGGER.exception("采集轮次失败：%s", exc)
                self.write_health("error", {}, error=exc)
                self.close_driver()
                wait_seconds = retry_seconds
            LOGGER.info("等待 %s 秒后继续", wait_seconds)
            time.sleep(wait_seconds)


def main():
    collector = TemperatureCollector()
    try:
        if env_flag("RUN_ONCE", default=False):
            changed, published = collector.run_cycle()
            LOGGER.info("单次采集完成：变化 %s，发布 %s", changed, published)
        else:
            collector.run_forever()
    finally:
        collector.close_driver()


if __name__ == "__main__":
    main()

