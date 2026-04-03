#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web file transfer system - core module.
Provides a web UI over rsync-based transfers.
"""

from flask import request
import paramiko
import threading
import os
import json
import time
import subprocess
import re
import asyncio
import concurrent.futures
import random
from datetime import datetime
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import shutil
import shlex
import uuid
import signal
import select
import pty
import fcntl
import termios
import struct
from difflib import SequenceMatcher

from .extensions import socketio

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
CONFIG_FILE = os.path.join(BASE_DIR, 'data', 'config.json')

def load_config():
    """Load config from data/config.json; raise if missing or invalid."""
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_FILE}")
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("配置文件格式无效，顶层应为对象")
    return cfg


def _normalize_servers(raw_servers):
    """Normalize configured servers into a stable server-id -> meta mapping."""
    if not isinstance(raw_servers, dict) or not raw_servers:
        raise RuntimeError("配置缺少 servers 列表")

    normalized = {}
    for server_id, server_cfg in raw_servers.items():
        if not isinstance(server_cfg, dict):
            raise ValueError(f"服务器配置格式无效: {server_id}")
        entry = dict(server_cfg)
        entry["host"] = str(entry.get("host") or server_id).strip() or str(server_id)
        if not entry.get("name"):
            entry["name"] = str(server_id)
        normalized[str(server_id)] = entry
    return normalized

CONFIG = load_config()

secret_key = CONFIG.get('secret_key')
if not secret_key:
    raise RuntimeError("配置缺少 secret_key")

TURBOFILE_HOST_IP = CONFIG.get('host_ip') or ''
if not TURBOFILE_HOST_IP:
    raise RuntimeError("配置缺少 host_ip")

SERVERS = _normalize_servers(CONFIG.get('servers'))
CONFIG['servers'] = SERVERS

ADMIN_MODE_ENABLED = bool(CONFIG.get('admin_mode_enabled'))
ADMIN_CLIENT_IPS = set(CONFIG.get('admin_client_ips') or [])

def extract_client_ipv4_from_request(req) -> str:
    """
    Extract client IPv4 from the request, preferring proxy headers for consistency.
    Return an empty string if unavailable.
    """
    try:
        def _extract_ipv4(s: str):
            if not s:
                return None
            first = s.split(',')[0].strip()
            m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', first)
            return m.group(1) if m else None

        candidates = [
            req.headers.get('X-Forwarded-For', ''),
            req.headers.get('X-Real-IP', ''),
            req.remote_addr
        ]
        for c in candidates:
            ip = _extract_ipv4(c)
            if ip:
                return ip
    except Exception:
        pass
    return ''

def is_admin_client_ip(ip: str) -> bool:
    try:
        return bool(ADMIN_MODE_ENABLED and ip and ip in ADMIN_CLIENT_IPS)
    except Exception:
        return False


def get_server_host(server_ip: str) -> str:
    """Return the real host/IP used to connect to a configured server."""
    try:
        server_cfg = SERVERS.get(server_ip) or {}
        host = str(server_cfg.get('host') or server_ip).strip()
        return host or str(server_ip)
    except Exception:
        return str(server_ip)


def build_remote_spec(server_ip: str, user: str, remote_path: str) -> str:
    """Build rsync/scp style user@host:path spec from server id + configured host."""
    return f'{user}@{get_server_host(server_ip)}:{remote_path}'


def get_server_visible_client_ips(server_ip: str):
    """Return a normalized client-IP allowlist for a server; empty means unrestricted."""
    try:
        server = SERVERS.get(server_ip) or {}
        raw = server.get('visible_client_ips')
        if not raw:
            return set()
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple, set)):
            return {str(ip).strip() for ip in raw if str(ip).strip()}
    except Exception:
        pass
    return set()


def is_server_visible_to_client(server_ip: str, client_ip: str) -> bool:
    """Return whether the given configured server should be visible to a client IP."""
    try:
        if server_ip not in SERVERS:
            return False
        allowed_client_ips = get_server_visible_client_ips(server_ip)
        if not allowed_client_ips:
            return True
        return bool(client_ip and client_ip in allowed_client_ips)
    except Exception:
        return False


def get_visible_servers_for_client(client_ip: str):
    """Return the configured server map filtered by client-IP visibility rules."""
    try:
        return {
            server_ip: server_cfg
            for server_ip, server_cfg in SERVERS.items()
            if is_server_visible_to_client(server_ip, client_ip)
        }
    except Exception:
        return {}



@lru_cache(maxsize=1)
def get_current_host_ip():
    """Get the current host IP address."""
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return TURBOFILE_HOST_IP

def determine_transfer_mode(source_server, target_server):
    """
    Determine transfer mode; any server can be a source.

    Returns:
    - 'local_to_remote': from TurboFile host to remote
    - 'remote_to_remote': from remote to another remote
    - 'remote_to_local': from remote to TurboFile host
    """
    is_source_local = is_local_server(source_server)
    is_target_local = is_local_server(target_server)

    if is_source_local and not is_target_local:
        return 'local_to_remote'
    elif not is_source_local and is_target_local:
        return 'remote_to_local'
    elif not is_source_local and not is_target_local:
        return 'remote_to_remote'
    else:

        return 'local_to_local'

def is_local_server(server_ip):
    """Return whether the server is the local TurboFile host."""
    current_host = get_current_host_ip()
    local_aliases = {"localhost", "127.0.0.1", current_host, TURBOFILE_HOST_IP}
    resolved_host = get_server_host(server_ip)
    return server_ip in local_aliases or resolved_host in local_aliases


def load_client_paths():
    global client_paths_cache
    if client_paths_cache:
        return client_paths_cache
    try:
        if not os.path.exists(CLIENT_PATH_FILE):
            os.makedirs(os.path.dirname(CLIENT_PATH_FILE), exist_ok=True)
            client_paths_cache = {}
            return client_paths_cache
        with open(CLIENT_PATH_FILE, 'r', encoding='utf-8') as f:
            text = f.read().strip()
            client_paths_cache = json.loads(text) if text else {}
    except Exception:
        client_paths_cache = {}
    return client_paths_cache

def save_client_paths():
    os.makedirs(os.path.dirname(CLIENT_PATH_FILE), exist_ok=True)
    tmp = CLIENT_PATH_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(client_paths_cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CLIENT_PATH_FILE)

def remember_path(client_ip: str, panel: str, server: str, path: str):
    if not client_ip or not panel or not server or not path:
        return
    with CLIENT_PATH_LOCK:
        load_client_paths()
        client_paths_cache.setdefault(client_ip, {})
        client_paths_cache[client_ip][panel] = {'server': server, 'path': path}
        save_client_paths()


ssh_connections = {}
active_transfers = {}
transfer_processes = {}
TRANSFER_PROCESS_LOCK = threading.Lock()

def register_transfer_process(transfer_id: str, proc_info: dict) -> None:
    """Register a transfer process/channel (supports multiple parallel children)."""
    if not transfer_id or not isinstance(proc_info, dict):
        return
    with TRANSFER_PROCESS_LOCK:
        bucket = transfer_processes.get(transfer_id)
        if bucket is None:
            transfer_processes[transfer_id] = [proc_info]
        elif isinstance(bucket, list):
            bucket.append(proc_info)
        elif isinstance(bucket, dict):
            transfer_processes[transfer_id] = [bucket, proc_info]
        else:
            transfer_processes[transfer_id] = [proc_info]

def get_transfer_processes_snapshot(transfer_id: str):
    """Get a snapshot of transfer processes/channels to avoid concurrent mutation."""
    with TRANSFER_PROCESS_LOCK:
        bucket = transfer_processes.get(transfer_id)
        if bucket is None:
            return []
        if isinstance(bucket, list):
            return list(bucket)
        if isinstance(bucket, dict):
            return [bucket]
        return []
CLIENT_ROOMS = {}
CLIENT_PATH_LOCK = threading.Lock()
CLIENT_PATH_FILE = os.path.join(BASE_DIR, 'data', 'client_paths.json')
client_paths_cache = {}


TRANSFER_WATCHDOG_INTERVAL = 60
STALE_TRANSFER_TIMEOUT = 12 * 3600


PARALLEL_TRANSFER_CONFIG = {
    'max_workers': 8,
    'enable_parallel': True,
    'instant_start': True,
    'enable_folder_parallel': False,
    'folder_parallel_threshold': 1000,
    'enable_batch_transfer': True,
    'batch_max_files': 200
}


PERFORMANCE_CONFIG = {
    'speed_update_interval': 1,
    'progress_update_interval': 0.3,
    'disable_progress_monitoring': True,
    'reduce_websocket_traffic': True,
    'optimize_rsync_params': True
}


def _load_transfer_bytes_config():
    config = {
        'enabled': True,
        'update_interval': 2.0
    }
    raw = CONFIG.get('transfer_bytes_config')
    if not isinstance(raw, dict):
        return config
    enabled = raw.get('enabled')
    if isinstance(enabled, bool):
        config['enabled'] = enabled
    elif isinstance(enabled, int):
        config['enabled'] = bool(enabled)
    update_interval = raw.get('update_interval')
    if update_interval is not None:
        try:
            update_interval = float(update_interval)
            if update_interval > 0:
                config['update_interval'] = update_interval
        except (TypeError, ValueError):
            pass
    return config

TRANSFER_BYTES_CONFIG = _load_transfer_bytes_config()





RSYNC_SSH_CMD = "ssh -o Compression=no -o Ciphers=aes128-ctr -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o MACs=umac-64@openssh.com -o ControlMaster=auto -o ControlPersist=300 -o ControlPath=/tmp/turbofile-ssh-%r@%h:%p"


UI_LOG_FILTER_CONFIG = {
    'enabled': True,
    'skip_patterns': [
        '🚀 开始',
        '🔄 传输模式',
        '🔧 调试',
        '📝 执行命令',
        '📁 正在分析',
        '⚡ 快速模式',
        '⚡ 启动',
        '📊 并行任务',
        '✅ 并行任务完成',
        '🎉 目录并行',
        '⚠️ 目录',
        '📁 启用目录',
        '🔁 检测到Windows',
        '✂️',
        '📁 本地到本地',
        '🪟 Windows',
        '🐧 Linux',
        '⚡️ 开始传输',
        '正在传输',
        '✅ 本地剪切完成',
        '✅ 本地复制完成',
        '✅ 同服务器剪切完成',
        '✅ 同服务器复制完成',
    ]
}

def should_emit_to_ui(message):
    """Decide whether to show the log message in the UI."""
    if not UI_LOG_FILTER_CONFIG['enabled']:
        return True


    for pattern in UI_LOG_FILTER_CONFIG['skip_patterns']:
        if pattern in message:
            return False


    return True

def emit_transfer_log(transfer_id, message):
    """Send transfer logs to the UI (with filtering)."""
    if should_emit_to_ui(message):
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': message
        })

TRANSFER_BYTES_STATE = {}
TRANSFER_BYTES_LOCK = threading.Lock()
RSYNC_PROGRESS_BYTES_RE = re.compile(r'^\s*([0-9][0-9,]*)\s+\d+%')

def init_transfer_bytes(transfer_id):
    if not transfer_id:
        return
    with TRANSFER_BYTES_LOCK:
        TRANSFER_BYTES_STATE[transfer_id] = {
            'parts': {},
            'completed_total': 0
        }

def cleanup_transfer_bytes(transfer_id):
    if not transfer_id:
        return
    with TRANSFER_BYTES_LOCK:
        TRANSFER_BYTES_STATE.pop(transfer_id, None)

def update_transfer_bytes_part(transfer_id, part_id, bytes_val):
    if not transfer_id or not part_id:
        return
    try:
        bytes_val = int(bytes_val)
    except Exception:
        return
    with TRANSFER_BYTES_LOCK:
        state = TRANSFER_BYTES_STATE.setdefault(transfer_id, {'parts': {}, 'completed_total': 0})
        current = state['parts'].get(part_id)
        if current is None or bytes_val > current:
            state['parts'][part_id] = bytes_val

def finalize_transfer_bytes_part(transfer_id, part_id, final_bytes=None):
    if not transfer_id or not part_id:
        return
    with TRANSFER_BYTES_LOCK:
        state = TRANSFER_BYTES_STATE.setdefault(transfer_id, {'parts': {}, 'completed_total': 0})
        part_val = None
        if part_id in state['parts']:
            part_val = state['parts'].pop(part_id)
        if final_bytes is not None:
            try:
                final_bytes = int(final_bytes)
                if part_val is None or final_bytes > part_val:
                    part_val = final_bytes
            except Exception:
                pass
        if part_val is not None:
            state['completed_total'] += part_val

def get_transfer_bytes_total(transfer_id):
    if not transfer_id:
        return 0
    with TRANSFER_BYTES_LOCK:
        state = TRANSFER_BYTES_STATE.get(transfer_id)
        if not state:
            return 0
        return state.get('completed_total', 0) + sum(state.get('parts', {}).values())

def emit_transfer_bytes_snapshot(transfer_id):
    if not TRANSFER_BYTES_CONFIG.get('enabled', True):
        return
    total = get_transfer_bytes_total(transfer_id)
    socketio.emit('speed_update', {
        'transfer_id': transfer_id,
        'transferred_bytes': total,
        'transferred_human': _human_readable_size(total)
    })

def _parse_rsync_progress_bytes(text):
    if not text:
        return None
    match = RSYNC_PROGRESS_BYTES_RE.match(text.strip())
    if not match:
        return None
    try:
        return int(match.group(1).replace(',', ''))
    except Exception:
        return None

def _consume_progress_text(buffer, text, transfer_id, part_id):
    if not text:
        return buffer
    buffer = (buffer or '') + text
    while True:
        idx_r = buffer.find('\r')
        idx_n = buffer.find('\n')
        idx = idx_r if idx_n == -1 else (idx_n if idx_r == -1 else min(idx_r, idx_n))
        if idx == -1:
            break
        line = buffer[:idx]
        buffer = buffer[idx + 1:]
        bytes_val = _parse_rsync_progress_bytes(line)
        if bytes_val is not None:
            update_transfer_bytes_part(transfer_id, part_id, bytes_val)
    if len(buffer) > 8192:
        buffer = buffer[-8192:]
    return buffer

def _append_rsync_progress_opts(rsync_opts):
    if TRANSFER_BYTES_CONFIG.get('enabled', True) and '--info=progress2' not in rsync_opts:
        rsync_opts.append('--info=progress2')

def _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id):
    import subprocess
    import os
    import signal

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        preexec_fn=os.setsid
    )

    register_transfer_process(transfer_id, {
        'type': 'subprocess',
        'process': process
    })

    buffer = ''
    try:
        stdout = process.stdout
        while True:
            if stdout is None:
                break
            try:
                ready, _, _ = select.select([stdout], [], [], 0.2)
            except Exception:
                ready = []
            if ready:
                try:
                    chunk = os.read(stdout.fileno(), 4096)
                except Exception:
                    chunk = b''
                if chunk:
                    buffer = _consume_progress_text(buffer, chunk.decode('utf-8', errors='ignore'), transfer_id, part_id)
                else:
                    if process.poll() is not None:
                        break
            if process.poll() is not None:

                try:
                    while True:
                        chunk = os.read(stdout.fileno(), 4096)
                        if not chunk:
                            break
                        buffer = _consume_progress_text(buffer, chunk.decode('utf-8', errors='ignore'), transfer_id, part_id)
                except Exception:
                    pass
                break
        return_code = process.wait()
    except KeyboardInterrupt:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
            except Exception:
                pass
        raise Exception("传输被用户取消")
    finally:
        finalize_transfer_bytes_part(transfer_id, part_id)

    return return_code

def _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id):
    stdin, stdout, stderr = ssh.exec_command(remote_cmd)
    register_transfer_process(transfer_id, {'type': 'ssh', 'channel': stdout.channel})

    channel = stdout.channel
    buffer = ''
    err_buf = ''
    max_err = 8192

    while True:
        if channel.recv_ready():
            chunk = channel.recv(4096)
            if chunk:
                buffer = _consume_progress_text(buffer, chunk.decode('utf-8', errors='ignore'), transfer_id, part_id)
        if channel.recv_stderr_ready():
            chunk = channel.recv_stderr(4096)
            if chunk:
                err_buf += chunk.decode('utf-8', errors='ignore')
                if len(err_buf) > max_err:
                    err_buf = err_buf[-max_err:]
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        time.sleep(0.1)

    exit_status = channel.recv_exit_status()
    finalize_transfer_bytes_part(transfer_id, part_id)
    return exit_status, err_buf


LOG_FILE_PATH = os.path.join(BASE_DIR, 'transfer.log')
_log_file_lock = threading.Lock()
LOG_MAX_LINES = 10000

def _count_log_lines(path: str) -> int:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

def clear_log_if_too_large(max_lines: int = None) -> bool:
    limit = max_lines if isinstance(max_lines, int) and max_lines > 0 else LOG_MAX_LINES
    try:
        with _log_file_lock:
            if _count_log_lines(LOG_FILE_PATH) >= limit:
                with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
                    f.write('')
                return True
    except Exception:
        pass
    return False

def _normalize_ip_for_log(server_ip: str) -> str:
    """Normalize local aliases to the real host IP; leave others unchanged."""
    try:
        return TURBOFILE_HOST_IP if is_local_server(server_ip) else get_server_host(server_ip)
    except Exception:
        return server_ip


def _join_target_full_path_for_log(target_server: str, base_path: str, name: str) -> str:
    """Build the full target path by server type (Windows/POSIX)."""
    try:
        if is_windows_server(target_server):
            import ntpath
            return ntpath.join(base_path, name)
        else:
            base = base_path.rstrip('/\\')
            return f"{base}/{name}"
    except Exception:

        return f"{base_path}/{name}"

def _get_client_ip() -> str:
    """Extract client IP (proxy-aware)."""
    try:
        import re as _re
        def _extract_ipv4(s: str):
            if not s:
                return None
            first = s.split(',')[0].strip()
            m = _re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', first)
            return m.group(1) if m else None

        candidates = [
            request.headers.get('X-Forwarded-For', ''),
            request.headers.get('X-Real-IP', ''),
            request.remote_addr
        ]
        for c in candidates:
            ip = _extract_ipv4(c)
            if ip:
                return ip
    except Exception:
        pass
    return '未知'

def _hhmmss_to_seconds(time_str: str) -> float:
    try:
        parts = str(time_str).split(':')
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def append_transfer_log_record(source_ip: str,
                               target_ip: str,
                               source_path: str,
                               target_full_path: str,
                               duration_sec: float,
                               status: str,
                               error: str = "",
                               client_ip: str = "",
                               mode: str = "",
                               file_name: str = "",
                               action: str = "transfer") -> None:
    """Write a transfer record as a log line.
    Fields: timestamp, action, client_ip, source_ip, target_ip, source_path, target_path, file, mode, duration_sec, status, error
    """
    record = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': action or 'transfer',
        'client_ip': client_ip or '未知',
        'source_ip': _normalize_ip_for_log(source_ip),
        'target_ip': _normalize_ip_for_log(target_ip),
        'source_path': source_path,
        'target_path': target_full_path,
        'file': file_name or '',
        'mode': mode or '',
        'duration_sec': round(float(duration_sec), 3),
        'status': 'success' if status.lower() == 'success' else 'failure'
    }
    if error:
        record['error'] = str(error)

    line = json.dumps(record, ensure_ascii=False)
    try:
        with _log_file_lock:
            with open(LOG_FILE_PATH, 'a', encoding='utf-8') as f:
                f.write(line + "\n")
    except Exception as _:

        pass


class SpeedSimulator:
    def __init__(self):
        self.transfer_speeds = {}
        self.lock = threading.Lock()

    def init_transfer_speed(self, transfer_id, min_speed: float = 110.0, max_speed: float = 114.0):
        """Initialize transfer speed; allow scenario-specific ranges."""
        with self.lock:

            initial_speed = random.uniform(min_speed, max_speed)
            self.transfer_speeds[transfer_id] = {
                'current_speed': initial_speed,
                'last_update': time.time(),
                'trend': random.choice(['up', 'down', 'stable']),
                'trend_duration': 0,
                'min_speed': min_speed,
                'max_speed': max_speed
            }

    def get_simulated_speed(self, transfer_id):
        """Get simulated transfer speed; supports per-transfer ranges."""
        with self.lock:
            if transfer_id not in self.transfer_speeds:
                self.init_transfer_speed(transfer_id)

            speed_data = self.transfer_speeds[transfer_id]
            current_time = time.time()


            min_s = speed_data.get('min_speed', 110.0)
            max_s = speed_data.get('max_speed', 114.0)
            width = max(0.1, max_s - min_s)
            edge = max(0.2, 0.25 * width)


            if current_time - speed_data['last_update'] >= 0.1:
                speed_data['last_update'] = current_time
                speed_data['trend_duration'] += 1


                if speed_data['trend_duration'] >= 20:
                    speed_data['trend'] = random.choice(['up', 'down', 'stable'])
                    speed_data['trend_duration'] = 0

                current_speed = speed_data['current_speed']

                if speed_data['trend'] == 'up':
                    change = random.uniform(0.05 * width, 0.15 * width)
                    new_speed = min(max_s, current_speed + change)
                    if new_speed >= max_s - edge:
                        speed_data['trend'] = 'down'
                elif speed_data['trend'] == 'down':
                    change = random.uniform(0.05 * width, 0.15 * width)
                    new_speed = max(min_s, current_speed - change)
                    if new_speed <= min_s + edge:
                        speed_data['trend'] = 'up'
                else:  # stable
                    change = random.uniform(-0.05 * width, 0.05 * width)
                    new_speed = max(min_s, min(max_s, current_speed + change))

                speed_data['current_speed'] = new_speed

            return f"{speed_data['current_speed']:.1f} MB/s"

    def cleanup_transfer(self, transfer_id):
        """Clear transfer speed data."""
        with self.lock:
            if transfer_id in self.transfer_speeds:
                del self.transfer_speeds[transfer_id]


speed_simulator = SpeedSimulator()


class TransferTimeTracker:
    def __init__(self):
        self.transfer_start_times = {}
        self.lock = threading.Lock()

    def start_transfer(self, transfer_id):
        """Start transfer timing."""
        with self.lock:
            self.transfer_start_times[transfer_id] = time.time()

    def get_elapsed_time(self, transfer_id):
        """Get elapsed time."""
        with self.lock:
            if transfer_id in self.transfer_start_times:
                elapsed = time.time() - self.transfer_start_times[transfer_id]
                return self.format_time(elapsed)
            return "00:00:00"

    def end_transfer(self, transfer_id):
        """Stop transfer timing."""
        with self.lock:
            if transfer_id in self.transfer_start_times:
                elapsed = time.time() - self.transfer_start_times[transfer_id]
                del self.transfer_start_times[transfer_id]
                return self.format_time(elapsed)
            return "00:00:00"

    def format_time(self, seconds):
        """Format elapsed time."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


time_tracker = TransferTimeTracker()


class ProgressManager:
    def __init__(self):
        self.transfer_progress = {}
        self.progress_lock = threading.Lock()

    def init_transfer(self, transfer_id, total_files, total_bytes=0):
        """Initialize transfer progress."""
        with self.progress_lock:
            self.transfer_progress[transfer_id] = {
                'total_files': total_files,
                'completed_files': 0,
                'failed_files': 0,
                'total_bytes': total_bytes,
                'transferred_bytes': 0,
                'file_progress': {},
                'last_update_time': time.time()
            }

    def update_file_progress(self, transfer_id, file_name, percentage, bytes_transferred=0, speed=''):
        """Update progress for a single file."""
        with self.progress_lock:
            if transfer_id not in self.transfer_progress:
                return

            progress = self.transfer_progress[transfer_id]
            progress['file_progress'][file_name] = {
                'percentage': percentage,
                'bytes_transferred': bytes_transferred,
                'speed': speed
            }


            completed_files = progress['completed_files']
            total_files = progress['total_files']


            current_file_contribution = 0
            for fname, fprogress in progress['file_progress'].items():
                if fprogress['percentage'] < 100:
                    current_file_contribution += fprogress['percentage'] / 100

            overall_percentage = int(((completed_files + current_file_contribution) / total_files) * 100)
            overall_percentage = min(100, max(0, overall_percentage))


            current_time = time.time()
            if current_time - progress['last_update_time'] >= 0.5:
                progress['last_update_time'] = current_time


                simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
                elapsed_time = time_tracker.get_elapsed_time(transfer_id)


                pass

    def complete_file(self, transfer_id, file_name, success=True):
        """Mark file transfer complete."""
        with self.progress_lock:
            if transfer_id not in self.transfer_progress:
                return

            progress = self.transfer_progress[transfer_id]
            if success:
                progress['completed_files'] += 1
            else:
                progress['failed_files'] += 1


            if file_name in progress['file_progress']:
                del progress['file_progress'][file_name]


            pass

    def cleanup_transfer(self, transfer_id):
        """Clear transfer progress records."""
        with self.progress_lock:
            if transfer_id in self.transfer_progress:
                del self.transfer_progress[transfer_id]

progress_manager = ProgressManager()

def _is_transfer_process_active(proc_info):
    """Check whether a recorded transfer process is still running."""
    try:
        if not proc_info:
            return False

        if isinstance(proc_info, list):
            return any(_is_transfer_process_active(p) for p in proc_info)
        ptype = proc_info.get('type')
        if ptype == 'subprocess':
            proc = proc_info.get('process')
            return proc is not None and proc.poll() is None
        if ptype == 'ssh':
            ch = proc_info.get('channel')
            return ch is not None and not ch.exit_status_ready()
    except Exception:
        return False
    return False


def _cleanup_transfer_state(transfer_id):
    """Clean transfer state to avoid zombie tasks."""
    if transfer_id in active_transfers:
        del active_transfers[transfer_id]
    with TRANSFER_PROCESS_LOCK:
        transfer_processes.pop(transfer_id, None)
    cleanup_transfer_bytes(transfer_id)
    progress_manager.cleanup_transfer(transfer_id)
    speed_simulator.cleanup_transfer(transfer_id)
    cleanup_transfer_bytes(transfer_id)


def start_transfer_watchdog():
    """Background cleaner: purge timed-out tasks with no active processes."""
    def watchdog():
        while True:
            try:
                time.sleep(TRANSFER_WATCHDOG_INTERVAL)
                now = datetime.now()
                stale_ids = []
                for tid, meta in list(active_transfers.items()):
                    start_ts = meta.get('start_time')
                    try:
                        age = (now - start_ts).total_seconds() if isinstance(start_ts, datetime) else max(0, time.time() - float(start_ts))
                    except Exception:
                        age = 0

                    if age < STALE_TRANSFER_TIMEOUT:
                        continue

                    proc_info = get_transfer_processes_snapshot(tid)
                    if proc_info and _is_transfer_process_active(proc_info):

                        continue


                    stale_ids.append(tid)

                for tid in stale_ids:
                    print(f"[WATCHDOG] 清理疑似僵尸传输任务: {tid}")
                    _cleanup_transfer_state(tid)
            except Exception as e:
                print(f"[WATCHDOG] 传输清理器异常: {e}")
                continue

    t = threading.Thread(target=watchdog, daemon=True)
    t.start()

start_transfer_watchdog()

class SSHManager:
    def __init__(self):
        self.connections = {}
        self.connection_pool_size = 3
        self.connection_pools = {}

    def get_connection(self, server_ip):
        """Get an SSH connection using the pool."""

        if server_ip not in self.connection_pools:
            self.connection_pools[server_ip] = []


        pool = self.connection_pools[server_ip]
        for i, ssh in enumerate(pool):
            if ssh and ssh.get_transport() and ssh.get_transport().is_active():
                try:
                    ssh.get_transport().set_keepalive(TERMINAL_SSH_KEEPALIVE_SECONDS)
                except Exception:
                    pass

                pool.append(pool.pop(i))
                return ssh
            else:

                if ssh:
                    try:
                        ssh.close()
                    except:
                        pass
                pool.remove(ssh)


        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            server_config = SERVERS[server_ip]


            connect_kwargs = {
                'hostname': get_server_host(server_ip),
                'username': server_config["user"],
                'port': server_config.get("port", 22),
                'timeout': 5,
                'compress': False,
                'look_for_keys': True,
                'allow_agent': True,
                'sock': None,
                'gss_auth': False,
                'gss_kex': False,
                'gss_deleg_creds': False,
                'gss_host': None,
                'banner_timeout': 5,
                'auth_timeout': 5,
                'channel_timeout': 5
            }


            try:
                ssh.connect(**connect_kwargs)
                print(f"✅ 使用密钥连接到服务器 {server_ip}")
            except:

                connect_kwargs['password'] = server_config["password"]
                ssh.connect(**connect_kwargs)
                print(f"✅ 使用密码连接到服务器 {server_ip}")

            try:
                transport = ssh.get_transport()
                if transport:
                    transport.set_keepalive(TERMINAL_SSH_KEEPALIVE_SECONDS)
            except Exception:
                pass


            if len(pool) >= self.connection_pool_size:

                old_ssh = pool.pop(0)
                try:
                    old_ssh.close()
                except:
                    pass

            pool.append(ssh)
            return ssh

        except Exception as e:
            print(f"❌ 连接服务器 {server_ip} 失败: {e}")
            return None

    def execute_command(self, server_ip, command):
        """Execute a remote command and return (stdout, stderr, exit_code)."""
        ssh = self.get_connection(server_ip)
        if not ssh:
            return None, f"无法连接到服务器 {server_ip}", -1


        is_win = is_windows_server(server_ip)
        encoding = 'gbk' if is_win else 'utf-8'

        try:
            stdin, stdout, stderr = ssh.exec_command(command)

            output = stdout.read().decode(encoding, errors='ignore')
            error = stderr.read().decode(encoding, errors='ignore')
            try:
                exit_code = stdout.channel.recv_exit_status()
            except Exception:
                exit_code = 0 if not error else 1
            return output, error, exit_code
        except Exception as e:

            print(f"⚠️  SSH连接异常，尝试重新连接到 {server_ip}: {e}")
            if server_ip in self.connections:
                try:
                    self.connections[server_ip].close()
                except:
                    pass
                del self.connections[server_ip]


            ssh = self.get_connection(server_ip)
            if ssh:
                try:
                    stdin, stdout, stderr = ssh.exec_command(command)
                    output = stdout.read().decode(encoding, errors='ignore')
                    error = stderr.read().decode(encoding, errors='ignore')
                    try:
                        exit_code = stdout.channel.recv_exit_status()
                    except Exception:
                        exit_code = 0 if not error else 1
                    return output, error, exit_code
                except Exception as retry_e:
                    return None, f"重连后仍然失败: {str(retry_e)}", -1

            return None, str(e), -1

ssh_manager = SSHManager()
RUN_TASKS = {}
RUN_TASKS_LOCK = threading.Lock()
TERMINAL_TASKS = {}
TERMINAL_TASKS_LOCK = threading.Lock()
TERMINAL_DETACH_GRACE_SECONDS = 43200
TERMINAL_REAPER_INTERVAL_SECONDS = 30
TERMINAL_SSH_KEEPALIVE_SECONDS = 15
TERMINAL_LOCAL_POLL_INTERVAL_SECONDS = 0.015
TERMINAL_REMOTE_IDLE_SLEEP_SECONDS = 0.01
_TERMINAL_REAPER_STARTED = False
_TERMINAL_REAPER_LOCK = threading.Lock()

TERMINAL_PROFILE_OPTIONS_POSIX = (
    {'id': 'bash', 'label': 'Bash'},
    {'id': 'login', 'label': '登录 Shell'},
    {'id': 'sh', 'label': 'Sh'},
)

TERMINAL_PROFILE_OPTIONS_WINDOWS = (
    {'id': 'powershell', 'label': 'PowerShell'},
    {'id': 'cmd', 'label': 'CMD'},
)

def get_ssh_command_with_port(server_ip, fast_ssh=True):
    """Build an SSH command string with custom port support."""
    server_config = SERVERS[server_ip]
    port = server_config.get("port", 22)

    ssh_cmd_parts = [
        "ssh",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "PasswordAuthentication=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "TCPKeepAlive=yes",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath=/tmp/ssh-%r@%h:{port}",
        "-o", "ControlPersist=300"
    ]

    if fast_ssh:
        ssh_cmd_parts.extend([
            "-o", "Compression=no",
            "-o", "Ciphers=aes128-ctr",
            "-o", "MACs=umac-64@openssh.com"
        ])

    return " ".join(ssh_cmd_parts)

def is_windows_server(server_ip):
    """Return whether the server is Windows."""
    server_config = SERVERS.get(server_ip, {})
    is_windows = server_config.get("os_type") == "windows"
    print(f"🔍 检查是否为Windows服务器: {server_ip} -> {is_windows}")
    return is_windows

def convert_windows_path_to_cygwin(windows_path):
    """Convert Windows path to Cygwin format.
    Example: C:\\Users\\warrior\\Documents -> /cygdrive/c/Users/warrior/Documents
    """
    import re

    match = re.match(r'^([A-Za-z]):[/\\](.*)$', windows_path)
    if match:
        drive = match.group(1).lower()
        path = match.group(2).replace('\\', '/')
        return f"/cygdrive/{drive}/{path}"

    return windows_path.replace('\\', '/')

def convert_cygwin_path_to_windows(cygwin_path):
    """Convert Cygwin path to Windows format.
    Example: /cygdrive/c/Users/warrior/Documents -> C:/Users/warrior/Documents
    """
    import re
    match = re.match(r'^/cygdrive/([a-z])/(.*)$', cygwin_path)
    if match:
        drive = match.group(1).upper()
        path = match.group(2)
        return f"{drive}:/{path}"
    return cygwin_path


def normalize_windows_path_for_transfer(p: str) -> str:
    try:
        if not p:
            return p
        s = p.replace('\\', '/')
        import re

        if s.startswith('/') and re.match(r'^/[A-Za-z]:/?$', s):
            s = s[1:]

        if re.match(r'^[A-Za-z]:$', s):
            s = s + '/'
        return s
    except Exception:
        return p


def normalize_windows_path_for_cmd(p: str) -> str:
    """Convert a path to Windows CMD format (backslashes)."""
    try:
        if not p:
            return p

        s = normalize_windows_path_for_transfer(p)

        s = s.replace('/', '\\')
        return s
    except Exception:
        return p





def get_default_path(server_ip):
    """Get the default server path."""
    server_config = SERVERS.get(server_ip, {})
    configured_default = server_config.get("default_path")


    if is_windows_server(server_ip):
        try:

            output, error, _ = ssh_manager.execute_command(server_ip, 'echo %USERPROFILE%')
            if output and not error:

                user_profile = output.strip().replace('\\', '/')
                print(f"🏠 Windows用户主目录: {user_profile}")
                return user_profile
        except Exception as e:
            print(f"⚠️  无法获取Windows用户主目录: {e}")


        return configured_default or "C:/"


    if configured_default:
        return configured_default

    user = server_config.get("user", "th")
    return f"/home/{user}"

class ParallelTransferManager:
    def __init__(self):
        self.active_transfers = {}
        self.transfer_stats = {}

    def get_file_size(self, server_ip, file_path):
        """Get file size."""
        if is_local_server(server_ip):
            try:
                return os.path.getsize(file_path)
            except:
                return 0
        else:

            output, error, _ = ssh_manager.execute_command(server_ip, f"stat -c%s {shlex.quote(file_path)} 2>/dev/null || echo 0")
            try:
                return int(output.strip())
            except:
                return 0

    def analyze_directory_structure(self, source_server, dir_path):
        """Analyze directory structure and return child file info."""
        all_files = []

        print(f"🔍 分析目录结构: {source_server}:{dir_path}")


        is_local_source = is_local_server(source_server)

        if is_local_source:

            print(f"📁 本地目录分析: {dir_path}")
            try:
                for root, dirs, files in os.walk(dir_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            file_size = os.path.getsize(file_path)
                            relative_path = os.path.relpath(file_path, dir_path)
                            all_files.append({
                                'path': file_path,
                                'name': relative_path,
                                'size': file_size,
                                'is_directory': False
                            })
                        except Exception as e:
                            print(f"⚠️ 跳过文件 {file_path}: {e}")
                            continue
                print(f"✅ 本地目录分析完成，找到 {len(all_files)} 个文件")
            except Exception as e:
                print(f"❌ 本地目录分析失败: {e}")
        else:

            print(f"🌐 远程目录分析: {source_server}:{dir_path}")
            try:

                cmd = f"find {shlex.quote(dir_path)} -type f -exec stat -c '%n %s' {{}} \\;"
                print(f"🔧 执行命令: {cmd}")
                output, error, _ = ssh_manager.execute_command(source_server, cmd)

                if error:
                    print(f"⚠️ 命令执行警告: {error}")

                if output:
                    print(f"📄 命令输出长度: {len(output)} 字符")
                    lines = output.strip().split('\n')
                    print(f"📄 输出行数: {len(lines)}")

                    for line in lines:
                        if line.strip():
                            parts = line.rsplit(' ', 1)
                            if len(parts) == 2:
                                file_path, size_str = parts
                                try:
                                    file_size = int(size_str)
                                    relative_path = os.path.relpath(file_path, dir_path)
                                    all_files.append({
                                        'path': file_path,
                                        'name': relative_path,
                                        'size': file_size,
                                        'is_directory': False
                                    })
                                except Exception as e:
                                    print(f"⚠️ 解析文件信息失败 {line}: {e}")
                                    continue
                    print(f"✅ 远程目录分析完成，找到 {len(all_files)} 个文件")
                else:
                    print(f"⚠️ 命令无输出，可能目录为空或无权限")
            except Exception as e:
                print(f"❌ 远程目录分析失败: {e}")

        return all_files

    def categorize_files(self, source_server, source_files, transfer_id=None):
        """Classify files into small/large groups and analyze directory structure."""
        small_files = []
        large_files = []
        directory_files = []

        threshold_bytes = PARALLEL_TRANSFER_CONFIG['small_file_threshold_mb'] * 1024 * 1024

        print(f"🔍 开始文件分类，源服务器: {source_server}, 文件数量: {len(source_files)}")

        try:
            for i, file_info in enumerate(source_files):
                print(f"📁 处理文件 {i+1}/{len(source_files)}: {file_info['name']} (目录: {file_info['is_directory']})")

                if file_info['is_directory']:

                    print(f"🔍 分析目录: {file_info['path']}")


                    if transfer_id:
                        emit_transfer_log(transfer_id, f'📁 正在分析目录 {file_info["name"]} 的结构...')

                    try:

                        if PARALLEL_TRANSFER_CONFIG['fast_mode']:

                            if transfer_id:
                                emit_transfer_log(transfer_id, f'⚡ 快速模式：跳过目录 {file_info["name"]} 的详细分析')


                            large_files.append({
                                **file_info,
                                'sub_files_count': 1,
                                'total_size': 0
                            })
                        else:

                            dir_files = self.analyze_directory_structure(source_server, file_info['path'])
                            directory_files.extend(dir_files)

                            print(f"✅ 目录 {file_info['name']} 包含 {len(dir_files)} 个文件")


                            if len(dir_files) > PARALLEL_TRANSFER_CONFIG['max_analysis_files']:
                                if transfer_id:
                                    emit_transfer_log(transfer_id, f'⚠️ 目录 {file_info["name"]} 包含 {len(dir_files)} 个文件，建议启用快速模式以提高性能')


                            if transfer_id:
                                emit_transfer_log(transfer_id, f'✅ 目录 {file_info["name"]} 分析完成，包含 {len(dir_files)} 个文件')


                            large_files.append({
                                **file_info,
                                'sub_files_count': len(dir_files),
                                'total_size': sum(f['size'] for f in dir_files)
                            })
                    except Exception as e:
                        print(f"❌ 分析目录 {file_info['name']} 失败: {e}")


                        if transfer_id:
                            emit_transfer_log(transfer_id, f'⚠️ 目录 {file_info["name"]} 分析失败: {str(e)}')


                        large_files.append({
                            **file_info,
                            'sub_files_count': 0,
                            'total_size': 0
                        })
                else:
                    try:
                        file_size = self.get_file_size(source_server, file_info['path'])
                        file_info['size'] = file_size

                        print(f"📄 文件 {file_info['name']} 大小: {file_size} 字节")

                        if file_size < threshold_bytes:
                            small_files.append(file_info)
                        else:
                            large_files.append(file_info)
                    except Exception as e:
                        print(f"❌ 获取文件 {file_info['name']} 大小失败: {e}")

                        large_files.append(file_info)

            print(f"✅ 文件分类完成: {len(small_files)}个小文件, {len(large_files)}个大文件/目录, {len(directory_files)}个子文件")

        except Exception as e:
            print(f"❌ 文件分类过程中出错: {e}")

            large_files = source_files.copy()
            small_files = []
            directory_files = []

        return small_files, large_files, directory_files

    def create_file_batches(self, files, batch_size=10):
        """Batch small files."""
        batches = []
        for i in range(0, len(files), batch_size):
            batches.append(files[i:i + batch_size])
        return batches

parallel_manager = ParallelTransferManager()


file_cache = {}
cache_timeout = 120
instant_cache_timeout = 300
BROWSE_PAGE_SIZE_DEFAULT = 400
BROWSE_PAGE_SIZE_MAX = 2000
BROWSE_PAGE_SIZE_MIN = 100

def _natural_sort_key(name: str):
    """Build a natural sort key (numbers by value, text case-insensitive)."""
    try:
        parts = re.split(r'(\d+)', name)
        return [int(p) if p.isdigit() else p.lower() for p in parts]
    except Exception:
        return [name.lower()]

BROWSE_SORT_BY_NAME = 'name'
BROWSE_SORT_BY_MODIFIED = 'modified'
BROWSE_SORT_BY_SIZE = 'size'
BROWSE_SORT_BY_TYPE = 'type'
BROWSE_SORT_ORDER_ASC = 'asc'
BROWSE_SORT_ORDER_DESC = 'desc'
BROWSE_SORT_BY_CHOICES = {
    BROWSE_SORT_BY_NAME,
    BROWSE_SORT_BY_MODIFIED,
    BROWSE_SORT_BY_SIZE,
    BROWSE_SORT_BY_TYPE,
}
BROWSE_SORT_ORDER_CHOICES = {
    BROWSE_SORT_ORDER_ASC,
    BROWSE_SORT_ORDER_DESC,
}


def normalize_browse_sort_by(value):
    sort_by = str(value or BROWSE_SORT_BY_NAME).strip().lower()
    return sort_by if sort_by in BROWSE_SORT_BY_CHOICES else BROWSE_SORT_BY_NAME


def normalize_browse_sort_order(value):
    sort_order = str(value or BROWSE_SORT_ORDER_ASC).strip().lower()
    return sort_order if sort_order in BROWSE_SORT_ORDER_CHOICES else BROWSE_SORT_ORDER_ASC


def _file_extension_sort_key(name: str):
    try:
        file_name = str(name or '')
        if file_name.startswith('.') and file_name.count('.') == 1:
            return ''
        _, ext = os.path.splitext(file_name)
        return ext[1:].lower()
    except Exception:
        return ''


def _coerce_modified_timestamp(value):
    try:
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value or '').strip()
        if not text:
            return 0
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M'):
            try:
                return int(datetime.strptime(text, fmt).timestamp())
            except Exception:
                continue
        text = text.replace('上午', 'AM').replace('下午', 'PM')
        for fmt in ('%Y-%m-%d %p %I:%M', '%Y/%m/%d %p %I:%M', '%m/%d/%Y %p %I:%M'):
            try:
                return int(datetime.strptime(text, fmt).timestamp())
            except Exception:
                continue
    except Exception:
        return 0
    return 0


def _get_item_modified_ts(item):
    try:
        if isinstance(item, dict):
            ts = item.get('modified_ts')
            if isinstance(ts, (int, float)):
                return int(ts)
            return _coerce_modified_timestamp(item.get('modified'))
    except Exception:
        return 0
    return 0


def sort_file_items(items, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Sort file items with directories pinned first and configurable secondary ordering."""
    try:
        sort_by = normalize_browse_sort_by(sort_by)
        sort_order = normalize_browse_sort_order(sort_order)
        reverse = sort_order == BROWSE_SORT_ORDER_DESC

        def _secondary_key(item):
            name_key = _natural_sort_key(str(item.get('name', '')))
            if sort_by == BROWSE_SORT_BY_MODIFIED:
                return (_get_item_modified_ts(item), name_key)
            if sort_by == BROWSE_SORT_BY_SIZE:
                return (int(item.get('size') or 0), name_key)
            if sort_by == BROWSE_SORT_BY_TYPE:
                if item.get('is_directory'):
                    return ('', name_key)
                return (_file_extension_sort_key(item.get('name', '')), name_key)
            return name_key

        ordered = sorted(items, key=_secondary_key, reverse=reverse)
        return sorted(ordered, key=lambda x: 0 if x.get('is_directory') else 1)
    except Exception:
        return items


def get_cache_key(server_ip, path, show_hidden, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Build cache key."""
    return (
        f"{server_ip}:{path}:{show_hidden}:"
        f"{normalize_browse_sort_by(sort_by)}:{normalize_browse_sort_order(sort_order)}"
    )

def is_cache_valid(cache_entry):
    """Check whether cache is valid."""
    return time.time() - cache_entry['timestamp'] < cache_timeout

def get_cached_listing(server_ip, path, show_hidden, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Get cached file list."""
    cache_key = get_cache_key(server_ip, path, show_hidden, sort_by, sort_order)
    if cache_key in file_cache:
        cache_entry = file_cache[cache_key]
        if is_cache_valid(cache_entry):
            return cache_entry['data']
    return None

def set_cached_listing(server_ip, path, show_hidden, data, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Set file list cache."""
    cache_key = get_cache_key(server_ip, path, show_hidden, sort_by, sort_order)
    file_cache[cache_key] = {
        'data': data,
        'timestamp': time.time()
    }


    current_time = time.time()
    expired_keys = [k for k, v in file_cache.items()
                   if current_time - v['timestamp'] > cache_timeout]
    for key in expired_keys:
        del file_cache[key]

def clear_cached_listing(server_ip, path, show_hidden=None):
    """Clear cache for a path."""
    if show_hidden is None:

        keys_to_remove = []
        for cache_key in file_cache.keys():
            if cache_key.startswith(f"{server_ip}:{path}:"):
                keys_to_remove.append(cache_key)

        for key in keys_to_remove:
            del file_cache[key]

        return len(keys_to_remove)
    else:
        keys_to_remove = []
        prefix = f"{server_ip}:{path}:{show_hidden}:"
        for cache_key in file_cache.keys():
            if cache_key.startswith(prefix):
                keys_to_remove.append(cache_key)

        for key in keys_to_remove:
            del file_cache[key]

        return len(keys_to_remove)

def clear_all_cache():
    """Clear all caches."""
    cache_count = len(file_cache)
    file_cache.clear()
    return cache_count

def is_winscp_hidden_file(name, permissions="", path="/"):
    """Decide whether to hide a file per WinSCP rules.

    Args:
        name: file name
        permissions: permission string (ls -l format)
        path: current directory path

    Returns:
        bool: True to hide, False to show
    """

    if name.startswith('.'):
        return True


    system_symlinks = {
        'bin', 'sbin', 'lib', 'lib32', 'lib64', 'libx32'
    }
    if name in system_symlinks:
        return True


    system_dirs = {
        'proc', 'sys', 'dev', 'run', 'boot', 'etc', 'var', 'tmp',
        'lost+found', 'cdrom', 'media', 'mnt', 'opt', 'srv', 'usr'
    }
    if name in system_dirs:
        return True


    system_files = {
        'swapfile', 'vmlinuz', 'initrd.img'
    }
    if name in system_files:
        return True


    if name.startswith('.Trash-'):
        return True


    if name == 'root' and path != '/':
        return True


    if name == 'home' and path != '/':
        return True


    if name == 'snap':
        return True



    if '/Work' in path or path.endswith('/Work'):

        work_hidden_dirs = {
            'home', 'root', 'snap', 'boot', 'etc', 'var', 'usr', 'opt',
            'proc', 'sys', 'dev', 'run', 'tmp', 'media', 'mnt', 'srv',
            'lost+found', 'cdrom'
        }
        if name in work_hidden_dirs:
            return True


        if name in {'bin', 'sbin', 'lib', 'lib32', 'lib64', 'libx32'}:
            return True

    return False

def get_directory_listing(server_ip, path=None, show_hidden=False, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Get a remote directory listing.

    Args:
        server_ip: server IP
        path: directory path
        show_hidden: include hidden files (WinSCP rules)
    """

    if path is None:
        path = get_default_path(server_ip)


    cached_result = get_cached_listing(server_ip, path, show_hidden, sort_by, sort_order)
    if cached_result is not None:
        return cached_result
    if is_local_server(server_ip):

        try:
            items = []
            for item in os.listdir(path):
                if not show_hidden and item.startswith('.'):
                    continue

                item_path = os.path.join(path, item)
                is_dir = os.path.isdir(item_path)
                size = os.path.getsize(item_path) if not is_dir else 0
                mtime = os.path.getmtime(item_path)

                items.append({
                    "name": item,
                    "path": item_path,
                    "is_directory": is_dir,
                    "size": size,
                    "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_ts": int(mtime)
                })
            items = sort_file_items(items, sort_by, sort_order)
            set_cached_listing(server_ip, path, show_hidden, items, sort_by, sort_order)
            return items
        except Exception:
            return []
    else:

        # Return whether the server is Windows.
        if is_windows_server(server_ip):


            import re
            normalized_path = path or ''

            if normalized_path.startswith('/') and re.match(r'^/[A-Za-z]:', normalized_path):
                normalized_path = normalized_path[1:]

            if re.match(r'^[A-Za-z]:$', normalized_path):
                normalized_path = normalized_path + '/'

            win_path = normalized_path.replace('/', '\\')

            dir_flags = "/-c"
            if show_hidden:
                dir_flags = "/a /-c"
            command = f'dir "{win_path}" {dir_flags}'

            output, error, _ = ssh_manager.execute_command(server_ip, command)

            if error and "找不到文件" not in error and "File Not Found" not in error:
                print(f"Windows dir命令错误: {error}")
                return []

            items = []
            lines = output.strip().split('\n')


            for line in lines:
                line = line.strip()
                if not line:
                    continue


                if 'Directory of' in line or '个文件' in line or '个目录' in line or 'File(s)' in line or 'Dir(s)' in line or 'bytes free' in line or '的目录' in line or '可用字节' in line:
                    continue






                import re

                match = re.match(r'(\d{2,4}[-/]\d{2}[-/]\d{2,4})\s+(上午|下午)?\s*(\d{2}:\d{2})\s+(<DIR>|<JUNCTION>|\d[\d,]*)\s+(.+)$', line)

                if match:
                    date_str = match.group(1)
                    am_pm = match.group(2) or ''
                    time_str = match.group(3)
                    size_or_dir = match.group(4)
                    name = match.group(5).strip()


                    if name in ['.', '..']:
                        continue


                    is_directory = (size_or_dir in ['<DIR>', '<JUNCTION>'])


                    if is_directory:
                        size = 0
                    else:
                        try:
                            size = int(size_or_dir.replace(',', ''))
                        except:
                            size = 0


                    base_path = normalized_path if 'normalized_path' in locals() and normalized_path else path
                    full_path = f"{base_path.rstrip('/')}/{name}".replace('\\', '/')


                    full_time = f"{am_pm} {time_str}".strip() if am_pm else time_str

                    items.append({
                        "name": name,
                        "path": full_path,
                        "is_directory": is_directory,
                        "size": size,
                        "modified": f"{date_str} {full_time}",
                        "modified_ts": _coerce_modified_timestamp(f"{date_str} {full_time}")
                    })

            items = sort_file_items(items, sort_by, sort_order)
            set_cached_listing(server_ip, path, show_hidden, items, sort_by, sort_order)
            return items
        else:



            command = f"LC_ALL=C ls -la --time-style=long-iso {shlex.quote(path)} | tail -n +2"

            output, error, _ = ssh_manager.execute_command(server_ip, command)

            if error:
                return []

            items = []


























            for line in output.strip().split('\n'):



                if not line:
                    continue

                parts = line.split()
                if len(parts) < 8:
                    continue

                permissions = parts[0]
                size = parts[4]
                date_parts = parts[5:7]
                name = ' '.join(parts[7:])

                if not show_hidden and name.startswith('.'):
                    continue


                if name in ['.', '..']:
                    continue


                if not show_hidden:
                    if is_winscp_hidden_file(name, permissions, path):
                        continue

                is_directory = permissions.startswith('d')

                items.append({
                    "name": name,
                    "path": os.path.join(path, name),
                    "is_directory": is_directory,
                    "size": int(size) if size.isdigit() else 0,
                    "modified": ' '.join(date_parts),
                    "modified_ts": _coerce_modified_timestamp(' '.join(date_parts))
                })

            items = sort_file_items(items, sort_by, sort_order)
            set_cached_listing(server_ip, path, show_hidden, items, sort_by, sort_order)
            return items

def get_directory_listing_optimized(server_ip, path=None, show_hidden=False, sort_by=BROWSE_SORT_BY_NAME, sort_order=BROWSE_SORT_ORDER_ASC):
    """Optimized directory listing focused on response speed."""


    if path is None:
        path = get_default_path(server_ip)


    cached_result = get_cached_listing(server_ip, path, show_hidden, sort_by, sort_order)
    if cached_result is not None:
        return cached_result


    if is_local_server(server_ip):

        try:
            items = []

            with os.scandir(path) as entries:
                for entry in entries:
                    if not show_hidden and entry.name.startswith('.'):
                        continue

                    try:
                        stat_info = entry.stat()
                        is_dir = entry.is_dir()
                        size = 0 if is_dir else stat_info.st_size
                        mtime = stat_info.st_mtime

                        items.append({
                            "name": entry.name,
                            "path": os.path.join(path, entry.name),
                            "is_directory": is_dir,
                            "size": size,
                            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "modified_ts": int(mtime)
                        })
                    except (OSError, PermissionError):

                        continue

            items = sort_file_items(items, sort_by, sort_order)
            set_cached_listing(server_ip, path, show_hidden, items, sort_by, sort_order)
            return items
        except Exception:
            return []
    else:

        return get_directory_listing(server_ip, path, show_hidden, sort_by, sort_order)

def start_speed_update_timer(transfer_id, source_server, target_server):
    """Start the speed update timer to improve transfer performance."""
    def speed_updater():
        last_time_update = time.time()
        last_speed_update = time.time()
        last_bytes_update = time.time()

        while transfer_id in active_transfers:
            try:

                time.sleep(0.25)

                if transfer_id not in active_transfers:
                    break

                current_time = time.time()


                simulated_speed = None
                if current_time - last_speed_update >= 0.1:
                    simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
                    last_speed_update = current_time


                elapsed_time = None
                if current_time - last_time_update >= 1.0:
                    elapsed_time = time_tracker.get_elapsed_time(transfer_id)
                    last_time_update = current_time

                transferred_human = None
                transferred_bytes = None
                if TRANSFER_BYTES_CONFIG.get('enabled', True) and current_time - last_bytes_update >= TRANSFER_BYTES_CONFIG.get('update_interval', 2.0):
                    transferred_bytes = get_transfer_bytes_total(transfer_id)
                    transferred_human = _human_readable_size(transferred_bytes)
                    last_bytes_update = current_time


                if simulated_speed is not None or elapsed_time is not None or transferred_human is not None:

                    is_local_source = is_local_server(source_server)
                    is_local_target = is_local_server(target_server)

                    if is_local_source and not is_local_target:
                        transfer_mode = 'local_to_remote'
                    elif not is_local_source and is_local_target:
                        transfer_mode = 'remote_to_local'
                    else:
                        transfer_mode = 'remote_to_remote'


                    update_data = {
                        'transfer_id': transfer_id,
                        'source_server': source_server,
                        'target_server': target_server,
                        'transfer_mode': transfer_mode
                    }


                    if simulated_speed is not None:
                        update_data['speed'] = simulated_speed
                    if elapsed_time is not None:
                        update_data['elapsed_time'] = elapsed_time
                    if transferred_human is not None:
                        update_data['transferred_bytes'] = transferred_bytes
                        update_data['transferred_human'] = transferred_human

                    socketio.emit('speed_update', update_data)

            except Exception as e:
                print(f"速度更新器出错: {e}")
                break


    speed_thread = threading.Thread(target=speed_updater)
    speed_thread.daemon = True
    speed_thread.start()

def _normalize_batch_path(path: str) -> str:
    if not path:
        return ''
    return path.replace('\\', '/')

def _get_batch_parent(source_files, source_is_windows):
    parents = set()
    for file_info in source_files or []:
        path = _normalize_batch_path(file_info.get('path', ''))
        if not path:
            continue
        if source_is_windows:
            import ntpath
            parent = ntpath.dirname(path)
            if parent and parent.endswith(':'):
                parent += '/'
        else:
            parent = os.path.dirname(path) or '/'
        parents.add(_normalize_batch_path(parent))
    if len(parents) == 1:
        return parents.pop()
    return None

def _can_batch_transfer(transfer_mode, source_files, source_is_windows, source_server, target_server):
    if transfer_mode not in ('local_to_remote', 'remote_to_local', 'remote_to_remote'):
        return False
    if source_server == target_server:
        return False
    if not source_files or len(source_files) < 2:
        return False
    return bool(_get_batch_parent(source_files, source_is_windows))

def _build_batch_rsync_opts(source_is_windows=False, target_is_windows=False):
    rsync_opts = [
        '-a',
        '--inplace',
        '--whole-file',
        '--no-compress',
        '--numeric-ids',
        '--timeout=600',
        '-s',
        '--no-perms',
        '--no-owner',
        '--no-group',
        '--omit-dir-times',
    ]
    if source_is_windows or target_is_windows:
        rsync_opts.append('--iconv=UTF-8,UTF-8')
    _append_rsync_progress_opts(rsync_opts)
    return rsync_opts

def _delete_source_paths_batch(source_server, paths):
    if not paths:
        return True

    is_local = is_local_server(source_server)
    is_windows = is_windows_server(source_server)

    try:
        if is_local:
            try:
                subprocess.run(['rm', '-rf', '--', *paths], check=True)
                return True
            except Exception:
                ok = True
                for p in paths:
                    try:
                        if os.path.isdir(p):
                            shutil.rmtree(p)
                        else:
                            os.remove(p)
                    except Exception:
                        ok = False
                return ok

        if is_windows:
            try:
                path_pairs = []
                for p in paths:
                    win_p = normalize_windows_path_for_cmd(p)
                    path_pairs.append(win_p)
                ps_items = ",".join([f"'{_escape_pwsh_literal(win_p)}'" for win_p in path_pairs])
                ps_script = (
                    "$failed=@();"
                    f"$paths=@({ps_items});"
                    "foreach($p in $paths){"
                    "  if(Test-Path -LiteralPath $p){"
                    "    $err='';"
                    "    try{ Remove-Item -LiteralPath $p -Force -Recurse -ErrorAction Stop }catch{ $err=$_.Exception.Message }"
                    "    if(Test-Path -LiteralPath $p){"
                    "      if([string]::IsNullOrEmpty($err)){ $err='删除失败' }"
                    "      $failed += [pscustomobject]@{path=$p; error=$err}"
                    "    }"
                    "  }"
                    "}"
                    "if($failed.Count -gt 0){ $failed | ConvertTo-Json -Compress; exit 1 }"
                    "exit 0"
                )
                delete_cmd = f'powershell -NoProfile -Command "{ps_script}"'
                _, _, exit_code = ssh_manager.execute_command(source_server, delete_cmd)
                if exit_code == 0:
                    return True
            except Exception:
                pass

            ok = True
            for p in paths:
                try:
                    win_path = normalize_windows_path_for_cmd(p)
                    ps_path = win_path.replace("'", "''")
                    delete_cmd = (
                        "powershell -NoProfile -Command "
                        f"\"Remove-Item -LiteralPath '{ps_path}' -Force -Recurse -ErrorAction SilentlyContinue; "
                        f"if (Test-Path -LiteralPath '{ps_path}') {{ exit 1 }}\""
                    )
                    _, _, exit_code = ssh_manager.execute_command(source_server, delete_cmd)
                    if exit_code != 0:
                        ok = False
                except Exception:
                    ok = False
            return ok

        quoted_paths = " ".join([shlex.quote(p) for p in paths if p])
        if quoted_paths:
            rm_cmd_sudo = f"sudo -n rm -rf -- {quoted_paths}"
            _, _, exit_code = ssh_manager.execute_command(source_server, rm_cmd_sudo)
            if exit_code != 0:
                rm_cmd = f"rm -rf -- {quoted_paths}"
                _, _, exit_code = ssh_manager.execute_command(source_server, rm_cmd)
            if exit_code == 0:
                return True

        ok = True
        for p in paths:
            try:
                rm_cmd_sudo = f"sudo -n rm -rf {shlex.quote(p)}"
                _, _, exit_code = ssh_manager.execute_command(source_server, rm_cmd_sudo)
                if exit_code != 0:
                    rm_cmd = f"rm -rf {shlex.quote(p)}"
                    _, _, exit_code = ssh_manager.execute_command(source_server, rm_cmd)
                if exit_code != 0:
                    ok = False
            except Exception:
                ok = False
        return ok
    except Exception:
        return False

def _clear_transfer_listing_cache(source_server, target_server, source_files, target_path, mode):
    try:
        if target_server and target_path:
            clear_cached_listing(target_server, target_path)
    except Exception:
        pass

    if mode != 'move':
        return

    try:
        source_is_windows = is_windows_server(source_server)
        parent = _get_batch_parent(source_files, source_is_windows)
        if parent:
            clear_cached_listing(source_server, parent)
            return
        parents = set()
        for info in source_files or []:
            p = _normalize_batch_path(info.get('path', ''))
            if not p:
                continue
            if source_is_windows:
                import ntpath
                pp = ntpath.dirname(p)
                if pp and pp.endswith(':'):
                    pp += '/'
            else:
                pp = os.path.dirname(p) or '/'
            parents.add(_normalize_batch_path(pp))
        for pp in parents:
            clear_cached_listing(source_server, pp)
    except Exception:
        pass

def transfer_batch_instant(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """Batch transfer: combine same-directory files into one rsync to reduce overhead."""
    transfer_mode = determine_transfer_mode(source_server, target_server)
    source_is_windows = is_windows_server(source_server)
    target_is_windows = is_windows_server(target_server)

    if not _can_batch_transfer(transfer_mode, source_files, source_is_windows, source_server, target_server):
        return {'success': False, 'message': 'batch not applicable'}

    if transfer_mode == 'remote_to_remote' and source_server == target_server:
        return {'success': False, 'message': 'same server no rsync'}

    max_files = PARALLEL_TRANSFER_CONFIG.get('batch_max_files', 200)
    if max_files and len(source_files) > max_files:
        return {'success': False, 'message': 'batch size too large'}

    rsync_opts = _build_batch_rsync_opts(source_is_windows, target_is_windows)
    rsync_opts_str = ' '.join(rsync_opts)

    try:
        if transfer_mode == 'local_to_remote':
            target_user = SERVERS[target_server]['user']
            target_password = SERVERS[target_server].get('password')
            rsync_target_path = target_path
            if target_is_windows:
                normalized_target = normalize_windows_path_for_transfer(target_path)
                rsync_target_path = convert_windows_path_to_cygwin(normalized_target)

            ssh_cmd = RSYNC_SSH_CMD
            target_port = SERVERS[target_server].get('port', 22)
            if target_port != 22:
                ssh_cmd = f"{ssh_cmd} -p {target_port}"

            sources = [_normalize_batch_path(f.get('path', '')) for f in source_files]
            sources = [s for s in sources if s]
            if not sources:
                return {'success': False, 'message': 'empty sources'}

            target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/')
            if target_password:
                cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd] + sources + [target_spec]
            else:
                cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd] + sources + [target_spec]

            part_id = f"rsync_{uuid.uuid4().hex}"
            return_code = _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id)
            if return_code != 0:
                return {'success': False, 'message': f'rsync exit {return_code}'}

        elif transfer_mode == 'remote_to_local':
            source_user = SERVERS[source_server]['user']
            source_password = SERVERS[source_server].get('password')

            ssh_cmd = RSYNC_SSH_CMD
            source_port = SERVERS[source_server].get('port', 22)
            if source_port != 22:
                ssh_cmd = f"{ssh_cmd} -p {source_port}"

            sources = []
            for f in source_files:
                src_path = _normalize_batch_path(f.get('path', ''))
                if not src_path:
                    continue
                if source_is_windows:
                    src_path = convert_windows_path_to_cygwin(src_path)
                sources.append(build_remote_spec(source_server, source_user, src_path))

            if not sources:
                return {'success': False, 'message': 'empty sources'}

            if source_password:
                cmd = ['sshpass', '-p', source_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd] + sources + [f'{target_path}/']
            else:
                cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd] + sources + [f'{target_path}/']

            part_id = f"rsync_{uuid.uuid4().hex}"
            return_code = _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id)
            if return_code != 0:
                return {'success': False, 'message': f'rsync exit {return_code}'}

        elif transfer_mode == 'remote_to_remote':
            target_user = SERVERS[target_server]['user']
            target_password = SERVERS[target_server].get('password')
            source_user = SERVERS[source_server]['user']
            source_password = SERVERS[source_server].get('password')

            if source_is_windows and not target_is_windows:
                exec_server = target_server
                sshpass_cmd = "sshpass"

                ssh_to_source = RSYNC_SSH_CMD
                source_port = SERVERS[source_server].get('port', 22)
                if source_port != 22:
                    ssh_to_source = f"{ssh_to_source} -p {source_port}"

                sources = []
                for f in source_files:
                    src_path = _normalize_batch_path(f.get('path', ''))
                    if not src_path:
                        continue
                    src_path = convert_windows_path_to_cygwin(src_path)
                    sources.append(build_remote_spec(source_server, source_user, src_path))

                if not sources:
                    return {'success': False, 'message': 'empty sources'}

                sources_arg = ' '.join([shlex.quote(s) for s in sources])
                target_dest = shlex.quote(f'{target_path}/')
                if source_password:
                    remote_cmd = f"{sshpass_cmd} -p {shlex.quote(source_password)} rsync {rsync_opts_str} -e {shlex.quote(ssh_to_source)} {sources_arg} {target_dest}"
                else:
                    remote_cmd = f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_source)} {sources_arg} {target_dest}"

                ssh = ssh_manager.get_connection(exec_server)
                if not ssh:
                    raise Exception(f"无法连接到目标服务器 {exec_server}")
                part_id = f"rsync_{uuid.uuid4().hex}"
                exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
                if exit_status != 0:
                    return {'success': False, 'message': f'rsync exit {exit_status}: {error}'}
            else:
                exec_server = source_server
                sshpass_cmd = "sshpass"

                rsync_target_path = target_path
                if target_is_windows:
                    normalized_target = normalize_windows_path_for_transfer(target_path)
                    rsync_target_path = convert_windows_path_to_cygwin(normalized_target)

                ssh_to_target = RSYNC_SSH_CMD
                target_port = SERVERS[target_server].get('port', 22)
                if target_port != 22:
                    ssh_to_target = f"{ssh_to_target} -p {target_port}"

                sources = []
                for f in source_files:
                    src_path = _normalize_batch_path(f.get('path', ''))
                    if not src_path:
                        continue
                    if source_is_windows:
                        src_path = convert_windows_path_to_cygwin(src_path)
                    sources.append(src_path)

                if not sources:
                    return {'success': False, 'message': 'empty sources'}

                sources_arg = ' '.join([shlex.quote(s) for s in sources])
                dest = shlex.quote(build_remote_spec(target_server, target_user, f'{rsync_target_path}/'))
                if target_password:
                    remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {rsync_opts_str} -e {shlex.quote(ssh_to_target)} {sources_arg} {dest}"
                else:
                    remote_cmd = f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_target)} {sources_arg} {dest}"

                ssh = ssh_manager.get_connection(exec_server)
                if not ssh:
                    raise Exception(f"无法连接到源服务器 {exec_server}")
                part_id = f"rsync_{uuid.uuid4().hex}"
                exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
                if exit_status != 0:
                    return {'success': False, 'message': f'rsync exit {exit_status}: {error}'}
        else:
            return {'success': False, 'message': 'unsupported mode'}

        if mode == 'move':
            paths = [_normalize_batch_path(f.get('path', '')) for f in source_files]
            paths = [p for p in paths if p]
            deleted_ok = _delete_source_paths_batch(source_server, paths)
            if not deleted_ok:
                emit_transfer_log(transfer_id, '⚠️ 剪切模式：源文件删除存在失败项')

        return {'success': True, 'completed': len(source_files), 'failed': 0}

    except Exception as e:
        return {'success': False, 'message': str(e)}

def _build_rsync_excludes_for_dir(source_dir: str, exclude_paths: list):
    src = str(source_dir or '').replace('\\', '/').rstrip('/')
    patterns = []
    for p in (exclude_paths or []):
        if not p:
            continue
        path = str(p).replace('\\', '/')
        if src and (path == src or path.startswith(src + '/')):
            rel = path[len(src):].lstrip('/')
            if rel:
                # Anchor exclude at transfer root to avoid accidental partial matches.
                patterns.append('/' + rel)
    # Deduplicate but keep stable order.
    seen = set()
    out = []
    for pat in patterns:
        if pat in seen:
            continue
        seen.add(pat)
        out.append(pat)
    return out


def transfer_directory_contents_instant(transfer_id, source_server, source_dir, target_server, target_path, mode="copy", fast_ssh=True, exclude_paths=None):
    """
    Transfer "current directory contents" via a single rsync command:
      rsync -a <source_dir>/ <target_dir>/
    This avoids constructing a huge file list (Ctrl+A on large directories).
    """
    try:
        if not source_dir or not target_path:
            return {'success': False, 'message': 'empty source_dir or target_path'}

        source_is_windows = is_windows_server(source_server)
        target_is_windows = is_windows_server(target_server)
        src_dir_raw = str(source_dir)
        tgt_dir_raw = str(target_path)

        src_dir = convert_windows_path_to_cygwin(src_dir_raw) if source_is_windows else src_dir_raw
        tgt_dir = convert_windows_path_to_cygwin(tgt_dir_raw) if target_is_windows else tgt_dir_raw

        # Normalize trailing slash semantics: "dir/" means copy contents.
        src_with_slash = src_dir.rstrip('/') + '/'
        tgt_with_slash = tgt_dir.rstrip('/') + '/'

        rsync_opts = [
            "-a",
            "--inplace",
            "--whole-file",
            "--no-compress",
            "--numeric-ids",
            "--timeout=600",
            "-s",
            "--no-perms",
            "--no-owner",
            "--no-group",
            "--omit-dir-times",
        ]
        if source_is_windows or target_is_windows:
            rsync_opts.append("--iconv=UTF-8,UTF-8")

        # Exclude specific paths (relative to source_dir).
        for pat in _build_rsync_excludes_for_dir(source_dir, exclude_paths or []):
            rsync_opts.append(f"--exclude={shlex.quote(pat)}")

        # Move mode: remove source files after successful transfer.
        if mode == "move":
            rsync_opts.append("--remove-source-files")

        _append_rsync_progress_opts(rsync_opts)
        rsync_opts_str = ' '.join(rsync_opts)

        ssh = None

        if source_server == target_server:
            ssh = ssh_manager.get_connection(source_server)
            if not ssh:
                raise Exception(f"无法连接到源服务器 {source_server}")
            remote_cmd = f"rsync {rsync_opts_str} {shlex.quote(src_with_slash)} {shlex.quote(tgt_with_slash)}"
        elif source_is_windows and not target_is_windows:
            source_user = SERVERS[source_server]['user']
            source_password = SERVERS[source_server].get('password')

            ssh_to_source = RSYNC_SSH_CMD
            source_port = SERVERS[source_server].get('port', 22)
            if source_port != 22:
                ssh_to_source = f"{ssh_to_source} -p {source_port}"

            source_spec = shlex.quote(build_remote_spec(source_server, source_user, src_with_slash))
            if source_password:
                remote_cmd = (
                    f"sshpass -p {shlex.quote(source_password)} "
                    f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_source)} "
                    f"{source_spec} {shlex.quote(tgt_with_slash)}"
                )
            else:
                remote_cmd = (
                    f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_source)} "
                    f"{source_spec} {shlex.quote(tgt_with_slash)}"
                )

            ssh = ssh_manager.get_connection(target_server)
            if not ssh:
                raise Exception(f"无法连接到目标服务器 {target_server}")
        else:
            ssh = ssh_manager.get_connection(source_server)
            if not ssh:
                raise Exception(f"无法连接到源服务器 {source_server}")
            target_user = SERVERS[target_server]['user']
            target_password = SERVERS[target_server].get('password')

            # SSH options for rsync target.
            ssh_to_target = RSYNC_SSH_CMD
            target_port = SERVERS[target_server].get('port', 22)
            if target_port != 22:
                ssh_to_target = f"{ssh_to_target} -p {target_port}"

            dest = shlex.quote(build_remote_spec(target_server, target_user, tgt_with_slash))
            if target_password:
                remote_cmd = (
                    f"sshpass -p {shlex.quote(target_password)} "
                    f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_target)} "
                    f"{shlex.quote(src_with_slash)} {dest}"
                )
            else:
                remote_cmd = (
                    f"rsync {rsync_opts_str} -e {shlex.quote(ssh_to_target)} "
                    f"{shlex.quote(src_with_slash)} {dest}"
                )

        emit_transfer_log(transfer_id, f"📁 全选目录传输: {source_dir} -> {target_server}:{target_path}")
        part_id = f"rsync_{uuid.uuid4().hex}"
        exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
        if exit_status != 0:
            return {'success': False, 'message': f'rsync exit {exit_status}: {error}'}

        # Cleanup empty dirs after move (best-effort).
        if mode == "move":
            try:
                if source_is_windows:
                    # Cygwin find may exist in cwRsync environment.
                    cleanup_cmd = f"find {shlex.quote(src_dir)} -type d -empty -delete"
                else:
                    cleanup_cmd = f"find {shlex.quote(src_dir_raw)} -type d -empty -delete"
                ssh_manager.execute_command(source_server, cleanup_cmd)
            except Exception:
                emit_transfer_log(transfer_id, "⚠️ 剪切模式：清理空目录失败（已忽略）")

        return {'success': True}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def start_instant_parallel_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True, parallel_enabled=True, select_all=False, source_dir="", exclude_paths=None):
    """Start instant parallel transfer tasks without pre-analysis."""
    def _log_transfer_summary(status: str, total_time: str = "", error: str = ""):
        meta = active_transfers.get(transfer_id, {})
        client_ip = meta.get('client_ip', '未知')
        src_server = meta.get('source_server', source_server)
        tgt_server = meta.get('target_server', target_server)
        target_base = meta.get('target_path', target_path)
        files = meta.get('source_files', source_files)
        src_dir = meta.get('source_dir') or source_dir
        if isinstance(files, list):
            file_names = ','.join([f.get('name', '') for f in files if isinstance(f, dict)])
            source_paths = ';'.join([f.get('path', '') for f in files if isinstance(f, dict)])
        else:
            file_names = ''
            source_paths = ''

        append_transfer_log_record(
            source_ip=src_server,
            target_ip=tgt_server,
            source_path=source_paths or src_dir or target_base,
            target_full_path=target_base,
            duration_sec=_hhmmss_to_seconds(total_time),
            status=status,
            error=error,
            client_ip=client_ip,
            mode=meta.get('mode', mode),
            file_name=file_names or (files[0].get('name', '') if isinstance(files, list) and files else ''),
            action='transfer'
        )

    def transfer_worker():
        try:
            if select_all and source_dir:
                total_files = 1
            else:
                total_files = len(source_files)


            if (is_windows_server(source_server) or is_windows_server(target_server)):
                speed_simulator.init_transfer_speed(transfer_id, 50.0, 55.0)
            else:
                speed_simulator.init_transfer_speed(transfer_id)


            start_speed_update_timer(transfer_id, source_server, target_server)


            progress_manager.init_transfer(transfer_id, total_files)

            if select_all and source_dir:
                time_tracker.start_transfer(transfer_id)
                result = transfer_directory_contents_instant(
                    transfer_id,
                    source_server,
                    source_dir,
                    target_server,
                    target_path,
                    mode=mode,
                    fast_ssh=fast_ssh,
                    exclude_paths=exclude_paths or [],
                )
                total_time = time_tracker.end_transfer(transfer_id)
                emit_transfer_bytes_snapshot(transfer_id)
                if result and result.get('success'):
                    socketio.emit('transfer_complete', {
                        'transfer_id': transfer_id,
                        'status': 'success',
                        'message': '目录内容传输完成',
                        'total_time': total_time
                    })
                    _log_transfer_summary('success', total_time)
                else:
                    error_msg = (result or {}).get('message', 'unknown error')
                    socketio.emit('transfer_complete', {
                        'transfer_id': transfer_id,
                        'status': 'error',
                        'message': f'目录内容传输失败: {error_msg}',
                        'total_time': total_time
                    })
                    _log_transfer_summary('error', total_time, error_msg)
                return


            if not PERFORMANCE_CONFIG.get('reduce_websocket_traffic', True):
                emit_transfer_log(transfer_id, f'🚀 立即开始传输 {total_files} 个项目...')


            if not parallel_enabled or total_files == 1:

                time_tracker.start_transfer(transfer_id)

                return start_sequential_transfer(transfer_id, source_server, source_files, target_server, target_path, mode, fast_ssh)


            time_tracker.start_transfer(transfer_id)


            if PARALLEL_TRANSFER_CONFIG.get('enable_batch_transfer', True):
                batch_result = transfer_batch_instant(
                    transfer_id, source_server, source_files, target_server, target_path, mode, fast_ssh
                )
                if batch_result and batch_result.get('success'):
                    total_time = time_tracker.end_transfer(transfer_id)
                    completed_count = batch_result.get('completed', total_files)

                    _clear_transfer_listing_cache(source_server, target_server, source_files, target_path, mode)

                    emit_transfer_bytes_snapshot(transfer_id)
                    socketio.emit('transfer_complete', {
                        'transfer_id': transfer_id,
                        'status': 'success',
                        'message': f'成功传输 {completed_count} 个文件/文件夹',
                        'total_time': total_time
                    })
                    _log_transfer_summary('success', total_time)
                    return
                elif batch_result and batch_result.get('message'):
                    print(f"[INFO] 批量传输未启用或失败，回退并行模式: {batch_result.get('message')}")


            max_workers = min(PARALLEL_TRANSFER_CONFIG['max_workers'], total_files)

            emit_transfer_log(transfer_id, f'⚡ 启动 {max_workers} 个并行传输线程...')

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []


                for file_info in source_files:
                    future = executor.submit(
                        transfer_single_file_instant,
                        transfer_id, source_server, file_info, target_server, target_path, mode, fast_ssh
                    )
                    futures.append(future)


                completed_count = 0
                failed_count = 0

                for future in concurrent.futures.as_completed(futures):

                    if transfer_id not in active_transfers:

                        for f in futures:
                            f.cancel()
                        return

                    try:
                        result = future.result()

                        print(f"[DEBUG] 传输任务返回值: {result}, 类型: {type(result)}")



                        is_success = False
                        if result is not None:
                            if isinstance(result, dict):
                                is_success = result.get('success', False)
                                print(f"[DEBUG] 字典返回值，success={is_success}")
                            else:

                                print(f"[WARNING] 传输函数返回了非字典值: {result}, 类型: {type(result)}")

                                is_success = bool(result)
                        else:
                            print(f"[WARNING] 传输函数返回了None")

                        if is_success:
                            completed_count += 1
                            print(f"[DEBUG] 传输成功，已完成: {completed_count}/{total_files}")
                        else:
                            failed_count += 1
                            error_msg = result.get('message', '未知错误') if isinstance(result, dict) else str(result)
                            print(f"[DEBUG] 传输失败，失败数: {failed_count}, 原因: {error_msg}")



                    except Exception as e:

                        failed_count += 1
                        print(f"[ERROR] 传输任务异常: {str(e)}, 类型: {type(e).__name__}")
                        import traceback
                        print(f"[ERROR] 异常堆栈: {traceback.format_exc()}")
                        emit_transfer_log(transfer_id, f'❌ 传输任务失败: {str(e)}')



            print(f"[DEBUG] 传输完成统计 - 成功: {completed_count}, 失败: {failed_count}, 总数: {total_files}")


            processed_count = completed_count + failed_count
            if processed_count != total_files:
                print(f"[WARNING] 任务处理数量不匹配！已处理: {processed_count}, 总数: {total_files}")

                failed_count += (total_files - processed_count)
                print(f"[WARNING] 调整后失败数: {failed_count}")

            if failed_count > 0:

                total_time = time_tracker.end_transfer(transfer_id)

                _clear_transfer_listing_cache(source_server, target_server, source_files, target_path, mode)

                print(f"[DEBUG] 发送部分成功事件: transfer_id={transfer_id}, status=partial_success")
                emit_transfer_bytes_snapshot(transfer_id)
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'partial_success',
                    'message': f'传输完成，成功: {completed_count}, 失败: {failed_count}',
                    'total_time': total_time
                })
            else:
                # Stop transfer timing.
                total_time = time_tracker.end_transfer(transfer_id)

                _clear_transfer_listing_cache(source_server, target_server, source_files, target_path, mode)


                print(f"[性能监控] 传输ID: {transfer_id}")
                print(f"[性能监控] 文件数量: {completed_count}")
                print(f"[性能监控] 传输时间: {total_time}")

                print(f"[性能监控] 速度更新间隔: {PERFORMANCE_CONFIG['speed_update_interval']}秒")

                print(f"[DEBUG] 发送成功事件: transfer_id={transfer_id}, status=success")
                emit_transfer_bytes_snapshot(transfer_id)
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'success',
                    'message': f'成功传输 {completed_count} 个文件/文件夹',
                    'total_time': total_time
                })
                _log_transfer_summary('success', total_time)

        except Exception as e:

            total_time = time_tracker.end_transfer(transfer_id)


            print(f"[DEBUG] 传输异常: {str(e)}")
            print(f"[DEBUG] 发送错误事件: transfer_id={transfer_id}, status=error")

            emit_transfer_bytes_snapshot(transfer_id)
            socketio.emit('transfer_complete', {
                'transfer_id': transfer_id,
                'status': 'error',
                'message': str(e),
                'total_time': total_time
            })
            try:
                _log_transfer_summary('failure', total_time, str(e))
            except Exception:
                pass
        finally:

            if transfer_id in active_transfers:
                del active_transfers[transfer_id]
            with TRANSFER_PROCESS_LOCK:
                transfer_processes.pop(transfer_id, None)
            progress_manager.cleanup_transfer(transfer_id)
            speed_simulator.cleanup_transfer(transfer_id)
            cleanup_transfer_bytes(transfer_id)


    thread = threading.Thread(target=transfer_worker)
    thread.daemon = True
    thread.start()

def transfer_single_file_instant(transfer_id, source_server, file_info, target_server, target_path, mode="copy", fast_ssh=True):
    """Instantly transfer a file or directory without pre-analysis."""
    try:
        transfer_meta = active_transfers.get(transfer_id, {})
        _client_ip_for_log = transfer_meta.get('client_ip', '未知')
        _mode_for_log = transfer_meta.get('mode', mode)

        source_path = file_info['path']
        file_name = file_info['name']
        is_directory = file_info['is_directory']

        _file_transfer_start_ts = time.time()
        _log_target_full_path = _join_target_full_path_for_log(target_server, target_path, file_name)
        _log_source_ip = _normalize_ip_for_log(source_server)
        _log_target_ip = _normalize_ip_for_log(target_server)


        emit_transfer_log(transfer_id, f'🚀 开始传输 {file_name}...')


        if transfer_id not in active_transfers:
            return {'success': False, 'message': '传输被取消'}


        transfer_mode = determine_transfer_mode(source_server, target_server)

        print(f"🔄 传输模式: {transfer_mode} ({source_server} → {target_server})")


        emit_transfer_log(transfer_id, f'🔄 传输模式: {transfer_mode} ({source_server} → {target_server})')

        if transfer_mode == 'local_to_remote':

            print(f"📍 调用函数: transfer_file_via_local_rsync_instant")
            success = transfer_file_via_local_rsync_instant(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode)
            if not success:
                raise Exception("本地到远程传输失败")
        elif transfer_mode == 'remote_to_local':

            print(f"📍 调用函数: transfer_file_via_remote_to_local_rsync_instant")
            success = transfer_file_via_remote_to_local_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode)
            if not success:
                raise Exception("远程到本地传输失败")
        elif transfer_mode == 'remote_to_remote':

            print(f"📍 调用函数: transfer_file_via_remote_rsync_instant")
            success = transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode)
            if not success:
                raise Exception("远程到远程传输失败")
        else:

            print(f"📍 调用函数: transfer_file_via_local_to_local_instant")
            print(f"[DEBUG] 参数: source_path={source_path}, target_path={target_path}, file_name={file_name}, is_directory={is_directory}, mode={mode}")

            operation = "剪切" if mode == "move" else "复制"
            cmd_name = "mv" if mode == "move" else "cp"
            emit_transfer_log(transfer_id, f'🔄 传输模式: local_to_local (本地到本地{operation}，使用{cmd_name}命令)')

            success = transfer_file_via_local_to_local_instant(source_path, target_path, file_name, is_directory, transfer_id, mode)
            print(f"[DEBUG] transfer_file_via_local_to_local_instant返回值: {success}, 类型: {type(success)}")
            if not success:
                raise Exception(f"本地到本地{operation}失败")
            print(f"[DEBUG] 本地到本地{operation}成功，准备返回字典")



        need_delete_source = mode == "move" and not (transfer_mode == 'local_to_local' or (transfer_mode == 'remote_to_remote' and source_server == target_server))

        if need_delete_source:
            try:
                if is_local_server(source_server):

                    import shutil
                    if is_directory:
                        shutil.rmtree(source_path)
                    else:
                        os.remove(source_path)
                    emit_transfer_log(transfer_id, f'🗑️ 已删除源文件: {file_name}')
                else:

                    is_windows = is_windows_server(source_server)
                    if is_windows:

                        win_path = normalize_windows_path_for_cmd(source_path)
                        ps_path = win_path.replace("'", "''")
                        delete_cmd = (
                            "powershell -NoProfile -Command "
                            f"\"Remove-Item -LiteralPath '{ps_path}' -Force -Recurse -ErrorAction SilentlyContinue; "
                            f"if (Test-Path -LiteralPath '{ps_path}') {{ exit 1 }}\""
                        )
                        emit_transfer_log(transfer_id, f'🗑️ 执行Windows删除命令: {delete_cmd}')
                    else:

                        delete_cmd = f"rm -rf {shlex.quote(source_path)}"
                        emit_transfer_log(transfer_id, f'🗑️ 执行Linux删除命令: {delete_cmd}')

                    stdout, stderr, exit_code = ssh_manager.execute_command(source_server, delete_cmd)
                    if exit_code == 0:
                        emit_transfer_log(transfer_id, f'✅ 已删除源文件: {file_name}')
                    else:
                        emit_transfer_log(transfer_id, f'❌ 删除源文件失败: {stderr}')
            except Exception as e:
                emit_transfer_log(transfer_id, f'❌ 删除源文件异常: {str(e)}')

        emit_transfer_log(transfer_id, f'✅ {file_name} 传输完成')


        try:
            append_transfer_log_record(
                source_ip=_log_source_ip,
                target_ip=_log_target_ip,
                source_path=source_path,
                target_full_path=_log_target_full_path,
                duration_sec=(time.time() - _file_transfer_start_ts),
                status='success',
                error="",
                client_ip=_client_ip_for_log,
                mode=_mode_for_log,
                file_name=file_name
            )
        except Exception:
            pass

        return {'success': True, 'message': f'{file_name} 传输完成'}

    except Exception as e:

        try:
            append_transfer_log_record(
                source_ip=_log_source_ip if '_log_source_ip' in locals() else source_server,
                target_ip=_log_target_ip if '_log_target_ip' in locals() else target_server,
                source_path=source_path if 'source_path' in locals() else file_info.get('path', ''),
                target_full_path=_log_target_full_path if '_log_target_full_path' in locals() else _join_target_full_path_for_log(target_server, target_path, file_info.get('name', '')),
                duration_sec=(time.time() - _file_transfer_start_ts) if '_file_transfer_start_ts' in locals() else 0.0,
                status='failure',
                error=str(e),
                client_ip=_client_ip_for_log,
                mode=_mode_for_log,
                file_name=file_info.get('name', '')
            )
        except Exception:
            pass


        try:
            failed_source_path = source_path if 'source_path' in locals() else file_info.get('path', '')
        except Exception:
            failed_source_path = ''
        try:
            failed_target_full = _log_target_full_path if '_log_target_full_path' in locals() else _join_target_full_path_for_log(target_server, target_path, file_info.get('name', ''))
        except Exception:
            failed_target_full = ''
        failed_name = ''
        try:
            if isinstance(file_info, dict):
                failed_name = file_info.get('name', '')
        except Exception:
            failed_name = ''

        emit_transfer_log(
            transfer_id,
            f'❌ 传输失败: {failed_name or "[未知名称]"} | 源: {source_server}:{failed_source_path} -> 目标: {target_server}:{failed_target_full} | 错误: {str(e)}'
        )
        return {'success': False, 'message': str(e)}

def transfer_file_via_local_rsync_instant(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode='copy'):
    """Instant local rsync transfer with folder-level parallelism and NAS support."""





    enable_folder_parallel = PARALLEL_TRANSFER_CONFIG.get('enable_folder_parallel', False)
    folder_parallel_threshold = PARALLEL_TRANSFER_CONFIG.get('folder_parallel_threshold', 1000)

    if is_directory and enable_folder_parallel:

        try:
            file_count = sum(len(files) for _, _, files in os.walk(source_path))
            if file_count > folder_parallel_threshold:

                return transfer_directory_parallel(source_path, target_server, target_path, file_name, transfer_id, fast_ssh, mode)
        except:
            pass


    return transfer_single_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode)

def transfer_single_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode='copy'):
    """Single rsync transfer implementation."""



    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')


    target_is_windows = is_windows_server(target_server)


    rsync_opts = [
        '-a',
        '--inplace',
        '--whole-file',
        '--no-compress',
        '--numeric-ids',
        '--timeout=600',
        '-s',
        '--no-perms',
        '--no-owner',
        '--no-group',
        '--omit-dir-times',
    ]

    if target_is_windows:
        rsync_opts.append('--iconv=UTF-8,UTF-8')
    _append_rsync_progress_opts(rsync_opts)








    rsync_target_path = target_path
    if target_is_windows:
        normalized_target = normalize_windows_path_for_transfer(target_path)
        rsync_target_path = convert_windows_path_to_cygwin(normalized_target)
        print(f"🔄 Windows目标路径转换: {target_path} -> {rsync_target_path}")


    ssh_cmd = RSYNC_SSH_CMD


    target_port = SERVERS[target_server].get('port', 22)
    if target_port != 22:
        ssh_cmd = f"{ssh_cmd} -p {target_port}"

    if is_directory:
        target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/{file_name}/')
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, f'{source_path}/', target_spec]
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, f'{source_path}/', target_spec]
    else:
        target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/')
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, source_path, target_spec]
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, source_path, target_spec]


    part_id = f"rsync_{uuid.uuid4().hex}"
    return_code = _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id)
    if return_code != 0:
        raise Exception(f"rsync传输失败，退出码: {return_code}")


    return True

def transfer_directory_parallel(source_path, target_server, target_path, file_name, transfer_id, fast_ssh, mode='copy'):
    """In-directory parallel transfer implementation."""
    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    target_is_windows = is_windows_server(target_server)
    remote_target_root = target_path
    if target_is_windows:
        normalized = normalize_windows_path_for_transfer(target_path)
        remote_target_root = convert_windows_path_to_cygwin(normalized)

    emit_transfer_log(transfer_id, f'📁 启用目录内部并行传输: {file_name}')


    parallel_tasks = []

    try:

        items = os.listdir(source_path)
        subdirs = []
        files = []

        for item in items:
            item_path = os.path.join(source_path, item)
            if os.path.isdir(item_path):
                subdirs.append(item)
            else:
                files.append(item)


        for subdir in subdirs:
            parallel_tasks.append({
                'type': 'subdir',
                'source': os.path.join(source_path, subdir),
                'target_subpath': f'{file_name}/{subdir}',
                'name': subdir
            })


        if files:

            max_file_groups = 3
            group_size = max(1, len(files) // max_file_groups)

            for i in range(0, len(files), group_size):
                file_group = files[i:i + group_size]
                parallel_tasks.append({
                    'type': 'files',
                    'files': file_group,
                    'source_dir': source_path,
                    'target_subpath': file_name,
                    'name': f'文件组{i//group_size + 1}'
                })

        emit_transfer_log(transfer_id, f'📊 并行任务: {len(subdirs)}个子目录 + {len(files)}个文件 → {len(parallel_tasks)}个并行任务')


        max_workers = min(4, len(parallel_tasks))

        def execute_parallel_task(task):
            """Execute a single parallel task."""

            rsync_opts = ['-a', '--inplace', '--whole-file', '--no-compress', '--numeric-ids', '--timeout=600', '--no-perms', '--no-owner', '--no-group', '--omit-dir-times']
            if target_is_windows:
                rsync_opts.append('--iconv=UTF-8,UTF-8')

            if task['type'] == 'subdir':

                target_spec = build_remote_spec(target_server, target_user, f"{remote_target_root}/{task['target_subpath']}/")
                if target_password:
                    cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', RSYNC_SSH_CMD,
                        f"{task['source']}/", target_spec
                    ]
                else:
                    cmd = ['rsync'] + rsync_opts + ['-e', RSYNC_SSH_CMD,
                        f"{task['source']}/", target_spec
                    ]
            else:

                file_paths = [os.path.join(task['source_dir'], f) for f in task['files']]
                target_spec = build_remote_spec(target_server, target_user, f"{remote_target_root}/{task['target_subpath']}/")
                if target_password:
                    cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', RSYNC_SSH_CMD] + file_paths + [
                        target_spec
                    ]
                else:
                    cmd = ['rsync'] + rsync_opts + ['-e', RSYNC_SSH_CMD] + file_paths + [
                        target_spec
                    ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    return {'success': True, 'task_name': task['name']}
                else:
                    return {'success': False, 'task_name': task['name'], 'error': result.stderr}
            except Exception as e:
                return {'success': False, 'task_name': task['name'], 'error': str(e)}


        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(execute_parallel_task, task) for task in parallel_tasks]


            completed_tasks = 0
            failed_tasks = 0

            for future in concurrent.futures.as_completed(futures):

                if transfer_id not in active_transfers:

                    for f in futures:
                        f.cancel()
                    raise Exception("传输被用户取消")

                result = future.result()
                if result['success']:
                    completed_tasks += 1
                    emit_transfer_log(transfer_id, f'✅ 并行任务完成: {result["task_name"]}')
                else:
                    failed_tasks += 1
                    emit_transfer_log(transfer_id, f'❌ 并行任务失败: {result["task_name"]} - {result.get("error", "未知错误")}')

        if failed_tasks > 0:
            raise Exception(f"目录并行传输部分失败: {failed_tasks}/{len(parallel_tasks)} 任务失败")

        emit_transfer_log(transfer_id, f'🎉 目录并行传输完成: {completed_tasks}/{len(parallel_tasks)} 任务成功')

    except Exception as e:
        emit_transfer_log(transfer_id, f'⚠️ 目录并行传输失败，回退到单rsync: {str(e)}')

        return transfer_single_rsync(source_path, target_server, target_path, file_name, True, transfer_id, fast_ssh, mode='copy')

def transfer_file_via_remote_to_local_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode='copy'):
    """Transfer from remote server to TurboFile host using rsync pull."""



    source_user = SERVERS[source_server]['user']
    source_password = SERVERS[source_server].get('password')


    source_is_windows = is_windows_server(source_server)


    rsync_opts = [
        '-a',
        '--inplace',
        '--whole-file',
        '--no-compress',
        '--numeric-ids',
        '--timeout=600',
        '-s',
        '--no-perms',
        '--no-owner',
        '--no-group',
        '--omit-dir-times',
    ]
    if source_is_windows:
        rsync_opts.append('--iconv=UTF-8,UTF-8')
    _append_rsync_progress_opts(rsync_opts)


    rsync_source_path = source_path
    if source_is_windows:
        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")



    ssh_cmd = RSYNC_SSH_CMD
    source_port = SERVERS[source_server].get('port', 22)
    if source_port != 22:
        ssh_cmd = f"{ssh_cmd} -p {source_port}"

    if is_directory:
        source_spec = build_remote_spec(source_server, source_user, f'{rsync_source_path}/')
        if source_password:
            cmd = ['sshpass', '-p', source_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, source_spec, f'{target_path}/{file_name}/']
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, source_spec, f'{target_path}/{file_name}/']
    else:
        source_spec = build_remote_spec(source_server, source_user, rsync_source_path)
        if source_password:
            cmd = ['sshpass', '-p', source_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, source_spec, f'{target_path}/']
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, source_spec, f'{target_path}/']


    part_id = f"rsync_{uuid.uuid4().hex}"
    return_code = _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id)
    if return_code != 0:
        raise Exception(f"rsync传输失败，退出码: {return_code}")


    return True

def transfer_file_via_local_to_local_instant(source_path, target_path, file_name, is_directory, transfer_id, mode='copy'):
    """Local-to-local transfer using cp (copy) or mv (move).

    Args:
        source_path: source path
        target_path: target directory path
        file_name: file name
        is_directory: is a directory
        transfer_id: transfer ID
        mode: transfer mode, 'copy' or 'move'
    """
    import subprocess

    try:
        dest_path = os.path.join(target_path, file_name)

        if mode == 'move':

            print(f"[DEBUG] 本地剪切: {source_path} -> {dest_path}")

            emit_transfer_log(transfer_id, f'✂️ 本地到本地剪切，使用 mv 命令')


            mv_cmd = ['mv', '-f', source_path, target_path + '/']

            cmd_str = ' '.join(mv_cmd)
            print(f"[DEBUG] 执行命令: {cmd_str}")

            emit_transfer_log(transfer_id, f'📝 执行命令: {cmd_str}')

            result = subprocess.run(mv_cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "未知错误"
                print(f"[ERROR] mv失败: returncode={result.returncode}, stderr={error_msg}")
                raise Exception(f"本地剪切失败: {error_msg}")

            print(f"[DEBUG] mv成功: {file_name}")

            emit_transfer_log(transfer_id, f'✅ 本地剪切完成: {file_name}')
        else:

            if is_directory:

                print(f"[DEBUG] 本地目录复制: {source_path} -> {dest_path}")

                emit_transfer_log(transfer_id, f'📁 本地到本地复制，使用 cp -r 命令')


                cp_cmd = ['cp', '-r', source_path, target_path + '/']

                cmd_str = ' '.join(cp_cmd)
                print(f"[DEBUG] 执行命令: {cmd_str}")

                emit_transfer_log(transfer_id, f'📝 执行命令: {cmd_str}')

                result = subprocess.run(cp_cmd, capture_output=True, text=True, timeout=300)

                if result.returncode != 0:
                    error_msg = result.stderr.strip() if result.stderr else "未知错误"
                    print(f"[ERROR] cp -r失败: returncode={result.returncode}, stderr={error_msg}")
                    raise Exception(f"本地目录复制失败: {error_msg}")

                print(f"[DEBUG] cp -r成功: {file_name}")
            else:

                print(f"[DEBUG] 本地文件复制: {source_path} -> {dest_path}")


                cp_cmd = ['cp', '-f', source_path, dest_path]

                print(f"[DEBUG] 执行命令: {' '.join(cp_cmd)}")
                result = subprocess.run(cp_cmd, capture_output=True, text=True, timeout=60)

                if result.returncode != 0:
                    error_msg = result.stderr.strip() if result.stderr else "未知错误"
                    print(f"[ERROR] cp失败: returncode={result.returncode}, stderr={error_msg}")
                    raise Exception(f"本地文件复制失败: {error_msg}")

                print(f"[DEBUG] cp成功: {file_name}")

            emit_transfer_log(transfer_id, f'✅ 本地复制完成: {file_name}')

        print(f"[DEBUG] transfer_file_via_local_to_local_instant返回True")
        return True

    except subprocess.TimeoutExpired:
        error_msg = f"本地操作超时: {file_name}"
        print(f"[ERROR] {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"本地操作失败: {str(e)}"
        print(f"[ERROR] {error_msg}")
        raise Exception(error_msg)

def transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode='copy'):
    """Instant remote rsync transfer without progress monitoring, tuned for speed.

    Args:
        mode: transfer mode, 'copy' or 'move'
    """
    print(f"🔍 远程传输检查: 源={source_server}, 目标={target_server}, 模式={mode}")


    if source_server == target_server:
        print(f"🔍 检测到源和目标是同一台服务器: {source_server}")


        is_windows = is_windows_server(source_server)

        dest_path = os.path.join(target_path, file_name)

        if mode == 'move':

            if is_windows:

                print(f"🪟 Windows服务器使用move命令进行本地剪切")
                emit_transfer_log(transfer_id, f'✂️ 在Windows服务器上使用move剪切: {file_name}')

                src_cmd_path = normalize_windows_path_for_cmd(source_path)
                dest_cmd_path = normalize_windows_path_for_cmd(dest_path)

                ps_src = src_cmd_path.replace("'", "''")
                ps_dst = dest_cmd_path.replace("'", "''")
                ps_script = (
                    "$ErrorActionPreference='Stop';"
                    f"$src='{ps_src}';$dst='{ps_dst}';"
                    "$same=[string]::Equals($src.TrimEnd('\\','/'),$dst.TrimEnd('\\','/'),"
                    "[System.StringComparison]::InvariantCultureIgnoreCase);"
                    "if($same){exit 0};"
                    "if(Test-Path -LiteralPath $dst){Remove-Item -LiteralPath $dst -Force -Recurse -ErrorAction SilentlyContinue};"
                    "Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop"
                )
                remote_cmd = f'powershell -NoProfile -Command "{ps_script}"'
            else:

                print(f"🐧 Linux服务器使用mv命令进行本地剪切")
                emit_transfer_log(transfer_id, f'✂️ 在Linux服务器上使用mv剪切: {file_name}')


                remote_cmd = f"mv -f {shlex.quote(source_path)} {shlex.quote(target_path + '/')}"

            print(f"[DEBUG] 同服务器剪切命令: {remote_cmd}")
        else:

            if is_windows:
                print(f"🪟 Windows服务器使用copy/xcopy进行本地复制")
                emit_transfer_log(transfer_id, f'📁 在Windows服务器上使用copy/xcopy复制: {file_name}')

                src_cmd_path = normalize_windows_path_for_cmd(source_path)
                dest_cmd_path = normalize_windows_path_for_cmd(dest_path)
                if is_directory:

                    remote_cmd = f'xcopy "{src_cmd_path}" "{dest_cmd_path}" /E /I /Y /Q'
                else:
                    remote_cmd = f'copy /Y "{src_cmd_path}" "{dest_cmd_path}"'
            else:

                print(f"🐧 Linux服务器使用cp命令进行本地复制")
                emit_transfer_log(transfer_id, f'📁 在Linux服务器上使用cp复制: {file_name}')

                if is_directory:

                    remote_cmd = f"cp -r {shlex.quote(source_path)} {shlex.quote(target_path + '/')}"
                else:

                    remote_cmd = f"cp -f {shlex.quote(source_path)} {shlex.quote(dest_path)}"

            print(f"[DEBUG] 同服务器复制命令: {remote_cmd}")


        try:
            output, error, exit_code = ssh_manager.execute_command(source_server, remote_cmd)


            if mode == 'move':

                if is_windows:

                    if exit_code != 0 or (error and 'cannot find' in error.lower()):
                        err_msg = error or f"exit_code={exit_code}"
                        print(f"[ERROR] move失败: {err_msg}")
                        raise Exception(f"move剪切失败: {err_msg}")
                    else:
                        print(f"[DEBUG] move成功")
                else:

                    if exit_code != 0:
                        err_msg = error or f"exit_code={exit_code}"
                        print(f"[ERROR] mv失败: {err_msg}")
                        raise Exception(f"mv剪切失败: {err_msg}")
                    else:
                        print(f"[DEBUG] mv成功")

                emit_transfer_log(transfer_id, f'✅ 同服务器剪切完成: {file_name}')
            else:

                if is_windows:
                    if exit_code != 0:
                        err_msg = error or output or f"exit_code={exit_code}"
                        print(f"[ERROR] copy/xcopy失败: {err_msg}")
                        raise Exception(f"copy/xcopy复制失败: {err_msg}")
                    else:
                        print(f"[DEBUG] copy/xcopy成功")
                else:

                    if exit_code != 0:
                        err_msg = error or f"exit_code={exit_code}"
                        print(f"[ERROR] cp失败: {err_msg}")
                        raise Exception(f"cp复制失败: {err_msg}")
                    else:
                        print(f"[DEBUG] cp成功")

                emit_transfer_log(transfer_id, f'✅ 同服务器复制完成: {file_name}')

            return True

        except Exception as e:
            operation = "剪切" if mode == 'move' else "复制"
            error_msg = f"同服务器{operation}失败: {str(e)}"
            print(f"[ERROR] {error_msg}")
            emit_transfer_log(transfer_id, f'❌ {error_msg}')
            raise Exception(error_msg)



    print(f"🔄 使用rsync传输方案")


    source_is_windows = is_windows_server(source_server)
    target_is_windows = is_windows_server(target_server)

    print(f"🔍 Windows检测结果: 源是Windows={source_is_windows}, 目标是Windows={target_is_windows}")

    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')
    source_user = SERVERS[source_server]['user']
    source_password = SERVERS[source_server].get('password')


    rsync_base_opts = [
        "-a",
        "--inplace",
        "--whole-file",
        "--no-compress",
        "--numeric-ids",
        "--timeout=600",
        "-s",
        "--no-perms",
        "--no-owner",
        "--no-group",
        "--omit-dir-times",
    ]

    if source_is_windows or target_is_windows:
        rsync_base_opts.append("--iconv=UTF-8,UTF-8")
    _append_rsync_progress_opts(rsync_base_opts)


    if source_is_windows and not target_is_windows:
        emit_transfer_log(transfer_id, '🔁 检测到Windows作为源，切换为在目标Linux上运行rsync从Windows拉取')

        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")


        sshpass_cmd = "sshpass"


        ssh_to_source = RSYNC_SSH_CMD

        source_port = SERVERS[source_server].get('port', 22)
        if source_port != 22:
            ssh_to_source = f"{ssh_to_source} -p {source_port}"
        directory_source_spec = build_remote_spec(source_server, source_user, f'{rsync_source_path}/')
        file_source_spec = build_remote_spec(source_server, source_user, rsync_source_path)
        if is_directory:
            if source_password:
                remote_cmd = f"{sshpass_cmd} -p {shlex.quote(source_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(directory_source_spec)} {shlex.quote(f'{target_path}/{file_name}/')}"
            else:
                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(directory_source_spec)} {shlex.quote(f'{target_path}/{file_name}/')}"
        else:
            if source_password:
                remote_cmd = f"{sshpass_cmd} -p {shlex.quote(source_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(file_source_spec)} {shlex.quote(f'{target_path}/')}"
            else:
                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(file_source_spec)} {shlex.quote(f'{target_path}/')}"

        print(f"🔄 目标服务器执行的拉取命令: {remote_cmd}")


        ssh = ssh_manager.get_connection(target_server)
        if not ssh:
            raise Exception(f"无法连接到目标服务器 {target_server}")

        start_time = time.time()
        part_id = f"rsync_{uuid.uuid4().hex}"
        exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
        end_time = time.time()
        transfer_duration = end_time - start_time
        print(f"📊 拉取完成 - 耗时: {transfer_duration:.2f}秒, 状态: {exit_status}")
        if error:
            print(f"⚠️ 错误信息: {error}")

        emit_transfer_log(transfer_id, f'✅ {file_name} 传输完成')
        if exit_status != 0:
            raise Exception(f"rsync拉取失败，退出码: {exit_status}, 错误: {error}")
        return True




    rsync_source_path = source_path
    if source_is_windows:
        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")

    rsync_target_path = target_path
    if target_is_windows:
        rsync_target_path = convert_windows_path_to_cygwin(target_path)
        print(f"🔄 Windows目标路径转换: {target_path} -> {rsync_target_path}")


    sshpass_cmd = "sshpass"



    ssh_to_target = RSYNC_SSH_CMD
    target_port = SERVERS[target_server].get('port', 22)
    if target_port != 22:
        ssh_to_target = f"{ssh_to_target} -p {target_port}"
    directory_target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/{file_name}/')
    file_target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/')

    if is_directory:
        if target_password:
            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(f'{rsync_source_path}/')} {shlex.quote(directory_target_spec)}"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(f'{rsync_source_path}/')} {shlex.quote(directory_target_spec)}"
    else:
        if target_password:
            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(rsync_source_path)} {shlex.quote(file_target_spec)}"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(rsync_source_path)} {shlex.quote(file_target_spec)}"

    print(f"🔄 远程rsync命令: {remote_cmd}")

    start_time = time.time()
    ssh = ssh_manager.get_connection(source_server)
    if not ssh:
        raise Exception(f"无法连接到源服务器 {source_server}")
    part_id = f"rsync_{uuid.uuid4().hex}"
    exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
    end_time = time.time()
    transfer_duration = end_time - start_time
    print(f"📊 传输完成 - 耗时: {transfer_duration:.2f}秒")
    print(f"📊 退出状态: {exit_status}")
    if error:
        print(f"⚠️ 错误信息: {error}")
    emit_transfer_log(transfer_id, f'✅ {file_name} 传输完成')
    if exit_status != 0:
        raise Exception(f"rsync传输失败，退出码: {exit_status}, 错误: {error}")
    return True

def transfer_file_batch(transfer_id, source_server, file_batch, target_server, target_path, mode="copy", fast_ssh=True):
    """Batch transfer small files."""
    completed = 0
    failed = 0

    for file_info in file_batch:
        try:

            if transfer_id not in active_transfers:
                break

            result = transfer_single_file(transfer_id, source_server, file_info, target_server, target_path, mode, fast_ssh)
            completed += result['completed_files']
            failed += result['failed_files']

        except Exception as e:
            failed += 1
            emit_transfer_log(transfer_id, f'❌ 批量传输失败: {str(e)}')

    return {'completed_files': completed, 'failed_files': failed}

def transfer_file_via_remote_rsync(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, mode='copy'):
    """Transfer files via remote rsync."""



    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')


    source_is_windows = is_windows_server(source_server)
    target_is_windows = is_windows_server(target_server)


    ssh_cmd = RSYNC_SSH_CMD


    target_port = SERVERS[target_server].get('port', 22)
    if target_port != 22:
        ssh_cmd = f"{ssh_cmd} -p {target_port}"
        print(f"🔧 目标服务器使用自定义端口: {target_port}")


    rsync_base_opts = [
        "-a",
        "--inplace",
        "--whole-file",
        "--no-compress",
        "--numeric-ids",
        "--timeout=600",
        "-s",
        "--no-perms",
        "--no-owner",
        "--no-group",
        "--omit-dir-times",
    ]
    if source_is_windows or target_is_windows:
        rsync_base_opts.append("--iconv=UTF-8,UTF-8")
    _append_rsync_progress_opts(rsync_base_opts)


    sshpass_cmd = "sshpass"
    directory_target_spec = build_remote_spec(target_server, target_user, f'{target_path}/{file_name}/')
    file_target_spec = build_remote_spec(target_server, target_user, f'{target_path}/')


    if is_directory:
        if target_password:
            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_cmd)} {shlex.quote(f'{source_path}/')} {shlex.quote(directory_target_spec)}"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_cmd)} {shlex.quote(f'{source_path}/')} {shlex.quote(directory_target_spec)}"
    else:
        if target_password:
            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_cmd)} {shlex.quote(source_path)} {shlex.quote(file_target_spec)}"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_cmd)} {shlex.quote(source_path)} {shlex.quote(file_target_spec)}"


    ssh = ssh_manager.get_connection(source_server)
    if not ssh:
        raise Exception(f"无法连接到源服务器 {source_server}")

    start_time = time.time()


    part_id = f"rsync_{uuid.uuid4().hex}"
    exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
    if exit_status != 0:
        raise Exception(f"rsync传输失败 (退出码: {exit_status}): {error}")

def start_sequential_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """Original sequential transfer logic (fallback)."""
    total_files = len(source_files)
    completed_files = 0




    if (is_windows_server(source_server) or is_windows_server(target_server)):
        speed_simulator.init_transfer_speed(transfer_id, 50.0, 55.0)
    else:
        speed_simulator.init_transfer_speed(transfer_id)

    for file_info in source_files:

        if transfer_id not in active_transfers:
            print(f"传输 {transfer_id} 已被取消")
            return

        source_path = file_info['path']
        file_name = file_info['name']
        is_directory = file_info['is_directory']


        is_local_source = is_local_server(source_server)
        is_local_target = is_local_server(target_server)

        if is_local_source and not is_local_target:
            transfer_mode = 'local_to_remote'
        elif not is_local_source and is_local_target:
            transfer_mode = 'remote_to_local'
        elif is_local_source and is_local_target:
            transfer_mode = 'local_to_local'
        else:
            transfer_mode = 'remote_to_remote'

        simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
        elapsed_time = time_tracker.get_elapsed_time(transfer_id)





        is_local_source = is_local_server(source_server)
        is_local_target = is_local_server(target_server)

        if transfer_mode == 'local_to_local':

            operation = "剪切" if mode == "move" else "复制"
            cmd_name = "mv" if mode == "move" else "cp"
            print(f"📍 顺序传输-本地到本地{operation}: {source_path} -> {target_path}")
            emit_transfer_log(transfer_id, f'🔄 本地到本地传输，使用{cmd_name}命令')
            success = transfer_file_via_local_to_local_instant(source_path, target_path, file_name, is_directory, transfer_id, mode)
            if not success:
                raise Exception(f"本地到本地{operation}失败")
        elif is_local_source:

            success = transfer_file_via_local_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, completed_files, total_files, mode)
            if not success:
                raise Exception("本地传输失败")
        else:

            if source_server == target_server:

                is_windows = is_windows_server(source_server)
                if is_windows:
                    import ntpath
                    dest_path = ntpath.join(target_path, file_name)
                    src_cmd_path = normalize_windows_path_for_cmd(source_path)
                    dest_cmd_path = normalize_windows_path_for_cmd(dest_path)
                    if mode == "move":
                        emit_transfer_log(transfer_id, f'✂️ 同服务器剪切（Windows），使用move: {file_name}')
                        ps_src = src_cmd_path.replace("'", "''")
                        ps_dst = dest_cmd_path.replace("'", "''")
                        ps_script = (
                            "$ErrorActionPreference='Stop';"
                            f"$src='{ps_src}';$dst='{ps_dst}';"
                            "$same=[string]::Equals($src.TrimEnd('\\','/'),$dst.TrimEnd('\\','/'),"
                            "[System.StringComparison]::InvariantCultureIgnoreCase);"
                            "if($same){exit 0};"
                            "if(Test-Path -LiteralPath $dst){Remove-Item -LiteralPath $dst -Force -Recurse -ErrorAction SilentlyContinue};"
                            "Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop"
                        )
                        remote_cmd = f'powershell -NoProfile -Command "{ps_script}"'
                    else:
                        emit_transfer_log(transfer_id, f'📁 同服务器复制（Windows），使用copy/xcopy: {file_name}')
                        if is_directory:

                            remote_cmd = f'xcopy "{src_cmd_path}" "{dest_cmd_path}" /E /I /Y /Q'
                        else:
                            remote_cmd = f'copy /Y "{src_cmd_path}" "{dest_cmd_path}"'
                else:
                    dest_path = os.path.join(target_path, file_name)
                    if mode == "move":
                        emit_transfer_log(transfer_id, f'✂️ 同服务器剪切（Linux），使用mv: {file_name}')
                        remote_cmd = f"mv -f {shlex.quote(source_path)} {shlex.quote(dest_path)}"
                    else:
                        emit_transfer_log(transfer_id, f'📁 同服务器复制（Linux），使用cp: {file_name}')
                        if is_directory:
                            remote_cmd = f"cp -r {shlex.quote(source_path)} {shlex.quote(dest_path)}"
                        else:
                            remote_cmd = f"cp -f {shlex.quote(source_path)} {shlex.quote(dest_path)}"

                stdout, stderr, exit_code = ssh_manager.execute_command(source_server, remote_cmd)
                if exit_code != 0:
                    err_msg = stderr or stdout or f"exit_code={exit_code}"
                    raise Exception(f"同服务器{'剪切' if mode == 'move' else '复制'}失败: {err_msg}")

                emit_transfer_log(transfer_id, f'✅ 同服务器{"剪切" if mode == "move" else "复制"}完成: {file_name}')
            else:


                print(f"🔄 并行传输使用rsync方案")

                target_user = SERVERS[target_server]['user']
                target_password = SERVERS[target_server].get('password')
                source_user = SERVERS[source_server]['user']
                source_password = SERVERS[source_server].get('password')


                ssh_to_target = RSYNC_SSH_CMD


                target_port = SERVERS[target_server].get('port', 22)
                if target_port != 22:
                    ssh_to_target = f"{ssh_to_target} -p {target_port}"
                    print(f"🔧 目标服务器使用自定义端口: {target_port}")


                rsync_base_opts = [
                    "-a",
                    "--inplace",
                    "--whole-file",
                    "--no-compress",
                    "--numeric-ids",
                    "--timeout=600",
                    "-s",
                    "--no-perms",
                    "--no-owner",
                    "--no-group",
                    "--omit-dir-times",
                ]

                source_is_windows = is_windows_server(source_server)
                target_is_windows = is_windows_server(target_server)
                if source_is_windows or target_is_windows:
                    rsync_base_opts.append("--iconv=UTF-8,UTF-8")
                _append_rsync_progress_opts(rsync_base_opts)


                if source_is_windows and not target_is_windows:

                    sshpass_cmd = "sshpass"

                    ssh_to_source = RSYNC_SSH_CMD


                    source_port = SERVERS[source_server].get('port', 22)
                    if source_port != 22:
                        ssh_to_source = f"{ssh_to_source} -p {source_port}"
                        print(f"🔧 源服务器使用自定义端口: {source_port}")

                    rsync_source_path = convert_windows_path_to_cygwin(source_path)
                    directory_source_spec = build_remote_spec(source_server, source_user, f'{rsync_source_path}/')
                    file_source_spec = build_remote_spec(source_server, source_user, rsync_source_path)
                    if is_directory:
                        if source_password:
                            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(source_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(directory_source_spec)} {shlex.quote(f'{target_path}/{file_name}/')}"
                        else:
                            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(directory_source_spec)} {shlex.quote(f'{target_path}/{file_name}/')}"
                    else:
                        if source_password:
                            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(source_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(file_source_spec)} {shlex.quote(f'{target_path}/')}"
                        else:
                            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_source)} {shlex.quote(file_source_spec)} {shlex.quote(f'{target_path}/')}"


                    ssh = ssh_manager.get_connection(target_server)
                    if not ssh:
                        raise Exception(f"无法连接到目标服务器 {target_server}")
                else:


                    sshpass_cmd = "sshpass"


                    rsync_target_path = convert_windows_path_to_cygwin(target_path) if target_is_windows else target_path
                    rsync_source_path = convert_windows_path_to_cygwin(source_path) if source_is_windows else source_path
                    directory_target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/{file_name}/')
                    file_target_spec = build_remote_spec(target_server, target_user, f'{rsync_target_path}/')

                    if is_directory:
                        if target_password:
                            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(f'{rsync_source_path}/')} {shlex.quote(directory_target_spec)}"
                        else:
                            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(f'{rsync_source_path}/')} {shlex.quote(directory_target_spec)}"
                    else:
                        if target_password:
                            remote_cmd = f"{sshpass_cmd} -p {shlex.quote(target_password)} rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(rsync_source_path)} {shlex.quote(file_target_spec)}"
                        else:
                            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e {shlex.quote(ssh_to_target)} {shlex.quote(rsync_source_path)} {shlex.quote(file_target_spec)}"


                    ssh = ssh_manager.get_connection(source_server)
                    if not ssh:
                        raise Exception(f"无法连接到源服务器 {source_server}")

                import time
                start_time = time.time()

                emit_transfer_log(transfer_id, f'⚡️ 开始传输 {file_name}...')


                part_id = f"rsync_{uuid.uuid4().hex}"
                exit_status, error = _run_remote_rsync_with_progress(ssh, remote_cmd, transfer_id, part_id)
                if exit_status != 0:
                    raise Exception(f"传输 {file_name} 失败: {error}")


                end_time = time.time()
                duration = end_time - start_time

                emit_transfer_log(transfer_id, f'✅ {file_name} 传输完成')

        completed_files += 1



        need_delete_source = mode == "move" and not (transfer_mode == 'local_to_local' or (transfer_mode == 'remote_to_remote' and source_server == target_server))

        if need_delete_source:
            try:
                if is_local_server(source_server):

                    import shutil
                    if is_directory:
                        shutil.rmtree(source_path)
                    else:
                        os.remove(source_path)
                    emit_transfer_log(transfer_id, f'🗑️ 已删除源文件: {file_name}')
                else:

                    is_windows = is_windows_server(source_server)
                    if is_windows:

                        win_path = normalize_windows_path_for_cmd(source_path)


                        ps_path = win_path.replace('\\', '\\\\')
                        ps_check_cmd = f'powershell -Command "if (Test-Path -Path \'{ps_path}\' -PathType Container) {{ Write-Output \'DIR\' }} elseif (Test-Path -Path \'{ps_path}\' -PathType Leaf) {{ Write-Output \'FILE\' }} else {{ Write-Output \'NOTFOUND\' }}"'
                        ps_stdout, ps_stderr, ps_exit = ssh_manager.execute_command(source_server, ps_check_cmd)

                        is_dir = False
                        if ps_exit == 0 and ps_stdout:
                            result = ps_stdout.strip().upper()
                            if result == 'DIR':
                                is_dir = True
                            elif result == 'NOTFOUND':
                                emit_transfer_log(transfer_id, f'⚠️ 源文件不存在: {file_name}')
                                return


                        if is_dir:
                            delete_cmd = f'rd /s /q "{win_path}"'
                        else:
                            delete_cmd = f'del /f /q "{win_path}"'

                        emit_transfer_log(transfer_id, f'🗑️ 执行Windows删除命令: {delete_cmd}')
                    else:

                        delete_cmd = f"rm -rf {shlex.quote(source_path)}"
                        emit_transfer_log(transfer_id, f'🗑️ 执行Linux删除命令: {delete_cmd}')

                    stdout, stderr, exit_code = ssh_manager.execute_command(source_server, delete_cmd)
                    if exit_code == 0:
                        emit_transfer_log(transfer_id, f'✅ 已删除源文件: {file_name}')
                    else:
                        emit_transfer_log(transfer_id, f'❌ 删除源文件失败: {stderr}')
            except Exception as e:
                emit_transfer_log(transfer_id, f'❌ 删除源文件异常: {str(e)}')

    # Stop transfer timing.
    total_time = time_tracker.end_transfer(transfer_id)

    _clear_transfer_listing_cache(source_server, target_server, source_files, target_path, mode)


    print(f"[性能监控] 传输ID: {transfer_id}")
    print(f"[性能监控] 文件数量: {len(source_files)}")
    print(f"[性能监控] 传输时间: {total_time}")


    emit_transfer_bytes_snapshot(transfer_id)
    socketio.emit('transfer_complete', {
        'transfer_id': transfer_id,
        'status': 'success',
        'message': f'成功传输 {len(source_files)} 个文件/文件夹',
        'total_time': total_time
    })


    try:
        meta = active_transfers.get(transfer_id, {})
        client_ip = meta.get('client_ip', '未知')
        target_base = meta.get('target_path', target_path)
        mode_meta = meta.get('mode', mode)
        files_meta = meta.get('source_files', source_files)
        if isinstance(files_meta, list):
            file_names = ','.join([f.get('name', '') for f in files_meta if isinstance(f, dict)])
            source_paths = ';'.join([f.get('path', '') for f in files_meta if isinstance(f, dict)])
        else:
            file_names = ''
            source_paths = ''

        append_transfer_log_record(
            source_ip=source_server,
            target_ip=target_server,
            source_path=source_paths or target_base,
            target_full_path=target_base,
            duration_sec=_hhmmss_to_seconds(total_time),
            status='success',
            error="",
            client_ip=client_ip,
            mode=mode_meta,
            file_name=file_names or (files_meta[0].get('name', '') if isinstance(files_meta, list) and files_meta else ''),
            action='transfer'
        )
    except Exception:
        pass

def format_file_size(bytes_str):
    """Convert bytes to human-readable size."""
    try:

        bytes_num = int(bytes_str.replace(',', ''))


        if bytes_num < 1024 * 1024:
            return f"{bytes_num / 1024:.1f} KB"
        elif bytes_num < 1024 * 1024 * 1024:
            return f"{bytes_num / (1024 * 1024):.1f} MB"
        elif bytes_num < 1024 * 1024 * 1024 * 1024:
            return f"{bytes_num / (1024 * 1024 * 1024):.2f} GB"
        else:
            return f"{bytes_num / (1024 * 1024 * 1024 * 1024):.2f} TB"
    except (ValueError, AttributeError):
        return bytes_str

def parse_rsync_progress(line):
    """Parse rsync progress output (--info=progress2)."""
    import re


    # "  1,234,567  89%   12.34MB/s    0:00:05"
    progress2_pattern = r'\s*(\d+(?:,\d+)*)\s+(\d+)%\s+([\d.]+\w+/s)\s+(\d+:\d+:\d+)'
    match = re.search(progress2_pattern, line)

    if match:
        bytes_transferred = match.group(1)
        percentage = int(match.group(2))
        speed = match.group(3)
        eta = match.group(4)

        return {
            'type': 'progress',
            'bytes_transferred': bytes_transferred,
            'bytes_transferred_formatted': format_file_size(bytes_transferred),
            'percentage': percentage,
            'speed': speed,
            'eta': eta,
            'message': f"进度: {percentage}% | 速度: {speed} | 剩余: {eta}"
        }


    # "    32,768  26%  100.00kB/s    0:00:00      122,934 100%  400.00kB/s    0:00:00 (xfr#1, ir-chk=1000/2000)"
    detailed_pattern = r'(\d+,?\d*)\s+(\d+)%\s+([\d.]+\w+/s)\s+(\d+:\d+:\d+)\s+(\d+,?\d*)\s+(\d+)%\s+([\d.]+\w+/s)\s+(\d+:\d+:\d+)\s+\(xfr#(\d+),\s+ir-chk=(\d+)/(\d+)\)'
    match = re.search(detailed_pattern, line)

    if match:
        final_percent = int(match.group(6))
        final_speed = match.group(7)
        files_transferred = int(match.group(9))
        files_remaining = int(match.group(10))
        total_files = int(match.group(11))

        return {
            'type': 'progress',
            'percentage': final_percent,
            'speed': final_speed,
            'files_transferred': files_transferred,
            'files_remaining': files_remaining,
            'total_files': total_files,
            'message': f"进度: {final_percent}% | 速度: {final_speed} | 文件: {files_transferred}/{total_files}"
        }


    if "sent" in line and "received" in line and "bytes/sec" in line:
        return {
            'type': 'summary',
            'message': f"传输完成: {line}"
        }

    return None


def _human_readable_size(num_bytes):
    try:
        num_bytes = float(num_bytes)
    except Exception:
        return f"{num_bytes} bytes"
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    idx = 0
    while num_bytes >= 1024 and idx < len(units) - 1:
        num_bytes /= 1024.0
        idx += 1
    return f"{num_bytes:.2f} {units[idx]}"

def _escape_pwsh_literal(path: str) -> str:

    return path.replace("'", "''")

def _human_timestamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def _clamp_terminal_cols(cols):
    try:
        cols = int(cols)
    except Exception:
        cols = 120
    return max(40, min(cols, 400))

def _clamp_terminal_rows(rows):
    try:
        rows = int(rows)
    except Exception:
        rows = 30
    return max(10, min(rows, 200))

def _set_local_terminal_size(fd, rows, cols):
    try:
        if fd is None:
            return
        winsize = struct.pack('HHHH', _clamp_terminal_rows(rows), _clamp_terminal_cols(cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass

def _resolve_local_terminal_cwd(cwd: str) -> str:
    try:
        candidate = os.path.abspath(os.path.expanduser(cwd or ''))
        if candidate and os.path.isdir(candidate):
            return candidate
    except Exception:
        pass
    return os.path.expanduser('~')

def get_terminal_profile_options(server_ip: str):
    return list(TERMINAL_PROFILE_OPTIONS_WINDOWS if is_windows_server(server_ip) else TERMINAL_PROFILE_OPTIONS_POSIX)

def _normalize_terminal_profile_by_platform(is_windows: bool, profile: str = '') -> str:
    options = TERMINAL_PROFILE_OPTIONS_WINDOWS if is_windows else TERMINAL_PROFILE_OPTIONS_POSIX
    default_profile = 'powershell' if is_windows else 'bash'
    allowed = {str(option.get('id') or '').strip() for option in options}
    candidate = str(profile or '').strip().lower()
    return candidate if candidate in allowed else default_profile

def normalize_terminal_profile(server_ip: str, profile: str = '') -> str:
    return _normalize_terminal_profile_by_platform(is_windows_server(server_ip), profile)

def _build_bash_integration_rcfile_payload() -> str:
    return (
        "if [ -f /etc/bash.bashrc ]; then . /etc/bash.bashrc; fi\n"
        "if [ -f ~/.bashrc ]; then . ~/.bashrc; fi\n"
        "__turbofile_prompt(){\n"
        "  local _tf_ec=$?;\n"
        "  printf '\\033]777;cwd=%s\\007' \"$PWD\";\n"
        "  printf '\\033]777;status=%s\\007' \"$_tf_ec\";\n"
        "}\n"
        "if [ -n \"${PROMPT_COMMAND-}\" ]; then\n"
        "  PROMPT_COMMAND=\"__turbofile_prompt;${PROMPT_COMMAND}\"\n"
        "else\n"
        "  PROMPT_COMMAND=\"__turbofile_prompt\"\n"
        "fi\n"
        "PS0=$'\\033]777;command_start\\007'\n"
        "rm -f -- \"${BASH_SOURCE[0]}\" 2>/dev/null || true\n"
    )

def _build_linux_terminal_command(cwd: str, profile: str = 'bash') -> str:
    profile = _normalize_terminal_profile_by_platform(False, profile)
    target_dir = shlex.quote(cwd or '~')
    if profile == 'login':
        return (
            f'export TERM=xterm-256color COLORTERM=truecolor; '
            f'cd {target_dir} 2>/dev/null || cd ~; '
            f'exec ${{SHELL:-/bin/bash}} -il'
        )
    if profile == 'sh':
        return (
            f'export TERM=xterm-256color COLORTERM=truecolor; '
            f'cd {target_dir} 2>/dev/null || cd ~; '
            f'exec /bin/sh -i'
        )
    rc_payload = _build_bash_integration_rcfile_payload()
    script = (
        "rcfile=$(mktemp /tmp/turbofile-bashrc.XXXXXX 2>/dev/null || mktemp); "
        "cat > \"$rcfile\" <<'__TURBOFILE_BASH_RC__'\n"
        f"{rc_payload}"
        "__TURBOFILE_BASH_RC__\n"
        f"export TERM=xterm-256color COLORTERM=truecolor; "
        f"cd {target_dir} 2>/dev/null || cd ~; "
        f"exec bash --rcfile \"$rcfile\" -i"
    )
    return script

def _build_windows_shell_integration_prompt() -> str:
    return (
        "$global:__TurboFileOriginalPrompt = $function:prompt; "
        "function global:prompt { "
        "$tfCode = if ($?) { 0 } elseif ($LASTEXITCODE -is [int]) { [int]$LASTEXITCODE } else { 1 }; "
        "[Console]::Out.Write(\"`e]777;cwd=$($PWD.Path)`a\"); "
        "[Console]::Out.Write(\"`e]777;status=$tfCode`a\"); "
        "if ($global:__TurboFileOriginalPrompt) { & $global:__TurboFileOriginalPrompt } "
        "else { \"PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) \" } "
        "}; "
    )

def _build_windows_terminal_command(cwd: str, profile: str = 'powershell') -> str:
    profile = _normalize_terminal_profile_by_platform(True, profile)
    if profile == 'cmd':
        target_dir = normalize_windows_path_for_cmd(cwd or '').replace('"', '""')
        return f'cmd.exe /Q /K "chcp 65001>nul && cd /d \\"{target_dir}\\""'

    target_dir = _escape_pwsh_literal(normalize_windows_path_for_cmd(cwd or ''))
    script = (
        "$PSStyle.OutputRendering = 'PlainText' 2>$null; "
        "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
        f"try {{ Set-Location -LiteralPath '{target_dir}' }} catch {{ Set-Location -LiteralPath $HOME }}; "
        f"{_build_windows_shell_integration_prompt()}"
        "Clear-Host"
    )
    escaped_script = script.replace('"', '`"')
    return (
        "powershell.exe -NoLogo -NoExit -NoProfile -ExecutionPolicy Bypass "
        f"-Command \"{escaped_script}\""
    )

def emit_terminal_output(terminal_id, data, final=False, exit_code=None, sid=None):
    """Push terminal output to the requesting client only."""
    try:
        payload = {
            'terminal_id': terminal_id,
            'data': data,
            'final': bool(final),
            'exit_code': exit_code
        }
        target_room = sid
        if not target_room:
            with TERMINAL_TASKS_LOCK:
                task = TERMINAL_TASKS.get(terminal_id)
                if task:
                    target_room = task.get('sid')
        if target_room:
            socketio.emit('terminal_output', payload, room=target_room)
    except Exception as e:
        print(f"❌ 发送终端输出失败: {e}")

def emit_terminal_status(terminal_id, status, message='', sid=None, extra=None):
    """Push terminal status updates to the requesting client only."""
    try:
        payload = {
            'terminal_id': terminal_id,
            'status': status,
            'message': message or ''
        }
        if isinstance(extra, dict):
            payload.update(extra)
        target_room = sid
        if not target_room:
            with TERMINAL_TASKS_LOCK:
                task = TERMINAL_TASKS.get(terminal_id)
                if task:
                    target_room = task.get('sid')
        if target_room:
            socketio.emit('terminal_status', payload, room=target_room)
    except Exception as e:
        print(f"❌ 发送终端状态失败: {e}")

def _get_terminal_task(terminal_id):
    with TERMINAL_TASKS_LOCK:
        return TERMINAL_TASKS.get(terminal_id)

def _pop_terminal_task(terminal_id):
    with TERMINAL_TASKS_LOCK:
        return TERMINAL_TASKS.pop(terminal_id, None)

def _terminal_session_payload(terminal_id, task):
    if not terminal_id or not isinstance(task, dict):
        return None
    detached_at = task.get('detached_at')
    detached_seconds = None
    if isinstance(detached_at, (int, float)):
        detached_seconds = max(0, int(time.time() - detached_at))
    return {
        'terminal_id': terminal_id,
        'server': task.get('server') or '',
        'host': task.get('host') or '',
        'panel': task.get('panel') or '',
        'cwd': task.get('cwd') or '',
        'profile': task.get('profile') or '',
        'client_token': task.get('client_token') or '',
        'browser_token': task.get('browser_token') or '',
        'detached': bool(task.get('sid') is None),
        'detached_seconds': detached_seconds,
        'opened_at': task.get('opened_at') or 0,
    }

def list_terminal_sessions_for_client(client_token: str):
    token = str(client_token or '').strip()
    if not token:
        return []
    sessions = []
    with TERMINAL_TASKS_LOCK:
        for terminal_id, task in TERMINAL_TASKS.items():
            if task.get('client_token') != token:
                continue
            payload = _terminal_session_payload(terminal_id, task)
            if payload:
                sessions.append(payload)
    sessions.sort(key=lambda item: (item.get('panel') or '', item.get('opened_at') or 0))
    return sessions

def list_active_terminal_sessions():
    sessions = []
    with TERMINAL_TASKS_LOCK:
        for terminal_id, task in TERMINAL_TASKS.items():
            payload = _terminal_session_payload(terminal_id, task)
            if payload:
                sessions.append(payload)
    sessions.sort(
        key=lambda item: (
            str(item.get('server') or ''),
            str(item.get('panel') or ''),
            float(item.get('opened_at') or 0),
        )
    )
    return sessions

def close_terminal_sessions_for_client_panel(client_token: str, panel: str):
    token = str(client_token or '').strip()
    panel_name = str(panel or '').strip()
    if not token or not panel_name:
        return
    with TERMINAL_TASKS_LOCK:
        terminal_ids = [
            terminal_id
            for terminal_id, task in TERMINAL_TASKS.items()
            if task.get('client_token') == token and task.get('panel') == panel_name
        ]
    for terminal_id in terminal_ids:
        close_terminal_session(terminal_id, emit_status=False)

def mark_terminal_sessions_detached_for_sid(sid):
    if not sid:
        return 0
    detached = 0
    now = time.time()
    with TERMINAL_TASKS_LOCK:
        for task in TERMINAL_TASKS.values():
            if task.get('sid') != sid:
                continue
            task['sid'] = None
            task['detached_at'] = now
            detached += 1
    return detached

def rebind_terminal_sessions(client_token: str, sid: str):
    token = str(client_token or '').strip()
    new_sid = str(sid or '').strip()
    if not token or not new_sid:
        return []
    rebound = []
    with TERMINAL_TASKS_LOCK:
        for terminal_id, task in TERMINAL_TASKS.items():
            if task.get('client_token') != token:
                continue
            task['sid'] = new_sid
            task['detached_at'] = None
            payload = _terminal_session_payload(terminal_id, task)
            if payload:
                rebound.append(payload)
    rebound.sort(key=lambda item: (item.get('panel') or '', item.get('opened_at') or 0))
    return rebound

def recover_detached_terminal_sessions_for_browser(browser_token: str, new_client_token: str, sid: str):
    browser = str(browser_token or '').strip()
    new_token = str(new_client_token or '').strip()
    new_sid = str(sid or '').strip()
    if not browser or not new_token or not new_sid:
        return []

    with TERMINAL_TASKS_LOCK:
        detached_items = [
            (terminal_id, task)
            for terminal_id, task in TERMINAL_TASKS.items()
            if task.get('browser_token') == browser and task.get('sid') is None
        ]
        if not detached_items:
            return []

        latest_task = max(
            detached_items,
            key=lambda item: float(item[1].get('detached_at') or item[1].get('opened_at') or 0)
        )[1]
        source_client_token = str(latest_task.get('client_token') or '').strip()
        if not source_client_token:
            return []

        rebound = []
        for terminal_id, task in TERMINAL_TASKS.items():
            if task.get('browser_token') != browser:
                continue
            if task.get('sid') is not None:
                continue
            if str(task.get('client_token') or '').strip() != source_client_token:
                continue
            task['sid'] = new_sid
            task['detached_at'] = None
            task['client_token'] = new_token
            payload = _terminal_session_payload(terminal_id, task)
            if payload:
                rebound.append(payload)

    rebound.sort(key=lambda item: (item.get('panel') or '', item.get('opened_at') or 0))
    return rebound

def _reap_detached_terminal_sessions():
    now = time.time()
    with TERMINAL_TASKS_LOCK:
        stale_ids = [
            terminal_id
            for terminal_id, task in TERMINAL_TASKS.items()
            if task.get('sid') is None
            and isinstance(task.get('detached_at'), (int, float))
            and (now - float(task.get('detached_at') or 0)) >= TERMINAL_DETACH_GRACE_SECONDS
        ]
    for terminal_id in stale_ids:
        close_terminal_session(terminal_id, emit_status=False)

def _terminal_reaper_loop():
    while True:
        try:
            _reap_detached_terminal_sessions()
        except Exception:
            pass
        time.sleep(TERMINAL_REAPER_INTERVAL_SECONDS)

def ensure_terminal_reaper_started():
    global _TERMINAL_REAPER_STARTED
    with _TERMINAL_REAPER_LOCK:
        if _TERMINAL_REAPER_STARTED:
            return
        threading.Thread(target=_terminal_reaper_loop, daemon=True, name='turbofile-terminal-reaper').start()
        _TERMINAL_REAPER_STARTED = True

def open_terminal_session(server_ip, cwd, rows, cols, sid=None, panel=None, client_token=None, browser_token=None, profile=None):
    """Create a terminal session and start background streaming."""
    terminal_id = f"term_{uuid.uuid4().hex}"
    rows = _clamp_terminal_rows(rows)
    cols = _clamp_terminal_cols(cols)
    is_local = is_local_server(server_ip)
    is_windows = is_windows_server(server_ip)
    host = get_server_host(server_ip)
    profile = normalize_terminal_profile(server_ip, profile)

    try:
        ensure_terminal_reaper_started()
        if is_local:
            work_dir = _resolve_local_terminal_cwd(cwd)
            master_fd, slave_fd = pty.openpty()
            _set_local_terminal_size(slave_fd, rows, cols)
            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'
            env['COLORTERM'] = 'truecolor'
            local_command = _build_windows_terminal_command(work_dir, profile) if is_windows else _build_linux_terminal_command(work_dir, profile)
            proc = subprocess.Popen(
                ['/bin/bash', '-lc', local_command],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=work_dir,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True
            )
            os.close(slave_fd)
            session = {
                'type': 'local',
                'server': server_ip,
                'host': host,
                'sid': sid,
                'panel': panel,
                'cwd': work_dir,
                'profile': profile,
                'client_token': str(client_token or '').strip(),
                'browser_token': str(browser_token or '').strip(),
                'opened_at': time.time(),
                'detached_at': None,
                'fd': master_fd,
                'process': proc,
                'encoding': 'utf-8',
                'closing': False,
            }
        else:
            ssh = ssh_manager.get_connection(server_ip)
            if not ssh:
                return None, f'无法连接到服务器 {server_ip}'
            transport = ssh.get_transport()
            if not transport or not transport.is_active():
                return None, f'服务器 {server_ip} 的 SSH 连接不可用'
            channel = transport.open_session(timeout=5)
            channel.get_pty(term='xterm-256color', width=cols, height=rows)
            command = _build_windows_terminal_command(cwd, profile) if is_windows else _build_linux_terminal_command(cwd, profile)
            channel.exec_command(command)
            session = {
                'type': 'remote',
                'server': server_ip,
                'host': host,
                'sid': sid,
                'panel': panel,
                'cwd': cwd or get_default_path(server_ip),
                'profile': profile,
                'client_token': str(client_token or '').strip(),
                'browser_token': str(browser_token or '').strip(),
                'opened_at': time.time(),
                'detached_at': None,
                'channel': channel,
                'encoding': 'utf-8',
                'closing': False,
            }

        with TERMINAL_TASKS_LOCK:
            TERMINAL_TASKS[terminal_id] = session

        socketio.start_background_task(stream_terminal_output, terminal_id)
        emit_terminal_status(
            terminal_id,
            'opened',
            sid=sid,
            extra={
                'server': server_ip,
                'host': host,
                'panel': panel,
                'cwd': session.get('cwd') or '',
                'profile': profile
            }
        )
        return terminal_id, None
    except Exception as e:
        return None, str(e)

def resize_terminal_session(terminal_id, rows, cols):
    """Resize a running terminal session."""
    session = _get_terminal_task(terminal_id)
    if not session:
        return False, '终端会话不存在'
    rows = _clamp_terminal_rows(rows)
    cols = _clamp_terminal_cols(cols)
    try:
        if session['type'] == 'local':
            _set_local_terminal_size(session.get('fd'), rows, cols)
        else:
            channel = session.get('channel')
            if channel:
                channel.resize_pty(width=cols, height=rows)
        return True, ''
    except Exception as e:
        return False, str(e)

def send_terminal_input(terminal_id, data):
    """Send raw terminal input bytes to a running session."""
    session = _get_terminal_task(terminal_id)
    if not session:
        return False, '终端会话不存在'
    payload = data or ''
    try:
        if session['type'] == 'local':
            fd = session.get('fd')
            if fd is None:
                return False, '本地终端不可写'
            os.write(fd, payload.encode('utf-8', errors='ignore'))
        else:
            channel = session.get('channel')
            if not channel:
                return False, '远程终端不可写'
            channel.send(payload)
        return True, ''
    except Exception as e:
        return False, str(e)

def close_terminal_session(terminal_id, emit_status=True):
    """Close a running terminal session."""
    session = _pop_terminal_task(terminal_id)
    if not session:
        return False, '终端会话不存在'

    session['closing'] = True
    try:
        if session['type'] == 'local':
            proc = session.get('process')
            fd = session.get('fd')
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if proc and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(0.2)
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        else:
            channel = session.get('channel')
            if channel:
                try:
                    channel.close()
                except Exception:
                    pass
        if emit_status:
            emit_terminal_status(terminal_id, 'closed', sid=session.get('sid'))
        return True, ''
    except Exception as e:
        return False, str(e)

def close_terminal_sessions_for_sid(sid):
    """Close all terminal sessions owned by the given socket id."""
    if not sid:
        return
    with TERMINAL_TASKS_LOCK:
        terminal_ids = [terminal_id for terminal_id, task in TERMINAL_TASKS.items() if task.get('sid') == sid]
    for terminal_id in terminal_ids:
        close_terminal_session(terminal_id, emit_status=False)

def stream_terminal_output(terminal_id):
    """Background thread: stream raw terminal output."""
    session = _get_terminal_task(terminal_id)
    if not session:
        return

    exit_code = 0
    final_note = '\r\n[终端已断开]\r\n'

    try:
        if session['type'] == 'local':
            proc = session.get('process')
            fd = session.get('fd')
            while True:
                if fd is None:
                    break
                rlist, _, _ = select.select([fd], [], [], TERMINAL_LOCAL_POLL_INTERVAL_SECONDS)
                if fd in rlist:
                    chunks = []
                    while True:
                        try:
                            data = os.read(fd, 65536)
                        except OSError:
                            data = b''
                        if not data:
                            break
                        chunks.append(data)
                        more_ready, _, _ = select.select([fd], [], [], 0)
                        if fd not in more_ready:
                            break
                    if chunks:
                        emit_terminal_output(
                            terminal_id,
                            b''.join(chunks).decode('utf-8', errors='replace'),
                            sid=session.get('sid')
                        )
                    elif proc is None or proc.poll() is not None:
                        break
                if proc is not None and proc.poll() is not None:
                    try:
                        tail_chunks = []
                        while True:
                            tail = os.read(fd, 4096)
                            if not tail:
                                break
                            tail_chunks.append(tail)
                        if tail_chunks:
                            emit_terminal_output(
                                terminal_id,
                                b''.join(tail_chunks).decode('utf-8', errors='replace'),
                                sid=session.get('sid')
                            )
                    except Exception:
                        pass
                    break
            if proc is not None:
                proc.wait(timeout=1)
                exit_code = proc.returncode
                final_note = f'\r\n[终端已结束，退出码 {exit_code}]\r\n'
        else:
            channel = session.get('channel')
            while channel is not None:
                had_data = False
                stdout_chunks = []
                stderr_chunks = []
                while channel.recv_ready():
                    data = channel.recv(65536)
                    if not data:
                        break
                    stdout_chunks.append(data)
                while channel.recv_stderr_ready():
                    data = channel.recv_stderr(65536)
                    if not data:
                        break
                    stderr_chunks.append(data)
                if stdout_chunks:
                    emit_terminal_output(
                        terminal_id,
                        b''.join(stdout_chunks).decode(session.get('encoding') or 'utf-8', errors='replace'),
                        sid=session.get('sid')
                    )
                    had_data = True
                if stderr_chunks:
                    emit_terminal_output(
                        terminal_id,
                        b''.join(stderr_chunks).decode(session.get('encoding') or 'utf-8', errors='replace'),
                        sid=session.get('sid')
                    )
                    had_data = True
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if not had_data:
                    time.sleep(TERMINAL_REMOTE_IDLE_SLEEP_SECONDS)
            if channel is not None:
                try:
                    exit_code = channel.recv_exit_status()
                    final_note = f'\r\n[终端已结束，退出码 {exit_code}]\r\n'
                except Exception:
                    final_note = '\r\n[终端已断开]\r\n'
    except Exception as e:
        final_note = f'\r\n[终端异常: {e}]\r\n'
        exit_code = -1
    finally:
        with TERMINAL_TASKS_LOCK:
            TERMINAL_TASKS.pop(terminal_id, None)
        emit_terminal_output(terminal_id, final_note, final=True, exit_code=exit_code, sid=session.get('sid'))
        emit_terminal_status(terminal_id, 'closed', extra={'exit_code': exit_code}, sid=session.get('sid'))

def emit_run_output(run_id, message, is_error=False, final=False, exit_code=None, sid=None):
    """Push runtime output to the requesting client only."""
    try:
        data = {
            'run_id': run_id,
            'message': message,
            'is_error': is_error,
            'final': final,
            'exit_code': exit_code
        }
        target_room = sid
        if not target_room:
            with RUN_TASKS_LOCK:
                task = RUN_TASKS.get(run_id)
                if task:
                    target_room = task.get('sid') or task.get('room')
        if target_room:
            socketio.emit('run_output', data, room=target_room)
        else:
            socketio.emit('run_output', data)
    except Exception as e:
        print(f"❌ 发送运行输出失败: {e}")


def stream_local_command(command, run_id, file_path, is_windows, sid=None):
    """Stream local command execution with merged stdout/stderr."""
    emit_run_output(run_id, f"▶️ 开始运行: {file_path}\n", is_error=False, final=False, sid=sid)
    work_dir = os.path.dirname(file_path) or None
    proc = None
    master_fd = None
    try:
        if not is_windows:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=work_dir,
                preexec_fn=os.setsid,
                close_fds=True
            )
            os.close(slave_fd)
            with RUN_TASKS_LOCK:
                RUN_TASKS[run_id] = {'type': 'local', 'process': proc, 'fd': master_fd, 'sid': sid}

            while True:
                rlist, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in rlist:
                    try:
                        data = os.read(master_fd, 1024)
                        if data:
                            try:
                                text = data.decode('utf-8', errors='replace')
                            except Exception:
                                text = str(data)
                            emit_run_output(run_id, text, is_error=False, final=False, sid=sid)
                        else:
                            break
                    except OSError:
                        break
                if proc.poll() is not None:

                    try:
                        while True:
                            data = os.read(master_fd, 1024)
                            if not data:
                                break
                            text = data.decode('utf-8', errors='replace')
                            emit_run_output(run_id, text, is_error=False, final=False, sid=sid)
                    except Exception:
                        pass
                    break

            exit_code = proc.returncode
            emit_run_output(run_id, f"\n[运行结束，退出码 {exit_code}]\n", is_error=exit_code != 0, final=True, exit_code=exit_code, sid=sid)
        else:
            proc = subprocess.Popen(
                command + " 2>&1",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                cwd=work_dir,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
            with RUN_TASKS_LOCK:
                RUN_TASKS[run_id] = {'type': 'local', 'process': proc, 'fd': None, 'sid': sid}

            if proc.stdout:
                for line in proc.stdout:
                    emit_run_output(run_id, line, is_error=False, final=False, sid=sid)
            proc.wait()
            exit_code = proc.returncode
            emit_run_output(run_id, f"\n[运行结束，退出码 {exit_code}]\n", is_error=exit_code != 0, final=True, exit_code=exit_code, sid=sid)
    except Exception as e:
        emit_run_output(run_id, f"运行异常: {e}\n", is_error=True, final=True, exit_code=-1, sid=sid)
    finally:
        with RUN_TASKS_LOCK:
            RUN_TASKS.pop(run_id, None)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except Exception:
                pass


def stream_remote_command(server_ip, command, run_id, file_path, is_windows, sid=None):
    """Stream remote command execution with merged stdout/stderr."""
    emit_run_output(run_id, f"▶️ 开始运行: {file_path}\n", is_error=False, final=False, sid=sid)
    ssh = ssh_manager.get_connection(server_ip)
    if not ssh:
        emit_run_output(run_id, f"无法连接到服务器 {server_ip}\n", is_error=True, final=True, exit_code=-1, sid=sid)
        return

    try:

        full_cmd = command + " 2>&1"
        stdin, stdout, stderr = ssh.exec_command(full_cmd, get_pty=True)
        with RUN_TASKS_LOCK:
            RUN_TASKS[run_id] = {'type': 'remote', 'channel': stdout.channel, 'server': server_ip, 'sid': sid}
        encoding = 'gbk' if is_windows else 'utf-8'


        while True:
            line_bytes = stdout.readline()
            if line_bytes:
                try:
                    line = line_bytes.decode(encoding, errors='replace')
                except Exception:
                    line = str(line_bytes)
                emit_run_output(run_id, line, is_error=False, final=False, sid=sid)
            else:
                if stdout.channel.exit_status_ready():
                    break
                time.sleep(0.05)

        exit_code = stdout.channel.recv_exit_status()
        emit_run_output(run_id, f"\n[运行结束，退出码 {exit_code}]\n", is_error=exit_code != 0, final=True, exit_code=exit_code, sid=sid)
    except Exception as e:
        emit_run_output(run_id, f"运行异常: {e}\n", is_error=True, final=True, exit_code=-1, sid=sid)
    finally:
        with RUN_TASKS_LOCK:
            RUN_TASKS.pop(run_id, None)


def stream_run_command(server_ip, command, file_path, run_id, is_windows, is_local, sid=None):
    """Background thread: stream command execution by server type."""
    if is_local:
        stream_local_command(command, run_id, file_path, is_windows, sid=sid)
    else:
        stream_remote_command(server_ip, command, run_id, file_path, is_windows, sid=sid)


def transfer_file_via_local_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, completed_files=0, total_files=1, mode='copy'):
    """High-speed local rsync transfer (same as original script)."""
    try:



        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')


        ssh_opts_str = RSYNC_SSH_CMD


        target_port = SERVERS[target_server].get('port', 22)
        if target_port != 22:
            ssh_opts_str = f"{ssh_opts_str} -p {target_port}"
            print(f"🔧 目标服务器使用自定义端口: {target_port}")


        final_target_path = target_path
        if is_windows_server(target_server):
            normalized = normalize_windows_path_for_transfer(target_path)
            final_target_path = convert_windows_path_to_cygwin(normalized)
            print(f"🔄 Windows目标路径转换(本地rsync): {target_path} -> {final_target_path}")


        if is_directory:

            source_with_slash = source_path.rstrip('/') + '/'
            target_full_path = f"{final_target_path}/{file_name}/"
        else:

            source_with_slash = source_path
            target_full_path = f"{final_target_path}/"


        rsync_opts = [
            '-a',
            '--inplace',
            '--whole-file',
            '--no-compress',
            '--numeric-ids',
            '--timeout=600',
            '-s',
            '--no-perms',
            '--no-owner',
            '--no-group',
            '--omit-dir-times',
        ]
        _append_rsync_progress_opts(rsync_opts)

        if target_password:

            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + [
                '-e', ssh_opts_str,
                source_with_slash,
                build_remote_spec(target_server, target_user, target_full_path)
            ]
        else:

            cmd = ['rsync'] + rsync_opts + [
                '-e', ssh_opts_str,
                source_with_slash,
                build_remote_spec(target_server, target_user, target_full_path)
            ]



        import time
        start_time = time.time()

        emit_transfer_log(transfer_id, f'⚡️ 开始传输 {file_name}...')

        part_id = f"rsync_{uuid.uuid4().hex}"
        return_code = _run_rsync_subprocess_with_progress(cmd, transfer_id, part_id)
        if return_code != 0:
            raise Exception(f"本地rsync传输失败，退出码: {return_code}")


        end_time = time.time()
        duration = end_time - start_time


        if duration < 60:
            time_str = f"{duration:.1f}秒"
        elif duration < 3600:
            minutes = int(duration // 60)
            seconds = duration % 60
            time_str = f"{minutes}分{seconds:.1f}秒"
        else:
            hours = int(duration // 3600)
            minutes = int((duration % 3600) // 60)
            seconds = duration % 60
            time_str = f"{hours}小时{minutes}分{seconds:.1f}秒"


        emit_transfer_log(transfer_id, f'✅ {file_name} 传输完成')

        return True

    except Exception as e:
        raise Exception(f"本地rsync传输失败: {str(e)}")

def transfer_file_via_paramiko(source_path, target_server, target_path, file_name, is_directory, transfer_id):
    """Transfer files with Paramiko (local to remote)."""
    ssh = ssh_manager.get_connection(target_server)
    if not ssh:
        raise Exception(f"无法连接到目标服务器 {target_server}")

    sftp = ssh.open_sftp()

    try:
        if is_directory:

            remote_dir_path = f"{target_path}/{file_name}"
            emit_transfer_log(transfer_id, f'正在传输目录: {file_name}')
            transfer_directory_to_remote(sftp, source_path, remote_dir_path, transfer_id)
        else:

            remote_file_path = f"{target_path}/{file_name}"
            emit_transfer_log(transfer_id, f'正在传输文件: {file_name}')
            sftp.put(source_path, remote_file_path)
    finally:
        sftp.close()



def transfer_directory_to_remote(sftp, local_dir, remote_dir, transfer_id):
    """Recursively transfer a directory to remote."""
    try:
        sftp.mkdir(remote_dir)
    except:
        pass

    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = f"{remote_dir}/{item}"

        if os.path.isfile(local_path):
            sftp.put(local_path, remote_path)
        elif os.path.isdir(local_path):
            transfer_directory_to_remote(sftp, local_path, remote_path, transfer_id)

def transfer_directory_from_remote(sftp, remote_dir, local_dir, transfer_id):
    """Recursively transfer a remote directory to local."""
    os.makedirs(local_dir, exist_ok=True)

    for item in sftp.listdir(remote_dir):
        remote_path = f"{remote_dir}/{item}"
        local_path = os.path.join(local_dir, item)

        try:
            stat = sftp.stat(remote_path)
            if stat.st_mode & 0o040000:
                transfer_directory_from_remote(sftp, remote_path, local_path, transfer_id)
            else:
                sftp.get(remote_path, local_path)
        except:
            pass


__all__ = [name for name in globals() if not name.startswith('__')]
