#!/usr/bin/env python3
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import analyze_mediafire
import webview


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_OUTPUT = ROOT / "downloads"
USER_SETTINGS_PATH = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "MediaFire Downloader"
    / "settings.json"
)
MAX_REQUEST_SIZE = 64 * 1024
MAX_LOG_LINES = 3000
MAX_NETWORK_SAMPLES = 60
NETWORK_SAMPLE_INTERVAL = 0.5
PROGRESS_PREFIX = "MF_DL_PROGRESS:"
SETTINGS_LOCK = threading.Lock()


def load_last_destination():
    with SETTINGS_LOCK:
        settings = _read_user_settings()
    destination = settings.get("last_destination")
    if destination is not None and not isinstance(destination, str):
        raise ValueError("Saved download folder is invalid.")
    return destination


def _read_user_settings():
    try:
        settings = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print(
            "Saved app settings are invalid; using defaults: {}".format(error),
            file=sys.stderr,
        )
        return {}
    except OSError as error:
        print(
            "Could not read saved app settings; using defaults: {}".format(error),
            file=sys.stderr,
        )
        return {}

    if not isinstance(settings, dict):
        print("Saved app settings are invalid; using defaults.", file=sys.stderr)
        return {}
    return settings


def _write_user_settings(settings):
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(USER_SETTINGS_PATH.parent),
            prefix=USER_SETTINGS_PATH.name + ".",
            suffix=".tmp",
            delete=False,
        ) as settings_file:
            temporary_path = settings_file.name
            settings_file.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_path, USER_SETTINGS_PATH)
        temporary_path = None
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def load_window_position():
    with SETTINGS_LOCK:
        settings = _read_user_settings()
    position = settings.get("window_position")
    if not isinstance(position, dict):
        return None

    x = position.get("x")
    y = position.get("y")
    if type(x) is not int or type(y) is not int:
        return None

    title_bar_height = 72
    for screen in webview.screens:
        if (
            x < screen.x + screen.width
            and x + 100 > screen.x
            and y < screen.y + screen.height
            and y + title_bar_height > screen.y
        ):
            return x, y
    return None


def app_has_launched():
    with SETTINGS_LOCK:
        settings = _read_user_settings()
    return settings.get("has_launched") is True


def mark_app_launched():
    with SETTINGS_LOCK:
        settings = _read_user_settings()
        settings["has_launched"] = True
        try:
            _write_user_settings(settings)
        except OSError as error:
            print("Could not save app launch state: {}".format(error), file=sys.stderr)


def save_window_position(x, y):
    with SETTINGS_LOCK:
        settings = _read_user_settings()
        settings["window_position"] = {"x": int(x), "y": int(y)}
        _write_user_settings(settings)


def save_last_destination(destination_text):
    if not isinstance(destination_text, str) or not destination_text.strip():
        raise ValueError("Enter a folder to save downloads to.")

    destination = Path(destination_text.strip()).expanduser()
    if not destination.is_absolute():
        destination = ROOT / destination
    destination = destination.resolve()

    with SETTINGS_LOCK:
        settings = _read_user_settings()
        settings["last_destination"] = str(destination)
        _write_user_settings(settings)
    return str(destination)


class WindowPositionSaver:
    def __init__(self, delay=0.4):
        self.delay = delay
        self.lock = threading.Lock()
        self.position = None
        self.timer = None
        self.generation = 0

    def moved(self, x, y):
        with self.lock:
            self.position = (x, y)
            self.generation += 1
            generation = self.generation
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(
                self.delay,
                self._save_pending,
                args=(generation,),
            )
            self.timer.daemon = True
            self.timer.start()

    def _save_pending(self, generation):
        with self.lock:
            if self.generation != generation:
                return
            position = self.position
            self.position = None
            self.timer = None
        if position is not None:
            try:
                save_window_position(*position)
            except OSError as error:
                print("Could not save window position: {}".format(error), file=sys.stderr)

    def flush(self):
        with self.lock:
            timer = self.timer
            position = self.position
            self.timer = None
            self.position = None
            self.generation += 1
            if timer is not None:
                timer.cancel()
        if position is not None:
            try:
                save_window_position(*position)
            except OSError as error:
                print("Could not save window position: {}".format(error), file=sys.stderr)


class FolderDialogApi:
    def __init__(self):
        self._window = None

    def select_folder(self, current_path=""):
        if self._window is None:
            raise RuntimeError("The application window is not ready.")

        directory = Path(current_path).expanduser() if current_path else DEFAULT_OUTPUT
        if not directory.is_dir():
            directory = DEFAULT_OUTPUT if DEFAULT_OUTPUT.is_dir() else ROOT

        selected = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=str(directory),
        )
        return selected[0] if selected else None


class DownloadManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    def start(self, link, destination_text):
        if not isinstance(link, str) or not link.strip():
            raise ValueError("Paste a MediaFire file or folder link.")
        if not isinstance(destination_text, str) or not destination_text.strip():
            raise ValueError("Enter a folder to save downloads to.")

        link = link.strip()
        parsed_links = analyze_mediafire.get_mediafire_links(link)
        if not any(parsed_links.values()):
            raise ValueError("That link was not recognized as a supported MediaFire link.")

        destination = Path(destination_text.strip()).expanduser()
        if not destination.is_absolute():
            destination = ROOT / destination
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if not destination.is_dir():
            raise ValueError("The download destination must be a folder.")

        with self.lock:
            if self.job and self.job["state"] == "running":
                raise RuntimeError("A download is already running. Wait for it to finish first.")

            job = {
                "id": secrets.token_urlsafe(18),
                "state": "running",
                "destination": str(destination),
                "lines": [],
                "line_offset": 0,
                "exit_code": None,
                "error": None,
                "process": None,
                "stop_requested": False,
                "size_estimate": {
                    "state": "estimating",
                    "total_bytes": None,
                    "file_count": 0,
                    "unknown_files": 0,
                },
                "progress": {
                    "total": 0,
                    "total_known": False,
                    "completed": 0,
                    "failed": 0,
                    "folders_pending": 0,
                    "folders_active": 0,
                    "phase": "estimating_size",
                },
                "network": {
                    "downloaded_bytes": 0,
                    "speed_bytes_per_second": 0.0,
                    "history": [],
                    "sampled_bytes": 0,
                    "sampled_at": time.monotonic(),
                },
            }
            self.job = job

        worker = threading.Thread(
            target=self._run_download,
            args=(job, link, destination),
            name="mediafire-download",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as error:
            message = "Could not start download worker: {}".format(error)
            with self.lock:
                job["error"] = message
                job["state"] = "failed"
                job["progress"]["phase"] = "failed"
            raise RuntimeError(message) from error
        return {"id": job["id"], "destination": str(destination)}

    def stop(self, job_id):
        with self.lock:
            job = self.job
            if job is None or job["id"] != job_id:
                raise KeyError("Download session not found.")
            if job["state"] != "running":
                raise RuntimeError("The download is not running.")
            job["stop_requested"] = True
            process = job.get("process")

        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            except OSError as error:
                if process.poll() is None:
                    with self.lock:
                        job["stop_requested"] = False
                    self._append_line(job, "Could not stop downloader process: {}".format(error))
                    raise RuntimeError(
                        "Could not stop the downloader process: {}".format(error)
                    ) from error

        return {"stopping": True}

    def _append_line(self, job, line):
        with self.lock:
            if self.job is not job:
                return
            job["lines"].append(line.rstrip("\r\n"))
            excess = len(job["lines"]) - MAX_LOG_LINES
            if excess > 0:
                del job["lines"][:excess]
                job["line_offset"] += excess

    def _apply_progress(self, job, event):
        progress = job["progress"]
        event_type = event.get("event")
        if event_type == "start":
            progress["total"] = max(0, int(event.get("total", 0)))
            progress["total_known"] = True
            progress["folders_pending"] = max(0, int(event.get("folders_pending", 0)))
            progress["phase"] = (
                "scanning" if progress["folders_pending"]
                else "downloading" if progress["total"]
                else "resolving"
            )
        elif event_type == "folder_start":
            progress["folders_pending"] = max(0, progress["folders_pending"] - 1)
            progress["folders_active"] += 1
            progress["phase"] = "scanning"
        elif event_type == "folder_done":
            progress["folders_active"] = max(0, progress["folders_active"] - 1)
            progress["folders_pending"] += max(0, int(event.get("child_folders", 0)))
            progress["total"] += max(0, int(event.get("file_count", 0)))
            if event.get("success") is False:
                progress["failed"] += 1
            progress["phase"] = (
                "scanning" if progress["folders_pending"] or progress["folders_active"]
                else "downloading"
            )
        elif event_type == "folder_skipped":
            progress["folders_pending"] = max(0, progress["folders_pending"] - 1)
            progress["phase"] = (
                "scanning" if progress["folders_pending"] or progress["folders_active"]
                else "downloading"
            )
        elif event_type == "file_done":
            progress["completed"] += 1
            if not event.get("success", False):
                progress["failed"] += 1
            if progress["folders_pending"] or progress["folders_active"]:
                progress["phase"] = "scanning"
            else:
                progress["phase"] = "downloading"
        elif event_type == "size_estimate":
            unknown_files = max(0, int(event.get("unknown_files", 0)))
            job["size_estimate"] = {
                "state": "partial" if unknown_files else "complete",
                "total_bytes": max(0, int(event.get("total_bytes", 0))),
                "file_count": max(0, int(event.get("file_count", 0))),
                "unknown_files": unknown_files,
            }
        elif event_type == "network_bytes":
            received = event.get("bytes")
            if isinstance(received, int) and not isinstance(received, bool) and received > 0:
                job["network"]["downloaded_bytes"] += received

    def _handle_output(self, job, line):
        if line.startswith(PROGRESS_PREFIX):
            try:
                event = json.loads(line[len(PROGRESS_PREFIX):])
                with self.lock:
                    if self.job is job:
                        self._apply_progress(job, event)
            except (TypeError, ValueError):
                self._append_line(job, "Invalid progress event: {}".format(line.strip()))
            return
        self._append_line(job, line)

    def _run_child_process(self, job, command, environment):
        with self.lock:
            if job.get("stop_requested", False):
                return None
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
            )
            job["process"] = process
        try:
            with process.stdout:
                for line in process.stdout:
                    self._handle_output(job, line)
            return process.wait()
        finally:
            with self.lock:
                if job.get("process") is process:
                    job["process"] = None

    def _run_download(self, job, link, destination):
        input_path = None
        try:
            with self.lock:
                if job.get("stop_requested", False):
                    job["state"] = "stopped"
                    job["progress"]["phase"] = "stopped"
                    return

            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".txt", delete=False
            ) as input_file:
                input_file.write(link + "\n")
                input_path = input_file.name

            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            downloader = [sys.executable, str(ROOT / "mfdl.py")]
            try:
                estimate_exit = self._run_child_process(
                    job,
                    downloader + [
                        "--estimate-size",
                        "--progress-events",
                        str(destination),
                        input_path,
                    ],
                    environment,
                )
            except Exception as error:
                self._append_line(job, "Could not estimate total download size: {}".format(error))
                estimate_exit = 1
            with self.lock:
                if job.get("stop_requested", False):
                    job["state"] = "stopped"
                    job["progress"]["phase"] = "stopped"
                    return
                if estimate_exit != 0 or job["size_estimate"]["state"] == "estimating":
                    job["size_estimate"]["state"] = "unavailable"
                job["progress"]["phase"] = "resolving"

            exit_code = self._run_child_process(
                job,
                downloader + ["--progress-events", str(destination), input_path],
                environment,
            )
            with self.lock:
                if job.get("stop_requested", False) and exit_code is None:
                    job["state"] = "stopped"
                    job["progress"]["phase"] = "stopped"
                    return
            if exit_code is None:
                raise RuntimeError("Downloader process did not start.")
            with self.lock:
                job["exit_code"] = exit_code
                if job.get("stop_requested", False):
                    job["state"] = "stopped"
                    job["progress"]["phase"] = "stopped"
                else:
                    job["state"] = "finished" if exit_code == 0 else "failed"
                    job["progress"]["phase"] = "finished" if exit_code == 0 else "failed"
        except Exception as error:
            with self.lock:
                if job.get("stop_requested", False):
                    job["state"] = "stopped"
                    job["progress"]["phase"] = "stopped"
                else:
                    job["error"] = str(error)
                    job["state"] = "failed"
                    job["progress"]["phase"] = "failed"
            if not job.get("stop_requested", False):
                self._append_line(job, "Downloader worker failed: {}".format(error))
        finally:
            if input_path and os.path.exists(input_path):
                try:
                    os.unlink(input_path)
                except OSError as error:
                    self._append_line(job, "Could not remove temporary input file: {}".format(error))

    def snapshot(self, job_id, after):
        with self.lock:
            job = self.job
            if job is None or job["id"] != job_id:
                raise KeyError("Download session not found.")

            now = time.monotonic()
            for metric, total_key in ((job["network"], "downloaded_bytes"),):
                elapsed = now - metric["sampled_at"]
                total = metric[total_key]
                byte_delta = total - metric["sampled_bytes"]
                if elapsed >= NETWORK_SAMPLE_INTERVAL and (
                    job["state"] == "running" or byte_delta > 0
                ):
                    speed = max(0, byte_delta / elapsed) if elapsed > 0 else 0
                    metric["speed_bytes_per_second"] = speed
                    metric["sampled_bytes"] = total
                    metric["sampled_at"] = now
                    metric["history"].append(speed)
                    del metric["history"][:-MAX_NETWORK_SAMPLES]

            offset = job["line_offset"]
            start = max(0, after - offset)
            return {
                "state": job["state"],
                "destination": job["destination"],
                "lines": job["lines"][start:],
                "next": offset + len(job["lines"]),
                "exit_code": job["exit_code"],
                "error": job["error"],
                "progress": dict(job["progress"]),
                "size_estimate": dict(job["size_estimate"]),
                "network": {
                    "downloaded_bytes": job["network"]["downloaded_bytes"],
                    "speed_bytes_per_second": job["network"]["speed_bytes_per_second"],
                    "history": list(job["network"]["history"]),
                },
            }

    def open_folder(self, job_id):
        with self.lock:
            job = self.job
            if job is None or job["id"] != job_id:
                raise KeyError("Download session not found.")
            if job["state"] != "finished":
                raise RuntimeError("The download has not finished successfully.")
            destination = Path(job["destination"])

        if not destination.is_dir():
            raise OSError("The download folder no longer exists.")
        os.startfile(str(destination))


DOWNLOADS = DownloadManager()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "mf-dl-local"
    sys_version = ""

    def _send(self, status, content, content_type):
        body = content if isinstance(content, bytes) else content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, data):
        self._send(status, json.dumps(data), "application/json; charset=utf-8")

    def _is_local_request(self, check_origin=False):
        expected_hosts = {
            "127.0.0.1:{}".format(self.server.server_port),
            "localhost:{}".format(self.server.server_port),
        }
        host = self.headers.get("Host", "").lower()
        if host not in expected_hosts:
            return False

        origin = self.headers.get("Origin")
        if check_origin and origin != "http://{}".format(host):
            return False
        return origin is None or origin == "http://{}".format(host)

    def do_GET(self):
        if not self._is_local_request():
            self._send_json(403, {"error": "Local requests only."})
            return

        request = urlsplit(self.path)
        if request.path == "/":
            self._send_file("loading.html", "text/html; charset=utf-8")
        elif request.path == "/app":
            self._send_file("index.html", "text/html; charset=utf-8")
        elif request.path == "/app-icon.png":
            try:
                content = (WEB_ROOT / "app-icon.png").read_bytes()
            except OSError:
                self._send_json(500, {"error": "Could not load the web interface."})
                return
            self._send(200, content, "image/png")
        elif request.path in (
            "/styles.css",
            "/app.js",
            "/loading.css",
            "/loading.js",
        ):
            asset = request.path.lstrip("/")
            content_type = (
                "text/css; charset=utf-8" if asset.endswith(".css")
                else "text/javascript; charset=utf-8"
            )
            self._send_file(asset, content_type)
        elif request.path == "/api/config":
            try:
                self._send_json(200, {
                    "default_destination": str(DEFAULT_OUTPUT),
                    "last_destination": load_last_destination(),
                })
            except (OSError, ValueError) as error:
                self._send_json(500, {"error": "Could not load app settings: {}".format(error)})
        elif request.path == "/api/job":
            parameters = parse_qs(request.query)
            job_id = parameters.get("id", [""])[0]
            try:
                after = max(0, int(parameters.get("after", ["0"])[0]))
                self._send_json(200, DOWNLOADS.snapshot(job_id, after))
            except ValueError:
                self._send_json(400, {"error": "Invalid log position."})
            except KeyError as error:
                self._send_json(404, {"error": str(error)})
        else:
            self._send_json(404, {"error": "Not found."})

    def _send_file(self, filename, content_type):
        try:
            content = (WEB_ROOT / filename).read_text(encoding="utf-8")
        except OSError:
            self._send_json(500, {"error": "Could not load the web interface."})
            return
        self._send(200, content, content_type)

    def do_POST(self):
        if not self._is_local_request(check_origin=True):
            self._send_json(403, {"error": "Local requests only."})
            return
        request_path = urlsplit(self.path).path
        if request_path not in (
            "/api/download",
            "/api/stop",
            "/api/open-folder",
            "/api/config",
        ):
            self._send_json(404, {"error": "Not found."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send_json(415, {"error": "Expected a JSON request."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid request size."})
            return
        if length <= 0 or length > MAX_REQUEST_SIZE:
            self._send_json(413, {"error": "Request is empty or too large."})
            return

        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid request body.")
            if request_path == "/api/stop":
                job_id = payload.get("id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("Invalid download session.")
                result = DOWNLOADS.stop(job_id)
                self._send_json(200, result)
                return
            if request_path == "/api/open-folder":
                job_id = payload.get("id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("Invalid download session.")
                DOWNLOADS.open_folder(job_id)
                self._send_json(200, {"opened": True})
                return
            if request_path == "/api/config":
                destination = save_last_destination(payload.get("destination"))
                self._send_json(200, {"last_destination": destination})
                return
            result = DOWNLOADS.start(payload.get("link"), payload.get("destination"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Invalid JSON request."})
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self._send_json(409, {"error": str(error)})
        except KeyError as error:
            self._send_json(404, {"error": str(error)})
        except OSError as error:
            status = 500 if request_path == "/api/open-folder" else 400
            self._send_json(status, {"error": str(error)})
        else:
            self._send_json(202, result)

    def log_message(self, format_string, *args):
        print("{} - {}".format(self.log_date_time_string(), format_string % args))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
    server.daemon_threads = True
    address = "http://127.0.0.1:{}".format(server.server_port)
    folder_dialog_api = FolderDialogApi()
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="mediafire-web-server",
        daemon=True,
    )
    server_thread.start()
    try:
        first_launch = not app_has_launched()
        position = load_window_position()
        window = webview.create_window(
            "MediaFire Downloader",
            address,
            js_api=folder_dialog_api,
            width=1100,
            height=800,
            x=position[0] if position and not first_launch else None,
            y=position[1] if position and not first_launch else None,
            min_size=(720, 600),
            maximized=first_launch,
        )
        if window is None:
            raise RuntimeError("Could not create the application window.")
        mark_app_launched()
        folder_dialog_api._window = window
        position_saver = WindowPositionSaver()
        window.events.moved += position_saver.moved
        window.events.closing += position_saver.flush
        webview.start(icon=str(WEB_ROOT / "app-icon.ico"))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
