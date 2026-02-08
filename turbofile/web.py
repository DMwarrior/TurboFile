from flask import Blueprint, render_template, request, jsonify, Response
from flask_socketio import emit

import os
import re
import threading
import time
import uuid

from .extensions import socketio
from .core import *  # noqa: F403 - Keep legacy imports; refine to explicit later.

bp = Blueprint('turbofile', __name__)

_REMOTE_IMAGE_TOOL_CACHE = {}
_REMOTE_IMAGE_TOOL_CACHE_GUARD = threading.Lock()
_REMOTE_IMAGE_TOOL_CACHE_TTL_SEC = 300

_REMOTE_WIN_IMAGE_TOOL_CACHE = {}
_REMOTE_WIN_IMAGE_TOOL_CACHE_GUARD = threading.Lock()
_REMOTE_WIN_IMAGE_TOOL_CACHE_TTL_SEC = 300


def _guess_image_mime_from_path(path: str):
    try:
        p = (path or '').lower()
        if p.endswith(('.jpg', '.jpeg')):
            return 'image/jpeg'
        if p.endswith('.png'):
            return 'image/png'
        if p.endswith('.webp'):
            return 'image/webp'
        if p.endswith('.gif'):
            return 'image/gif'
        if p.endswith('.bmp'):
            return 'image/bmp'
        if p.endswith('.svg'):
            return 'image/svg+xml'
    except Exception:
        return None
    return None


def _try_parse_jpeg_dimensions(data: bytes):
    """Best-effort JPEG header parser for (width, height)."""
    try:
        if not isinstance(data, (bytes, bytearray)):
            return None
        buf = bytes(data)
        if len(buf) < 4 or not buf.startswith(b'\xFF\xD8'):
            return None

        i = 2
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3,
            0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB,
            0xCD, 0xCE, 0xCF,
        }
        while i + 1 < len(buf):
            # Find marker prefix 0xFF.
            if buf[i] != 0xFF:
                i += 1
                continue
            # Skip fill bytes 0xFF.
            while i < len(buf) and buf[i] == 0xFF:
                i += 1
            if i >= len(buf):
                break
            marker = buf[i]
            i += 1

            # Standalone markers.
            if marker in {0xD8, 0xD9}:
                continue
            # Start of Scan: image data begins; stop parsing.
            if marker == 0xDA:
                break

            if i + 2 > len(buf):
                break
            seg_len = (buf[i] << 8) + buf[i + 1]
            i += 2
            if seg_len < 2:
                break

            if marker in sof_markers:
                # Segment: [precision(1), height(2), width(2), ...]
                if i + 5 > len(buf):
                    break
                height = (buf[i + 1] << 8) + buf[i + 2]
                width = (buf[i + 3] << 8) + buf[i + 4]
                if width > 0 and height > 0:
                    return int(width), int(height)
                return None

            i += seg_len - 2
    except Exception:
        return None
    return None


def _get_remote_linux_imagemagick_tool(server_ip: str):
    """Return 'magick' or 'convert' when available on a remote Linux server, else None."""
    if not server_ip:
        return None
    now = time.time()
    with _REMOTE_IMAGE_TOOL_CACHE_GUARD:
        entry = _REMOTE_IMAGE_TOOL_CACHE.get(server_ip)
        if entry and (now - float(entry.get('ts', 0) or 0)) < _REMOTE_IMAGE_TOOL_CACHE_TTL_SEC:
            tool = entry.get('tool') or ''
            return tool if tool in {'magick', 'convert'} else None

    tool = None
    try:
        # Only meaningful for remote Linux.
        if is_local_server(server_ip) or is_windows_server(server_ip):
            tool = None
        else:
            cmd = (
                "sh -lc \""
                "if command -v magick >/dev/null 2>&1; then echo magick; "
                "elif command -v convert >/dev/null 2>&1; then echo convert; "
                "else echo; fi\""
            )
            out, _, _ = ssh_manager.execute_command(server_ip, cmd)
            candidate = (out or '').strip().splitlines()
            tool = (candidate[0].strip() if candidate else '') or None
            if tool not in {'magick', 'convert'}:
                tool = None
    except Exception:
        tool = None

    with _REMOTE_IMAGE_TOOL_CACHE_GUARD:
        _REMOTE_IMAGE_TOOL_CACHE[server_ip] = {'ts': now, 'tool': tool or ''}
    return tool


def _get_remote_windows_imagemagick_tool(server_ip: str):
    """Return 'magick' when available on a remote Windows server, else None."""
    if not server_ip:
        return None
    now = time.time()
    with _REMOTE_WIN_IMAGE_TOOL_CACHE_GUARD:
        entry = _REMOTE_WIN_IMAGE_TOOL_CACHE.get(server_ip)
        if entry and (now - float(entry.get('ts', 0) or 0)) < _REMOTE_WIN_IMAGE_TOOL_CACHE_TTL_SEC:
            tool = entry.get('tool') or ''
            return tool if tool == 'magick' else None

    tool = None
    try:
        if is_local_server(server_ip) or (not is_windows_server(server_ip)):
            tool = None
        else:
            # `where magick` is the most reliable on Windows (avoid `convert` name clash).
            cmd = 'cmd /c "where magick 2>nul"'
            out, _, _ = ssh_manager.execute_command(server_ip, cmd)
            if (out or '').strip():
                tool = 'magick'
    except Exception:
        tool = None

    with _REMOTE_WIN_IMAGE_TOOL_CACHE_GUARD:
        _REMOTE_WIN_IMAGE_TOOL_CACHE[server_ip] = {'ts': now, 'tool': tool or ''}
    return tool


def _exec_ssh_command_bytes(ssh, command: str, timeout_sec: float = 25.0):
    """Execute command via paramiko and return (stdout_bytes, stderr_bytes, exit_code)."""
    if not ssh or not command:
        return b'', b'', 1
    try:
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout_sec)
        out_b = stdout.read() if stdout else b''
        err_b = stderr.read() if stderr else b''
        try:
            exit_code = stdout.channel.recv_exit_status() if stdout and stdout.channel else 0
        except Exception:
            exit_code = 0 if out_b else 1
        return out_b or b'', err_b or b'', int(exit_code or 0)
    except Exception as e:
        return b'', str(e).encode('utf-8', errors='ignore'), 1


def _windows_file_stat_via_ssh(ssh, path: str, timeout_sec: float = 12.0):
    """Return (size_bytes:int, mtime_unix:int) for a Windows path via PowerShell."""
    if not ssh or not path:
        raise FileNotFoundError(path)
    last_err = None
    for p in _windows_pwsh_path_candidates(path):
        try:
            safe_p = _escape_pwsh_literal(p)  # noqa: F405
            ps = (
                "$ErrorActionPreference='Stop';"
                f"$p='{safe_p}';"
                "$i=Get-Item -LiteralPath $p;"
                "$len=[int64]$i.Length;"
                "$ts=[int64]([DateTimeOffset]$i.LastWriteTimeUtc).ToUnixTimeSeconds();"
                "Write-Output (\"{0} {1}\" -f $len,$ts);"
            )
            cmd = f"powershell -NoProfile -Command \"{ps}\""
            out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd, timeout_sec=timeout_sec)
            if exit_code != 0:
                raise RuntimeError((err_b or b'').decode('utf-8', errors='ignore') or 'pwsh stat failed')
            text = (out_b or b'').decode('utf-8', errors='ignore').strip()
            parts = text.split()
            if len(parts) >= 2:
                size = int(parts[0])
                mtime = int(parts[1])
                return size, mtime
            raise RuntimeError(f"bad stat output: {text}")
        except Exception as e:
            last_err = e
            continue
    raise last_err or FileNotFoundError(path)


def _windows_thumbnail_via_imagemagick(ssh, path: str, geom: str, fmt_key: str, quality_eff: int, timeout_sec: float = 25.0):
    """Return (data_bytes, mime) using `magick` on Windows via cmd.exe."""
    if not ssh or not path:
        return None, None
    out_spec = 'webp:-' if fmt_key == 'webp' else ('png:-' if fmt_key == 'png' else 'jpg:-')
    out_mime = 'image/webp' if fmt_key == 'webp' else ('image/png' if fmt_key == 'png' else 'image/jpeg')
    q = int(quality_eff) if 1 <= int(quality_eff) <= 95 else 82

    def _q(s: str) -> str:
        # cmd.exe quoting: double quotes; escape inner quotes by doubling.
        return '"' + str(s).replace('"', '""') + '"'

    src = _q(path)
    geom_q = _q(geom) if geom else ''
    q_arg = f"-quality {q}"
    thumb_arg = f"-thumbnail {geom_q}" if geom_q else ""
    cmd = f"cmd /c \"magick {src} {thumb_arg} -strip {q_arg} {out_spec}\""
    out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd, timeout_sec=timeout_sec)
    if exit_code == 0 and out_b:
        return out_b, out_mime

    if out_spec == 'webp:-':
        cmd2 = f"cmd /c \"magick {src} {thumb_arg} -strip {q_arg} jpg:-\""
        out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd2, timeout_sec=timeout_sec)
        if exit_code == 0 and out_b:
            return out_b, 'image/jpeg'
    return None, None


def _windows_thumbnail_via_powershell(ssh, path: str, target_w: int, target_h: int, quality_eff: int, timeout_sec: float = 25.0):
    """Return (jpeg_bytes, 'image/jpeg') using System.Drawing on Windows."""
    if not ssh or not path:
        return None, None
    last_err = None
    for p in _windows_pwsh_path_candidates(path):
        try:
            safe_p = _escape_pwsh_literal(p)  # noqa: F405
            q = int(quality_eff) if 1 <= int(quality_eff) <= 95 else 82
            tw = int(target_w or 0)
            th = int(target_h or 0)
            if tw <= 0 and th <= 0:
                tw, th = 0, 0

            ps = (
                "$ErrorActionPreference='Stop';"
                "Add-Type -AssemblyName System.Drawing;"
                f"$p='{safe_p}';"
                f"$tw={tw};$th={th};$q={q};"
                "$img=[System.Drawing.Image]::FromFile($p);"
                "$w=$img.Width; $h=$img.Height;"
                "if($tw -le 0 -and $th -le 0){$nw=$w;$nh=$h} "
                "elseif($tw -le 0){$nh=$th; $nw=[int]([math]::Round($w*($nh/[double]$h)))} "
                "elseif($th -le 0){$nw=$tw; $nh=[int]([math]::Round($h*($nw/[double]$w)))} "
                "else{$r=[math]::Min($tw/[double]$w,$th/[double]$h); if($r -gt 1){$r=1}; $nw=[int]([math]::Round($w*$r)); $nh=[int]([math]::Round($h*$r))};"
                "if($nw -lt 1){$nw=1}; if($nh -lt 1){$nh=1};"
                "$bmp=New-Object System.Drawing.Bitmap $nw,$nh;"
                "$g=[System.Drawing.Graphics]::FromImage($bmp);"
                "$g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic;"
                "$g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::HighQuality;"
                "$g.PixelOffsetMode=[System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality;"
                "$g.CompositingQuality=[System.Drawing.Drawing2D.CompositingQuality]::HighQuality;"
                "$g.DrawImage($img,0,0,$nw,$nh);"
                "$ms=New-Object System.IO.MemoryStream;"
                "$codec=[System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1;"
                "$ep=New-Object System.Drawing.Imaging.EncoderParameters 1;"
                "$ep.Param[0]=New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality),([int]$q);"
                "$bmp.Save($ms,$codec,$ep);"
                "$g.Dispose();$bmp.Dispose();$img.Dispose();"
                "$b=$ms.ToArray();$ms.Dispose();"
                "$o=[Console]::OpenStandardOutput();$o.Write($b,0,$b.Length);"
            )
            cmd = f"powershell -NoProfile -Command \"{ps}\""
            out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd, timeout_sec=timeout_sec)
            if exit_code == 0 and out_b:
                return out_b, 'image/jpeg'
            last_err = RuntimeError((err_b or b'').decode('utf-8', errors='ignore') or 'pwsh thumb failed')
        except Exception as e:
            last_err = e
            continue
    return None, None


def _windows_sftp_path_candidates(path: str):
    """
    Generate candidate SFTP paths for Windows OpenSSH.

    Windows file listings return paths like: C:\\dir\\file.jpg
    But SFTP implementations often expect: C:/dir/file.jpg or /C:/dir/file.jpg
    """
    raw = str(path or '').strip()
    if not raw:
        return []

    # Strip surrounding quotes if present.
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()

    candidates = []
    candidates.append(raw)

    slash = raw.replace('\\', '/')
    # Normalize repeated slashes for drive-letter paths (keep UNC-like leading '//' untouched).
    slash = re.sub(r'^([A-Za-z]:)/+', r'\1/', slash)
    slash = re.sub(r'^/([A-Za-z]:)/+', r'/\1/', slash)
    if slash != raw:
        candidates.append(slash)

    # If looks like a drive path, try adding leading '/' (common in SFTP on Windows).
    m = re.match(r'^([A-Za-z]):/(.*)$', slash)
    if m:
        drive = m.group(1)
        rest = m.group(2)
        candidates.append(f"/{drive}:/{rest}")

    # Also handle already-prefixed form: /C:/...
    m2 = re.match(r'^/([A-Za-z]):/(.*)$', slash)
    if m2:
        drive = m2.group(1)
        rest = m2.group(2)
        candidates.append(f"{drive}:/{rest}")

    # Dedup, keep order.
    seen = set()
    out = []
    for c in candidates:
        if not c:
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _sftp_stat_with_fallback(sftp, path: str, is_windows: bool):
    if not sftp:
        raise FileNotFoundError("SFTP unavailable")
    if not is_windows:
        return sftp.stat(path), path
    last_err = None
    for p in _windows_sftp_path_candidates(path):
        try:
            return sftp.stat(p), p
        except Exception as e:
            last_err = e
            continue
    raise last_err or FileNotFoundError(path)


def _sftp_open_with_fallback(sftp, path: str, mode: str, is_windows: bool):
    if not sftp:
        raise FileNotFoundError("SFTP unavailable")
    if not is_windows:
        return sftp.file(path, mode), path
    last_err = None
    for p in _windows_sftp_path_candidates(path):
        try:
            return sftp.file(p, mode), p
        except Exception as e:
            last_err = e
            continue
    raise last_err or FileNotFoundError(path)


def _windows_pwsh_path_candidates(path: str):
    raw = str(path or '').strip()
    if not raw:
        return []

    # Strip SFTP-style leading "/C:/".
    if raw.startswith('/') and re.match(r'^/[A-Za-z]:', raw):
        raw = raw[1:]

    candidates = []
    # Prefer normalized cmd path (backslashes), but keep the original around.
    try:
        candidates.append(normalize_windows_path_for_cmd(raw))  # noqa: F405
    except Exception:
        candidates.append(raw)
    candidates.append(raw)

    # Also try slash form converted to cmd form.
    try:
        candidates.append(normalize_windows_path_for_cmd(raw.replace('\\', '/')))  # noqa: F405
    except Exception:
        pass

    # Dedup.
    seen = set()
    out = []
    for c in candidates:
        c = str(c or '').strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _read_windows_file_bytes_via_ssh(ssh, path: str, timeout_sec: float = 30.0) -> bytes:
    """
    Read a Windows file via PowerShell over SSH and return raw bytes.
    This is a fallback when SFTP path mapping/chroot causes ENOENT.
    """
    last_err = None
    for p in _windows_pwsh_path_candidates(path):
        try:
            safe_p = _escape_pwsh_literal(p)  # noqa: F405
            ps = (
                "$ErrorActionPreference='Stop';"
                f"$p='{safe_p}';"
                "[byte[]]$b=[System.IO.File]::ReadAllBytes($p);"
                "$o=[Console]::OpenStandardOutput();"
                "$o.Write($b,0,$b.Length);"
            )
            cmd = f"powershell -NoProfile -Command \"{ps}\""
            out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd, timeout_sec=timeout_sec)
            if exit_code == 0 and out_b:
                return out_b
            last_err = RuntimeError((err_b or b'').decode('utf-8', errors='ignore') or 'pwsh read failed')
        except Exception as e:
            last_err = e
            continue
    raise last_err or FileNotFoundError(path)


@bp.route('/')
def index():
    clear_log_if_too_large()

    # Resolve the client IPv4 for front-end logging.
    client_ipv4 = extract_client_ipv4_from_request(request) or None

    # Determine whether the client is an admin (IP + config gate).
    is_admin_client = is_admin_client_ip(client_ipv4)
    with CLIENT_PATH_LOCK:
        remembered_paths = load_client_paths().get(client_ipv4, {}) if client_ipv4 else {}

    return render_template(
        'index.html',
        servers=SERVERS,
        client_ipv4=client_ipv4,
        is_admin_client=is_admin_client,
        remembered_paths=remembered_paths,
        transfer_bytes_enabled=TRANSFER_BYTES_CONFIG.get('enabled', True)
    )

@bp.route('/api/image/stream')
def api_image_stream():
    server_ip = request.args.get('server')
    path = request.args.get('path')
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    new_w = _safe_int(request.args.get('width', 0))
    new_h = _safe_int(request.args.get('height', 0))
    quality = _safe_int(request.args.get('quality', 0))
    interp = (request.args.get('interp') or '').strip().lower()
    img_format = (request.args.get('format') or '').strip().lower()
    fmt_requested = img_format in {'jpg', 'jpeg', 'png', 'webp'}
    if not server_ip or not path:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    resize_requested = bool(new_w or new_h)
    encode_requested = bool(quality or interp or img_format)
    transform_requested = resize_requested or encode_requested

    # Normalize params for cache key.
    quality_eff = quality if 1 <= quality <= 95 else 82
    interp_key = interp if interp in {'lanczos', 'lanczos4', 'sharp'} else 'area'
    fmt_key = img_format if img_format in {'jpg', 'jpeg', 'png', 'webp'} else ''
    if fmt_key == 'jpeg':
        fmt_key = 'jpg'
    if transform_requested and not fmt_key:
        fmt_key = 'jpg'

    is_local = is_local_server(server_ip)
    is_windows = is_windows_server(server_ip)
    ssh = None
    sftp = None
    engine = 'unknown'
    resolved_remote_path = path

    try:
        import cv2
        import numpy as np

        def _decode_image_bytes(img_bytes: bytes):
            try:
                decode_flag = cv2.IMREAD_UNCHANGED
                if resize_requested and (new_w > 0 or new_h > 0):
                    lower = (path or '').lower()
                    is_jpeg = lower.endswith(('.jpg', '.jpeg')) or (isinstance(img_bytes, (bytes, bytearray)) and bytes(img_bytes[:2]) == b'\xFF\xD8')
                    if is_jpeg:
                        desired_w = new_w if new_w > 0 else new_h
                        desired_h = new_h if new_h > 0 else new_w
                        if desired_w and not desired_h:
                            desired_h = desired_w
                        if desired_h and not desired_w:
                            desired_w = desired_h
                        dims = _try_parse_jpeg_dimensions(bytes(img_bytes[:131072]))
                        if dims and desired_w and desired_h:
                            orig_w, orig_h = dims
                            reduce_factor = 1
                            if orig_w // 8 >= desired_w and orig_h // 8 >= desired_h:
                                reduce_factor = 8
                            elif orig_w // 4 >= desired_w and orig_h // 4 >= desired_h:
                                reduce_factor = 4
                            elif orig_w // 2 >= desired_w and orig_h // 2 >= desired_h:
                                reduce_factor = 2

                            if reduce_factor == 8:
                                decode_flag = cv2.IMREAD_REDUCED_COLOR_8
                            elif reduce_factor == 4:
                                decode_flag = cv2.IMREAD_REDUCED_COLOR_4
                            elif reduce_factor == 2:
                                decode_flag = cv2.IMREAD_REDUCED_COLOR_2
                            else:
                                decode_flag = cv2.IMREAD_COLOR

                arr = np.frombuffer(img_bytes, dtype=np.uint8)
                return cv2.imdecode(arr, decode_flag)
            except Exception:
                return None

        def _transform_cv_image(img):
            if img is None:
                return None, None
            h, w = img.shape[:2]
            if w <= 0 or h <= 0:
                return None, None

            out_img = img
            did_resize = False
            if resize_requested and (new_w > 0 or new_h > 0):
                target_w, target_h = new_w, new_h
                if target_w > 0 and target_h > 0:
                    ratio = min(target_w / w, target_h / h)
                elif target_w > 0:
                    ratio = target_w / w
                else:
                    ratio = target_h / h

                # Only downscale; keep original size when ratio >= 1 unless a re-encode is requested.
                if ratio > 0 and ratio < 1:
                    target_w = max(1, int(w * ratio))
                    target_h = max(1, int(h * ratio))
                    interp_method = cv2.INTER_AREA if interp_key == 'area' else cv2.INTER_LANCZOS4
                    out_img = cv2.resize(img, (target_w, target_h), interpolation=interp_method)
                    did_resize = True

            if resize_requested and not did_resize and not encode_requested and not fmt_requested:
                return None, None

            fmt = fmt_key
            if fmt not in {'jpg', 'png', 'webp'}:
                fmt = 'jpg'

            q = quality_eff
            if fmt == 'webp':
                ok, enc = cv2.imencode('.webp', out_img, [int(cv2.IMWRITE_WEBP_QUALITY), q])
                if ok:
                    return enc.tobytes(), 'image/webp'
                # Fallback to jpeg.
                ok, enc = cv2.imencode('.jpg', out_img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
                if ok:
                    return enc.tobytes(), 'image/jpeg'
                return None, None
            if fmt == 'png':
                ok, enc = cv2.imencode('.png', out_img, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
                if ok:
                    return enc.tobytes(), 'image/png'
                return None, None

            ok, enc = cv2.imencode('.jpg', out_img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
            if ok:
                return enc.tobytes(), 'image/jpeg'
            return None, None

        # Local read.
        if is_local:
            if not transform_requested:
                with open(path, 'rb') as f:
                    data = f.read()
                resp = Response(data, mimetype=_guess_image_mime_from_path(path) or 'application/octet-stream')
                resp.headers['X-TurboFile-Image-Cache'] = 'BYPASS'
                resp.headers['X-TurboFile-Image-Engine'] = 'raw'
                return resp

            img_read_flag = cv2.IMREAD_UNCHANGED
            try:
                lower = (path or '').lower()
                if resize_requested and (new_w > 0 or new_h > 0) and lower.endswith(('.jpg', '.jpeg')):
                    desired_w = new_w if new_w > 0 else new_h
                    desired_h = new_h if new_h > 0 else new_w
                    if desired_w and not desired_h:
                        desired_h = desired_w
                    if desired_h and not desired_w:
                        desired_w = desired_h
                    if desired_w and desired_h:
                        head = b''
                        try:
                            with open(path, 'rb') as hf:
                                head = hf.read(131072)
                        except Exception:
                            head = b''
                        dims = _try_parse_jpeg_dimensions(head)
                        if dims:
                            orig_w, orig_h = dims
                            reduce_factor = 1
                            if orig_w // 8 >= desired_w and orig_h // 8 >= desired_h:
                                reduce_factor = 8
                            elif orig_w // 4 >= desired_w and orig_h // 4 >= desired_h:
                                reduce_factor = 4
                            elif orig_w // 2 >= desired_w and orig_h // 2 >= desired_h:
                                reduce_factor = 2
                            if reduce_factor == 8:
                                img_read_flag = cv2.IMREAD_REDUCED_COLOR_8
                            elif reduce_factor == 4:
                                img_read_flag = cv2.IMREAD_REDUCED_COLOR_4
                            elif reduce_factor == 2:
                                img_read_flag = cv2.IMREAD_REDUCED_COLOR_2
                            else:
                                img_read_flag = cv2.IMREAD_COLOR
            except Exception:
                img_read_flag = cv2.IMREAD_UNCHANGED

            img = cv2.imread(path, img_read_flag)
            if img is None:
                with open(path, 'rb') as f:
                    data = f.read()
                resp = Response(data, mimetype=_guess_image_mime_from_path(path) or 'application/octet-stream')
                resp.headers['X-TurboFile-Image-Cache'] = 'BYPASS'
                resp.headers['X-TurboFile-Image-Engine'] = 'raw'
                return resp

            data, mime = _transform_cv_image(img)
            did_transform = bool(data and mime)
            engine = 'opencv' if did_transform else 'raw'
            if not did_transform:
                # Fallback to raw bytes when encode is bypassed/failed.
                with open(path, 'rb') as f:
                    data = f.read()
                mime = _guess_image_mime_from_path(path)
        else:
            if not ssh:
                ssh = ssh_manager.get_connection(server_ip)
            if not ssh:
                return jsonify({'success': False, 'error': 'SSH连接失败'}), 500
            if not sftp:
                try:
                    sftp = ssh.open_sftp()
                except Exception:
                    sftp = None

            if not transform_requested:
                data_in = None
                try:
                    fobj, resolved_remote_path = _sftp_open_with_fallback(sftp, path, 'rb', is_windows)
                    with fobj as f:
                        data_in = f.read()
                        if not isinstance(data_in, (bytes, bytearray)):
                            data_in = bytes(data_in)
                except Exception:
                    if is_windows:
                        data_in = _read_windows_file_bytes_via_ssh(ssh, path)
                        engine = 'windows-pwsh'
                    else:
                        raise
                resp = Response(data_in, mimetype=_guess_image_mime_from_path(path) or 'application/octet-stream')
                resp.headers['X-TurboFile-Image-Cache'] = 'BYPASS'
                resp.headers['X-TurboFile-Image-Engine'] = engine if engine != 'unknown' else 'raw'
                return resp

            did_transform = False
            data = None
            mime = None

            # Windows: prefer SFTP read + local OpenCV transform first (fast path similar to older versions).
            # This avoids spawning a PowerShell/ImageMagick process per image when SFTP access works.
            if is_windows:
                try:
                    data_in = None
                    try:
                        fobj, resolved_remote_path = _sftp_open_with_fallback(sftp, path, 'rb', True)
                        with fobj as f:
                            data_in = f.read()
                            if not isinstance(data_in, (bytes, bytearray)):
                                data_in = bytes(data_in)
                    except Exception:
                        data_in = None

                    if data_in:
                        img = _decode_image_bytes(data_in)
                        data, mime = _transform_cv_image(img)
                        did_transform = bool(data and mime)
                        if did_transform:
                            engine = 'opencv'
                except Exception:
                    did_transform = False
                    data = None
                    mime = None

            # Windows fallback: remote thumbnail generation (handles cases where SFTP path mapping fails).
            if is_windows and (not did_transform):
                try:
                    target_w = new_w if new_w > 0 else new_h
                    target_h = new_h if new_h > 0 else new_w
                    if not target_w:
                        target_w = 0
                    if not target_h:
                        target_h = 0
                    if target_w and not target_h:
                        target_h = target_w
                    if target_h and not target_w:
                        target_w = target_h
                    geom = f"{int(target_w)}x{int(target_h)}>" if (target_w and target_h) else ""

                    win_tool = _get_remote_windows_imagemagick_tool(server_ip)
                    if win_tool == 'magick':
                        out_b, out_mime = _windows_thumbnail_via_imagemagick(ssh, path, geom, fmt_key, quality_eff, timeout_sec=25.0)
                        if out_b and out_mime:
                            data, mime = out_b, out_mime
                            did_transform = True
                            engine = 'windows-imagemagick'
                    if not did_transform:
                        # For grid thumbnails, use a faster scaling preset to reduce per-image latency.
                        is_thumb = max(int(target_w or 0), int(target_h or 0)) <= 900
                        out_b, out_mime = _windows_thumbnail_via_powershell(
                            ssh,
                            path,
                            int(target_w or 0),
                            int(target_h or 0),
                            quality_eff,
                            fast=bool(is_thumb),
                            timeout_sec=25.0
                        )
                        if out_b and out_mime:
                            data, mime = out_b, out_mime
                            did_transform = True
                            engine = 'windows-powershell-resize'
                except Exception:
                    did_transform = False
                    data = None
                    mime = None

            # Prefer remote thumbnail generation to avoid transferring the full original image over SSH.
            remote_tool = None
            try:
                if not is_windows_server(server_ip):
                    remote_tool = _get_remote_linux_imagemagick_tool(server_ip)
            except Exception:
                remote_tool = None

            if (not did_transform) and remote_tool:
                try:
                    target_w = new_w if new_w > 0 else new_h
                    target_h = new_h if new_h > 0 else new_w
                    if not target_w:
                        target_w = 0
                    if not target_h:
                        target_h = 0
                    if target_w and not target_h:
                        target_h = target_w
                    if target_h and not target_w:
                        target_w = target_h
                    if target_w and target_h:
                        geom = f"{target_w}x{target_h}>"
                    elif target_w:
                        geom = f"{target_w}x{target_w}>"
                    else:
                        geom = ""

                    out_mime = 'image/webp' if fmt_key == 'webp' else ('image/png' if fmt_key == 'png' else 'image/jpeg')
                    out_spec = 'webp:-' if fmt_key == 'webp' else ('png:-' if fmt_key == 'png' else 'jpg:-')

                    filter_opt = ""
                    if interp_key != 'area':
                        filter_opt = " -filter Lanczos"
                    quality_opt = f" -quality {int(quality_eff)}" if 1 <= quality_eff <= 95 else ""
                    thumb_opt = f" -thumbnail {shlex.quote(geom)}" if geom else ""

                    # IM7: magick <in> ... <out>; IM6: convert <in> ... <out>
                    im_cmd = remote_tool
                    src = shlex.quote(path)
                    cmd = f"{im_cmd} {src}{thumb_opt}{filter_opt} -strip{quality_opt} {out_spec}"
                    out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd, timeout_sec=25.0)

                    if exit_code != 0 or not out_b:
                        # Fallback when webp delegate is missing etc.
                        if out_spec == 'webp:-':
                            cmd2 = f"{im_cmd} {src}{thumb_opt}{filter_opt} -strip{quality_opt} jpg:-"
                            out_b, err_b, exit_code = _exec_ssh_command_bytes(ssh, cmd2, timeout_sec=25.0)
                            if exit_code == 0 and out_b:
                                out_mime = 'image/jpeg'
                        if exit_code != 0 or not out_b:
                            raise RuntimeError((err_b or b'').decode('utf-8', errors='ignore') or 'remote thumbnail failed')

                    data = out_b
                    mime = out_mime
                    did_transform = True
                    engine = 'remote-imagemagick'
                except Exception:
                    did_transform = False
                    data = None
                    mime = None

            if not did_transform:
                data_in = None
                try:
                    fobj, resolved_remote_path = _sftp_open_with_fallback(sftp, path, 'rb', is_windows)
                    with fobj as f:
                        data_in = f.read()
                        if not isinstance(data_in, (bytes, bytearray)):
                            data_in = bytes(data_in)
                except Exception:
                    if is_windows:
                        data_in = _read_windows_file_bytes_via_ssh(ssh, path)
                        engine = 'windows-pwsh'
                    else:
                        raise

                img = _decode_image_bytes(data_in)
                data, mime = _transform_cv_image(img)
                did_transform = bool(data and mime)
                if not did_transform:
                    data = data_in
                    mime = _guess_image_mime_from_path(path)
                    engine = 'raw'
                else:
                    engine = 'opencv' if engine != 'windows-pwsh' else 'windows-pwsh+opencv'

        resp = Response(data, mimetype=mime or 'application/octet-stream')
        resp.headers['X-TurboFile-Image-Cache'] = 'BYPASS'
        resp.headers['X-TurboFile-Image-Engine'] = engine
        return resp
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass


@bp.route('/api/file/read', methods=['GET'])
def api_file_read():
    server_ip = request.args.get('server')
    path = request.args.get('path')
    if not server_ip or not path:
        return jsonify({'success': False, 'error': '缺少参数'}), 400
    try:
        if is_local_server(server_ip):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return jsonify({'success': True, 'content': content})
        else:
            ssh = ssh_manager.get_connection(server_ip)
            if not ssh:
                return jsonify({'success': False, 'error': 'SSH连接失败'}), 500
            sftp = ssh.open_sftp()
            try:
                with sftp.file(path, 'r') as f:
                    data = f.read()
                    if isinstance(data, (bytes, bytearray)):
                        content = data.decode('utf-8', errors='ignore')
                    else:
                        content = str(data)
                return jsonify({'success': True, 'content': content})
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/file/save', methods=['POST'])
def api_file_save():
    data = request.get_json(silent=True) or {}
    server_ip = data.get('server')
    path = data.get('path')
    content = data.get('content', '')
    if not server_ip or not path:
        return jsonify({'success': False, 'error': '缺少参数'}), 400
    try:
        if is_local_server(server_ip):
            with open(path, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content if isinstance(content, str) else str(content))
            return jsonify({'success': True})
        else:
            ssh = ssh_manager.get_connection(server_ip)
            if not ssh:
                return jsonify({'success': False, 'error': 'SSH连接失败'}), 500
            sftp = ssh.open_sftp()
            try:
                with sftp.file(path, 'w') as f:
                    data_bytes = content.encode('utf-8') if isinstance(content, str) else bytes(content)
                    f.write(data_bytes)
                return jsonify({'success': True})
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/servers')
def get_servers():
    return jsonify(SERVERS)

@bp.route('/api/windows_drives/<server_ip>')
def get_windows_drives(server_ip):
    """Return the drive list for a Windows server."""
    if not is_windows_server(server_ip):
        return jsonify({
            'success': False,
            'error': '不是Windows服务器'
        })

    try:
        # Use PowerShell to list drives (faster and more stable than WMIC).
        ps_cmd = (
            "powershell -NoProfile -Command "
            "\"$items = Get-PSDrive -PSProvider FileSystem | "
            "Select-Object Name,Root,DisplayRoot; "
            "$items | ForEach-Object { "
            "$letter = $_.Name + ':'; "
            "$label = if ($_.DisplayRoot) { $_.DisplayRoot } else { '' }; "
            "[pscustomobject]@{ "
            "letter=$letter; "
            "name=if ($label) { $letter + ' (' + $label + ')' } else { $letter }; "
            "type=if ($_.DisplayRoot) { 'network' } else { 'local' } "
            "} } | ConvertTo-Json -Compress\""
        )
        output, error, _ = ssh_manager.execute_command(server_ip, ps_cmd)
        text = (output or '').strip()
        if not text:
            raise RuntimeError(error or '获取磁盘列表失败')

        drives = []
        try:
            parsed = json.loads(text)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                letter = str(item.get('letter', '') or '')
                if not letter:
                    continue
                name = str(item.get('name', letter) or letter)
                dtype = str(item.get('type', 'local') or 'local')
                root = normalize_windows_path_for_transfer(f"{letter}/")  # noqa: F405
                drives.append({
                    'letter': letter,
                    'name': name,
                    'type': 'network' if dtype == 'network' else 'local',
                    'path': root
                })
        except Exception:
            drives = []

        if not drives:
            raise RuntimeError('获取磁盘列表失败')

        # Add a WinSCP-like quick entry to Desktop (put it first).
        try:
            desktop_cmd = (
                "powershell -NoProfile -Command "
                "\"[Environment]::GetFolderPath('Desktop')\""
            )
            desktop_out, _, _ = ssh_manager.execute_command(server_ip, desktop_cmd)
            desktop_raw = str(desktop_out or '').strip()
            desktop_path = normalize_windows_path_for_transfer(desktop_raw) if desktop_raw else ''  # noqa: F405
            if desktop_path:
                drives.insert(0, {
                    'letter': '',
                    'name': '桌面',
                    'type': 'desktop',
                    'kind': 'desktop',
                    'path': desktop_path
                })
        except Exception:
            # Best-effort only; keep drive list usable even if desktop query fails.
            pass

        return jsonify({'success': True, 'drives': drives})
    except Exception as e:
        print(f"获取Windows磁盘列表异常: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })

@bp.route('/api/browse/<server_ip>')
def browse_directory(server_ip):
    # Use dynamic default path.
    default_path = get_default_path(server_ip)
    path = request.args.get('path', default_path)
    show_hidden = request.args.get('show_hidden', 'false').lower() == 'true'
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    try:
        limit = int(request.args.get('limit', BROWSE_PAGE_SIZE_DEFAULT))
    except ValueError:
        limit = BROWSE_PAGE_SIZE_DEFAULT

    # Normalize pagination parameters.
    offset = max(offset, 0)
    limit = max(BROWSE_PAGE_SIZE_MIN, min(limit, BROWSE_PAGE_SIZE_MAX))

    # Performance timing.
    start_time = time.time()

    try:
        # Force refresh clears cache first.
        cleared_count = 0
        if force_refresh:
            cleared_count = clear_cached_listing(server_ip, path)
            print(f"🔄 强制刷新: 清除了 {cleared_count} 个缓存项 - {server_ip}:{path}")

        # Fetch directory list (rebuild after cache clear).
        files = get_directory_listing_optimized(server_ip, path, show_hidden)
        total_count = len(files)

        # Pagination slice.
        start_index = min(offset, total_count)
        end_index = min(start_index + limit, total_count)
        paged_files = files[start_index:end_index]
        has_more = end_index < total_count

        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # Convert to milliseconds.

        return jsonify({
            'success': True,
            'path': path,
            'files': paged_files,
            'show_hidden': show_hidden,
            'force_refresh': force_refresh,
            'cache_cleared': cleared_count if force_refresh else 0,
            'response_time': round(response_time, 2),  # Include response timing.
            'file_count': total_count,
            'total_count': total_count,
            'offset': start_index,
            'limit': limit,
            'has_more': has_more,
            'next_offset': end_index if has_more else None,
            'loaded_count': end_index
        })
    except Exception as e:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000

        return jsonify({
            'success': False,
            'error': str(e),
            'response_time': round(response_time, 2)
        })

@bp.route('/api/quick_search/<server_ip>')
def quick_search(server_ip):
    path = request.args.get('path', '')
    keyword = request.args.get('keyword', '').strip()
    show_hidden = request.args.get('show_hidden', 'false').lower() == 'true'

    if not server_ip or server_ip not in SERVERS:
        return jsonify({'success': False, 'error': '无效的服务器'}), 400
    if not path or not keyword:
        return jsonify({'success': False, 'error': '缺少路径或关键字'}), 400

    try:
        files = get_cached_listing(server_ip, path, show_hidden)
        if files is not None:
            if not files:
                return jsonify({
                    'success': True,
                    'path': path,
                    'keyword': keyword,
                    'total_count': 0,
                    'match': None,
                    'index': None
                })

            keyword_lower = keyword.lower()
            first_match = None
            first_index = None
            for idx, item in enumerate(files):
                name = str(item.get('name', ''))
                if keyword_lower in name.lower():
                    first_match = {
                        'name': item.get('name', ''),
                        'path': item.get('path', ''),
                        'is_directory': bool(item.get('is_directory'))
                    }
                    first_index = idx
                    break

            return jsonify({
                'success': True,
                'path': path,
                'keyword': keyword,
                'total_count': len(files),
                'match': first_match,
                'index': first_index
            })

        keyword_lower = keyword.lower()
        first_match = None
        first_index = None

        if is_local_server(server_ip):
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        if not show_hidden and entry.name.startswith('.'):
                            continue
                        if keyword_lower in entry.name.lower():
                            first_match = {
                                'name': entry.name,
                                'path': os.path.join(path, entry.name),
                                'is_directory': entry.is_dir()
                            }
                            break
            except Exception:
                first_match = None
        elif is_windows_server(server_ip):
            win_path = normalize_windows_path_for_cmd(path)
            safe_path = _escape_pwsh_literal(win_path)
            safe_kw = _escape_pwsh_literal(keyword)
            force_flag = "$true" if show_hidden else "$false"
            ps_cmd = (
                "$ErrorActionPreference='SilentlyContinue';"
                f"$kw = '{safe_kw}';"
                "$pattern = [regex]::Escape($kw);"
                f"$items = Get-ChildItem -LiteralPath '{safe_path}' -Force:{force_flag};"
                "$hit = $items | Where-Object { $_.Name -match $pattern } | Select-Object -First 1;"
                "if ($null -ne $hit) {"
                "  $obj = [pscustomobject]@{name=$hit.Name; path=$hit.FullName; is_directory=$hit.PSIsContainer};"
                "  $obj | ConvertTo-Json -Compress"
                "}"
            )
            cmd = f"powershell -NoProfile -Command \"{ps_cmd}\""
            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, cmd)
            text = (stdout or '').strip()
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        first_match = {
                            'name': parsed.get('name', ''),
                            'path': parsed.get('path', ''),
                            'is_directory': bool(parsed.get('is_directory'))
                        }
                except Exception:
                    first_match = None
        else:
            def _escape_find_glob(text: str) -> str:
                return re.sub(r'([*?\\[\\]\\\\])', lambda m: '\\\\' + m.group(1), text)

            pattern = f"*{_escape_find_glob(keyword)}*"
            hidden_filter = "" if show_hidden else " -not -name '.*'"
            find_cmd = (
                f"find {shlex.quote(path)} -maxdepth 1 -mindepth 1"
                f"{hidden_filter} -iname {shlex.quote(pattern)} -print -quit"
            )
            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, find_cmd)
            found_line = (stdout or '').strip().splitlines()
            found_path = found_line[0].strip() if found_line else ''
            if found_path:
                name = os.path.basename(found_path.rstrip('/'))
                test_cmd = f"[ -d {shlex.quote(found_path)} ] && echo DIR || echo FILE"
                t_out, _, _ = ssh_manager.execute_command(server_ip, test_cmd)
                is_dir = (t_out or '').strip().upper() == 'DIR'
                first_match = {
                    'name': name,
                    'path': found_path,
                    'is_directory': is_dir
                }

        return jsonify({
            'success': True,
            'path': path,
            'keyword': keyword,
            'total_count': None,
            'match': first_match,
            'index': first_index
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@socketio.on('start_transfer')
def handle_start_transfer(data):
    transfer_id = f"transfer_{uuid.uuid4().hex}"

    # Transfer-level config (avoid cross-client overwrite).
    parallel_enabled = bool(data.get('parallel_transfer', True))

    # Get client IP.
    client_ip = _get_client_ip()

    source_server = data.get('source_server')
    target_server = data.get('target_server')
    target_path = data.get('target_path')
    source_files = data.get('source_files') or []
    mode = data.get('mode', 'copy')
    fast_ssh = data.get('fast_ssh', True)

    select_all = bool(data.get('select_all', False))
    source_dir = data.get('source_dir') or ''
    exclude_paths = data.get('exclude_paths') or []

    if not source_server or not target_server or not target_path:
        emit('transfer_cancelled', {'status': 'error', 'message': '参数不完整：请提供源/目标服务器与目标路径'})
        return
    if select_all and not source_dir:
        emit('transfer_cancelled', {'status': 'error', 'message': '全选传输缺少 source_dir'})
        return

    # Record transfer task.
    active_transfers[transfer_id] = {
        'source_server': source_server,
        'source_files': source_files,
        'target_server': target_server,
        'target_path': target_path,
        'mode': mode,
        'select_all': select_all,
        'source_dir': source_dir,
        'exclude_paths': exclude_paths,
        'parallel_enabled': parallel_enabled,
        'start_time': datetime.now(),
        'client_ip': client_ip
    }
    init_transfer_bytes(transfer_id)

    # Start immediate parallel transfer.
    start_instant_parallel_transfer(
        transfer_id,
        source_server,
        source_files,
        target_server,
        target_path,
        mode,
        fast_ssh,
        select_all=select_all,
        source_dir=source_dir,
        exclude_paths=exclude_paths,
        parallel_enabled=parallel_enabled
    )

    emit('transfer_started', {'transfer_id': transfer_id})

@socketio.on('cancel_transfer')
def handle_cancel_transfer(data):
    """Handle transfer cancel requests."""
    transfer_id = data.get('transfer_id')
    force_cancel = data.get('force', False)

    if not transfer_id:
        emit('transfer_cancelled', {'status': 'error', 'message': '无效的传输ID'})
        return

    if transfer_id not in active_transfers and not force_cancel:
        emit('transfer_cancelled', {'status': 'error', 'message': '传输任务不存在或已完成'})
        return

    if force_cancel:
        print(f"收到强制取消传输请求: {transfer_id}")
    else:
        print(f"收到取消传输请求: {transfer_id}")

    # Mark cancelled first to prevent new parallel tasks.
    if transfer_id in active_transfers:
        del active_transfers[transfer_id]

    # Force-stop related processes (including parallel children).
    for process_info in get_transfer_processes_snapshot(transfer_id):
        try:
            ptype = (process_info or {}).get('type')
            if ptype == 'subprocess':
                # Force-stop subprocess and process group.
                process = (process_info or {}).get('process')
                if not process:
                    continue
                import os
                import signal

                try:
                    if force_cancel:
                        # Force cancel: use SIGKILL immediately.
                        print(f"强制取消模式，立即杀死进程组: {transfer_id}")
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait()
                        print(f"已强制杀死subprocess进程组: {transfer_id}")
                    else:
                        # Normal cancel: attempt graceful termination first.
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        try:
                            process.wait(timeout=1)  # Wait at most 1 second.
                            print(f"已优雅终止subprocess进程组: {transfer_id}")
                        except subprocess.TimeoutExpired:
                            # If not terminated within 1s, kill forcefully.
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            process.wait()
                            print(f"已强制杀死subprocess进程组: {transfer_id}")
                except ProcessLookupError:
                    # Process already exited.
                    print(f"进程组已不存在: {transfer_id}")
                except Exception as e:
                    # If process-group kill fails, fall back to single process.
                    print(f"进程组终止失败，回退到单进程终止: {e}")
                    try:
                        if force_cancel:
                            process.kill()
                        else:
                            process.terminate()
                            try:
                                process.wait(timeout=1)
                            except subprocess.TimeoutExpired:
                                process.kill()
                        process.wait()
                    except Exception:
                        pass
            elif ptype == 'ssh':
                # Force-close SSH channel/connection.
                channel = (process_info or {}).get('channel')
                if not channel:
                    continue
                try:
                    # Send interrupt signal to remote command.
                    channel.send('\x03')  # Ctrl+C
                    channel.close()
                    print(f"已发送中断信号并关闭SSH通道: {transfer_id}")
                except Exception:
                    try:
                        channel.close()
                        print(f"已强制关闭SSH通道: {transfer_id}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"终止进程时出错: {e}")

    # Cleanup transfer records.
    if transfer_id in active_transfers:
        del active_transfers[transfer_id]
    with TRANSFER_PROCESS_LOCK:
        transfer_processes.pop(transfer_id, None)

    # Send cancellation confirmation.
    emit('transfer_cancelled', {
        'transfer_id': transfer_id,
        'status': 'success',
        'message': '传输已取消'
    })

    print(f"传输 {transfer_id} 已成功取消")

@bp.route('/api/delete', methods=['POST'])
def delete_files():
    """Delete files or folders."""
    start_ts = time.time()
    client_ip = _get_client_ip()
    try:
        data = request.get_json(silent=True) or {}
        server_ip = data.get('server')
        paths = data.get('paths', [])  # Support batch delete.
        delete_all = bool(data.get('delete_all', False))
        base_dir = data.get('base_dir') or ''
        exclude_paths = data.get('exclude_paths') or []
        show_hidden = bool(data.get('show_hidden', False))

        if not server_ip:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        if delete_all:
            if not base_dir:
                return jsonify({'success': False, 'error': '缺少必要参数'}), 400

            deleted_count = 0
            failed_items = []
            parent_dirs = {str(base_dir).replace('\\', '/')}

            if is_local:
                # Local delete: delete immediate children of base_dir.
                if is_windows:
                    # Best-effort: try PowerShell on local Windows (rare).
                    win_dir = normalize_windows_path_for_cmd(base_dir)
                    exclude_win = [normalize_windows_path_for_cmd(p) for p in exclude_paths if p]
                    ps_dir = _escape_pwsh_literal(win_dir)
                    ps_ex = ",".join([f"'{_escape_pwsh_literal(p)}'" for p in exclude_win])
                    force_flag = "-Force" if show_hidden else ""
                    ps_script = (
                        "$failed=@();"
                        f"$dir='{ps_dir}';"
                        f"$exclude=@({ps_ex});"
                        f"$items=Get-ChildItem -LiteralPath $dir {force_flag};"
                        "foreach($it in $items){"
                        "  $p=$it.FullName;"
                        "  if($exclude -contains $p){ continue }"
                        "  $err='';"
                        "  try{ Remove-Item -LiteralPath $p -Force -Recurse -ErrorAction Stop }catch{ $err=$_.Exception.Message }"
                        "  if(Test-Path -LiteralPath $p){"
                        "    if([string]::IsNullOrEmpty($err)){ $err='删除失败' }"
                        "    $failed += [pscustomobject]@{path=$p; error=$err}"
                        "  }"
                        "}"
                        "if($failed.Count -gt 0){ $failed | ConvertTo-Json -Compress; exit 1 }"
                        "exit 0"
                    )
                    cmd = f'powershell -NoProfile -Command "{ps_script}"'
                    try:
                        subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                    except subprocess.CalledProcessError as e:
                        failed_items.append({'path': base_dir, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else str(e)})
                else:
                    find_args = ['find', base_dir, '-mindepth', '1', '-maxdepth', '1']
                    if not show_hidden:
                        find_args += ['!', '-name', '.*']
                    for p in exclude_paths:
                        if p:
                            find_args += ['!', '-path', p]
                    find_args += ['-exec', 'rm', '-rf', '--', '{}', '+']
                    try:
                        subprocess.check_output(['sudo', '-n'] + find_args, stderr=subprocess.STDOUT)
                    except subprocess.CalledProcessError:
                        # Fallback without sudo.
                        subprocess.check_output(find_args, stderr=subprocess.STDOUT)
            else:
                # Remote delete: delete immediate children of base_dir.
                if is_windows:
                    win_dir = normalize_windows_path_for_cmd(base_dir)
                    exclude_win = [normalize_windows_path_for_cmd(p) for p in exclude_paths if p]
                    ps_dir = _escape_pwsh_literal(win_dir)
                    ps_ex = ",".join([f"'{_escape_pwsh_literal(p)}'" for p in exclude_win])
                    force_flag = "-Force" if show_hidden else ""
                    ps_script = (
                        "$failed=@();"
                        f"$dir='{ps_dir}';"
                        f"$exclude=@({ps_ex});"
                        f"$items=Get-ChildItem -LiteralPath $dir {force_flag};"
                        "foreach($it in $items){"
                        "  $p=$it.FullName;"
                        "  if($exclude -contains $p){ continue }"
                        "  $err='';"
                        "  try{ Remove-Item -LiteralPath $p -Force -Recurse -ErrorAction Stop }catch{ $err=$_.Exception.Message }"
                        "  if(Test-Path -LiteralPath $p){"
                        "    if([string]::IsNullOrEmpty($err)){ $err='删除失败' }"
                        "    $failed += [pscustomobject]@{path=$p; error=$err}"
                        "  }"
                        "}"
                        "if($failed.Count -gt 0){ $failed | ConvertTo-Json -Compress; exit 1 }"
                        "exit 0"
                    )
                    delete_cmd = f'powershell -NoProfile -Command "{ps_script}"'
                    stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, delete_cmd)
                    if exit_code != 0:
                        failed_items.append({'path': base_dir, 'error': stderr or stdout or '删除失败'})
                else:
                    parts = [
                        "find",
                        shlex.quote(base_dir),
                        "-mindepth 1",
                        "-maxdepth 1",
                    ]
                    if not show_hidden:
                        parts.append("! -name '.*'")
                    for p in exclude_paths:
                        if p:
                            parts.append(f"! -path {shlex.quote(p)}")
                    parts.append("-exec rm -rf -- {} +")
                    find_cmd = " ".join(parts)
                    rm_cmd_sudo = f"sudo -n {find_cmd}"
                    stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rm_cmd_sudo)
                    if exit_code != 0:
                        stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, find_cmd)
                    if exit_code != 0:
                        failed_items.append({'path': base_dir, 'error': stderr or stdout or '删除失败'})

            # Clear cache for affected dirs to refresh the browser view.
            cache_cleared = 0
            try:
                for d in parent_dirs:
                    cache_cleared += clear_cached_listing(server_ip, d)
            except Exception:
                pass

            if failed_items:
                return jsonify({
                    'success': False,
                    'deleted_all': False,
                    'deleted_count': deleted_count,
                    'failed_items': failed_items,
                    'cache_cleared': cache_cleared,
                    'error': '删除失败'
                })

            return jsonify({
                'success': True,
                'deleted_all': True,
                'deleted_count': deleted_count,
                'cache_cleared': cache_cleared,
                'message': '删除完成'
            })

        if not paths:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        deleted_count = 0
        failed_items = []
        parent_dirs = set()
        # Track parent directories for cache invalidation.
        for path in paths:
            try:
                if is_windows:
                    import ntpath
                    parent_dir = ntpath.dirname(path)
                else:
                    parent_dir = os.path.dirname(path)
                if parent_dir:
                    parent_dirs.add(parent_dir.replace('\\', '/'))
            except Exception:
                pass

        if is_local:
            # Local delete.
            for path in paths:
                try:
                    if os.path.isdir(path):
                        try:
                            shutil.rmtree(path)
                            deleted_count += 1
                        except PermissionError:
                            try:
                                subprocess.check_output(['sudo', '-n', 'rm', '-rf', path], stderr=subprocess.STDOUT)
                                deleted_count += 1
                            except subprocess.CalledProcessError as e:
                                failed_items.append({'path': path, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else str(e)})
                    else:
                        try:
                            os.remove(path)
                            deleted_count += 1
                        except PermissionError:
                            try:
                                subprocess.check_output(['sudo', '-n', 'rm', '-f', path], stderr=subprocess.STDOUT)
                                deleted_count += 1
                            except subprocess.CalledProcessError as e:
                                failed_items.append({'path': path, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else str(e)})
                except Exception as e:
                    failed_items.append({'path': path, 'error': str(e)})
        else:
            # Remote delete.
            if is_windows:
                # Windows: try batch delete first (reduce SSH roundtrips), fallback per-item.
                try:
                    path_pairs = []
                    win_to_orig = {}
                    for p in paths:
                        win_p = normalize_windows_path_for_cmd(p)
                        path_pairs.append((p, win_p))
                        win_to_orig[win_p.lower()] = p

                    ps_items = ",".join([f"'{_escape_pwsh_literal(win_p)}'" for _, win_p in path_pairs])
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
                    stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, delete_cmd)

                    if exit_code == 0:
                        deleted_count = len(paths)
                    else:
                        parsed = None
                        try:
                            text = (stdout or '').strip()
                            if text:
                                parsed = json.loads(text)
                        except Exception:
                            parsed = None

                        if parsed is None:
                            raise RuntimeError(stderr or '批量删除失败')

                        failed_list = parsed if isinstance(parsed, list) else [parsed]
                        failed_items = []
                        for item in failed_list:
                            if not isinstance(item, dict):
                                continue
                            win_p = str(item.get('path', '') or '')
                            orig_p = win_to_orig.get(win_p.lower(), win_p)
                            failed_items.append({'path': orig_p, 'error': str(item.get('error', '') or '删除失败')})
                        deleted_count = max(0, len(paths) - len(failed_items))
                except Exception:
                    # Fallback per-item delete for clearer failure reporting.
                    deleted_count = 0
                    failed_items = []
                    for path in paths:
                        try:
                            win_path = normalize_windows_path_for_cmd(path)
                            ps_path = win_path.replace("'", "''")
                            delete_cmd = (
                                "powershell -NoProfile -Command "
                                f"\"Remove-Item -LiteralPath '{ps_path}' -Force -Recurse -ErrorAction SilentlyContinue; "
                                f"if (Test-Path -LiteralPath '{ps_path}') {{ exit 1 }}\""
                            )

                            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, delete_cmd)
                            if exit_code == 0:
                                deleted_count += 1
                            else:
                                failed_items.append({'path': path, 'error': stderr or '删除失败'})
                        except Exception as e:
                            failed_items.append({'path': path, 'error': str(e)})
            else:
                # Linux/NAS: try batch rm -rf first (reduce SSH roundtrips), fallback per-item.
                batch_ok = False
                if len(paths) > 1:
                    quoted_paths = " ".join([shlex.quote(p) for p in paths if p])
                    if quoted_paths:
                        rm_cmd_sudo = f"sudo -n rm -rf -- {quoted_paths}"
                        stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rm_cmd_sudo)
                        if exit_code != 0:
                            rm_cmd = f"rm -rf -- {quoted_paths}"
                            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rm_cmd)
                        if exit_code == 0:
                            deleted_count = len(paths)
                            batch_ok = True

                if not batch_ok:
                    for path in paths:
                        try:
                            rm_cmd_sudo = f"sudo -n rm -rf {shlex.quote(path)}"
                            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rm_cmd_sudo)
                            if exit_code != 0:
                                rm_cmd = f"rm -rf {shlex.quote(path)}"
                                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rm_cmd)

                            if exit_code == 0:
                                deleted_count += 1
                            else:
                                failed_items.append({'path': path, 'error': stderr or '删除失败'})
                        except Exception as e:
                            failed_items.append({'path': path, 'error': str(e)})

        # Clear cache for affected parent dirs to refresh the browser view.
        cache_cleared = 0
        try:
            for d in parent_dirs:
                cache_cleared += clear_cached_listing(server_ip, d)
        except Exception:
            pass

        if failed_items:
            try:
                append_transfer_log_record(
                    source_ip=server_ip,
                    target_ip=server_ip,
                    source_path=paths[0] if paths else '',
                    target_full_path=paths[-1] if paths else '',
                    duration_sec=(time.time() - start_ts),
                    status='failure',
                    error=str(failed_items),
                    client_ip=client_ip,
                    mode='delete',
                    file_name=f'批量删除({len(paths)})',
                    action='delete'
                )
            except Exception:
                pass
            return jsonify({
                'success': False,
                'deleted_count': deleted_count,
                'failed_items': failed_items,
                'cache_cleared': cache_cleared,
                'error': f'部分删除失败: {deleted_count}/{len(paths)} 成功'
            })

        try:
            append_transfer_log_record(
                source_ip=server_ip,
                target_ip=server_ip,
                source_path=paths[0] if paths else '',
                target_full_path=paths[-1] if paths else '',
                duration_sec=(time.time() - start_ts),
                status='success',
                error="",
                client_ip=client_ip,
                mode='delete',
                file_name=f'批量删除({len(paths)})',
                action='delete'
            )
        except Exception:
            pass

        return jsonify({
            'success': True,
            'deleted_count': deleted_count,
            'cache_cleared': cache_cleared,
            'message': f'成功删除 {deleted_count} 项'
        })

    except Exception as e:
        try:
            append_transfer_log_record(
                source_ip=server_ip if 'server_ip' in locals() else '',
                target_ip=server_ip if 'server_ip' in locals() else '',
                source_path=paths[0] if 'paths' in locals() and paths else '',
                target_full_path=paths[-1] if 'paths' in locals() and paths else '',
                duration_sec=(time.time() - start_ts) if 'start_ts' in locals() else 0.0,
                status='failure',
                error=str(e),
                client_ip=client_ip,
                mode='delete',
                file_name=f'批量删除({len(paths)})' if 'paths' in locals() else '批量删除',
                action='delete'
            )
        except Exception:
            pass
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/create_folder', methods=['POST'])
def create_folder():
    """Create a directory."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        parent_path = data.get('parent_path')
        folder_name = data.get('folder_name')

        if not server_ip or not parent_path or not folder_name:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        # Build full path.
        if is_windows:
            import ntpath
            full_path = ntpath.join(parent_path, folder_name)
        else:
            full_path = os.path.join(parent_path, folder_name)

        if is_local:
            # Local create.
            os.makedirs(full_path, exist_ok=True)
        else:
            # Remote create.
            if is_windows:
                # Windows: use mkdir.
                mkdir_cmd = f'mkdir "{full_path}"'
            else:
                # Linux/NAS: use mkdir -p with shlex.quote().
                mkdir_cmd = f'mkdir -p {shlex.quote(full_path)}'

            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, mkdir_cmd)

            if exit_code != 0:
                return jsonify({'success': False, 'error': stderr or '创建文件夹失败'})

        return jsonify({
            'success': True,
            'message': f'成功创建文件夹: {folder_name}',
            'full_path': full_path
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/create_file', methods=['POST'])
def create_file():
    """Create an empty file."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        parent_path = data.get('parent_path')
        file_name = data.get('file_name')

        if not server_ip or not parent_path or not file_name:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        # Build full path.
        if is_windows:
            import ntpath
            full_path = ntpath.join(parent_path, file_name)
        else:
            full_path = os.path.join(parent_path, file_name)

        if is_local:
            if os.path.exists(full_path):
                return jsonify({'success': False, 'error': '文件已存在'})
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write('')
        else:
            if is_windows:
                ps_cmd = f"""
                if (Test-Path -LiteralPath '{full_path}') {{
                    Write-Output '__EXIST__'
                }} else {{
                    New-Item -ItemType File -Path '{full_path}' -Force | Out-Null
                }}
                """
                cmd = f"powershell -Command \"{ps_cmd}\""
            else:
                safe_path = shlex.quote(full_path)
                cmd = f'if [ -e {safe_path} ]; then echo "__EXIST__"; else touch {safe_path}; fi'

            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, cmd)
            if '__EXIST__' in (stdout or ''):
                return jsonify({'success': False, 'error': '文件已存在'})
            if exit_code != 0:
                return jsonify({'success': False, 'error': stderr or '创建文件失败'})

        return jsonify({
            'success': True,
            'message': '创建文件成功',
            'full_path': full_path
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/client_path/save', methods=['POST'])
def api_client_path_save():
    data = request.get_json(silent=True) or {}
    panel = data.get('panel')
    server = data.get('server')
    path = data.get('path')
    client_ip = extract_client_ipv4_from_request(request)
    try:
        remember_path(client_ip, panel, server, path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/compare_files', methods=['POST'])
def compare_files():
    """Compare two files and return a line-by-line diff (VSCode-style)."""
    try:
        data = request.get_json(silent=True) or {}
        server_a = data.get('server_a')
        server_b = data.get('server_b')
        path_a = data.get('path_a')
        path_b = data.get('path_b')

        if not all([server_a, server_b, path_a, path_b]):
            return jsonify({'success': False, 'error': '缺少必要参数'})

        def read_text(server, path):
            if is_local_server(server):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            ssh = ssh_manager.get_connection(server)
            if not ssh:
                raise RuntimeError('SSH连接失败')
            sftp = ssh.open_sftp()
            try:
                with sftp.file(path, 'r') as f:
                    data_bytes = f.read()
                if isinstance(data_bytes, (bytes, bytearray)):
                    return data_bytes.decode('utf-8', errors='ignore')
                return str(data_bytes)
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass

        left_text = read_text(server_a, path_a).splitlines()
        right_text = read_text(server_b, path_b).splitlines()

        sm = SequenceMatcher(None, left_text, right_text)
        diff_lines = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                for k in range(i2 - i1):
                    diff_lines.append({
                        'left_no': i1 + k + 1,
                        'right_no': j1 + k + 1,
                        'left': left_text[i1 + k],
                        'right': right_text[j1 + k],
                        'tag': 'equal'
                    })
            elif tag == 'replace':
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    left_line = left_text[i1 + k] if (i1 + k) < i2 else ''
                    right_line = right_text[j1 + k] if (j1 + k) < j2 else ''
                    diff_lines.append({
                        'left_no': i1 + k + 1 if (i1 + k) < i2 else None,
                        'right_no': j1 + k + 1 if (j1 + k) < j2 else None,
                        'left': left_line,
                        'right': right_line,
                        'tag': 'replace'
                    })
            elif tag == 'delete':
                for k in range(i2 - i1):
                    diff_lines.append({
                        'left_no': i1 + k + 1,
                        'right_no': None,
                        'left': left_text[i1 + k],
                        'right': '',
                        'tag': 'delete'
                    })
            elif tag == 'insert':
                for k in range(j2 - j1):
                    diff_lines.append({
                        'left_no': None,
                        'right_no': j1 + k + 1,
                        'left': '',
                        'right': right_text[j1 + k],
                        'tag': 'insert'
                    })

        return jsonify({
            'success': True,
            'lines': diff_lines
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/rename', methods=['POST'])
def rename_file():
    """Rename a file or folder."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        old_path = data.get('old_path')
        new_name = data.get('new_name')

        if not server_ip or not old_path or not new_name:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        # Build new path (same directory).
        if is_windows:
            import ntpath
            parent_dir = ntpath.dirname(old_path)
            new_path = ntpath.join(parent_dir, new_name)
        else:
            parent_dir = os.path.dirname(old_path)
            new_path = os.path.join(parent_dir, new_name)

        # If old and new paths match, return success.
        if new_path == old_path:
            return jsonify({'success': True, 'message': '名称未变化', 'new_path': new_path})

        # Check whether the new path already exists.
        if is_local:
            if os.path.exists(new_path):
                return jsonify({'success': False, 'error': f'目标名称已存在: {new_name}'})
        else:
            # Remote check.
            if is_windows:
                cmd_new_path = normalize_windows_path_for_cmd(new_path)
                safe_new_path = _escape_pwsh_literal(cmd_new_path)
                check_cmd = (
                    "powershell -NoProfile -Command "
                    f"\"if (Test-Path -LiteralPath '{safe_new_path}') {{ 'EXISTS' }} else {{ 'NOTEXISTS' }}\""
                )
            else:
                # Linux/NAS: use shlex.quote() for safe paths.
                check_cmd = f'test -e {shlex.quote(new_path)} && echo EXISTS || echo NOTEXISTS'

            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, check_cmd)
            flag_line = (stdout or '').strip().splitlines()
            flag = flag_line[0].strip().upper() if flag_line else ''
            if flag == 'EXISTS':
                return jsonify({'success': False, 'error': f'目标名称已存在: {new_name}'})

        # Execute rename.
        if is_local:
            # Local rename.
            try:
                os.rename(old_path, new_path)
            except PermissionError:
                try:
                    subprocess.check_output(['sudo', '-n', 'mv', old_path, new_path], stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as e:
                    return jsonify({'success': False, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else str(e)})
        else:
            # Remote rename.
            if is_windows:
                # Windows: use PowerShell Rename-Item to avoid cmd path parsing quirks.
                cmd_old_path = normalize_windows_path_for_cmd(old_path)
                safe_old_path = _escape_pwsh_literal(cmd_old_path)
                safe_new_name = _escape_pwsh_literal(new_name)
                rename_cmd = (
                    "powershell -NoProfile -Command "
                    f"\"Rename-Item -LiteralPath '{safe_old_path}' -NewName '{safe_new_name}' -Force\""
                )
            else:
                # Linux/NAS: use mv with shlex.quote().
                rename_cmd = f'mv {shlex.quote(old_path)} {shlex.quote(new_path)}'

            stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, rename_cmd)

            if exit_code != 0 and not is_windows:
                # Try sudo.
                sudo_cmd = f'sudo -n mv {shlex.quote(old_path)} {shlex.quote(new_path)}'
                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, sudo_cmd)

            if exit_code != 0:
                return jsonify({'success': False, 'error': stderr or '重命名失败'})

        return jsonify({
            'success': True,
            'message': f'成功重命名为: {new_name}',
            'new_path': new_path
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/run_file', methods=['POST'])
def run_file():
    """Run a local or remote .py/.sh file."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        file_path = data.get('path')
        client_sid = data.get('sid')  # Client socket ID for targeted output.

        if not server_ip or not file_path:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ['.py', '.sh']:
            return jsonify({'success': False, 'error': '仅支持运行 .py 或 .sh 文件'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        # Simple local path existence check.
        if is_local and not os.path.isfile(file_path):
            return jsonify({'success': False, 'error': '文件不存在或不可访问'})

        def quote_path(p):
            if is_windows:
                safe = p.replace('"', '\\"')
                return f'"{safe}"'
            return shlex.quote(p)

        work_dir = os.path.dirname(file_path) or '.'
        script_name = os.path.basename(file_path)

        if ext == '.py':
            # Linux/NAS prefer python3, fallback to python; Windows uses python.
            if is_windows:
                command = f'cd /d {quote_path(work_dir)} && python -u {quote_path(script_name)}'
            else:
                command = f'cd {quote_path(work_dir)} && (python3 -u {quote_path(script_name)} || python -u {quote_path(script_name)})'
        else:
            if is_windows:
                return jsonify({'success': False, 'error': 'Windows 不支持直接运行 .sh 脚本'})
            # Shell scripts run via bash.
            command = f'cd {quote_path(work_dir)} && bash {quote_path(script_name)}'

        run_id = f"run_{uuid.uuid4().hex}"
        socketio.start_background_task(stream_run_command, server_ip, command, file_path, run_id, is_windows, is_local, client_sid)

        return jsonify({
            'success': True,
            'run_id': run_id,
            'message': f'开始运行: {file_path}'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/api/compute_size', methods=['POST'])
def compute_size():
    """Compute file/folder size."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        file_path = data.get('path')
        if not server_ip or not file_path:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        size_bytes = None

        if is_local and not is_windows:
            # Local Linux: use du -sh for human-readable size.
            if not os.path.exists(file_path):
                return jsonify({'success': False, 'error': '路径不存在'})
            du_human_cmd = f"du -sh {shlex.quote(file_path)} 2>/dev/null"
            try:
                output = subprocess.check_output(du_human_cmd, shell=True, text=True, stderr=subprocess.STDOUT)
                human_size = (output or '').strip().split()[0]
            except subprocess.CalledProcessError as e:
                return jsonify({'success': False, 'error': e.output.strip() if e.output else '计算失败'})
            return jsonify({
                'success': True,
                'size_bytes': None,
                'human_size': human_size
            })
        else:
            if is_windows:
                safe_path = _escape_pwsh_literal(file_path)
                pwsh_template = (
                    "powershell -NoProfile -Command "
                    "\"if (Test-Path -LiteralPath '{path}' -PathType Container) {{ "
                    "(Get-ChildItem -LiteralPath '{path}' -Recurse -Force -ErrorAction SilentlyContinue "
                    "| Measure-Object -Property Length -Sum).Sum "
                    "}} elseif (Test-Path -LiteralPath '{path}' -PathType Leaf) {{ "
                    "(Get-Item -LiteralPath '{path}').Length "
                    "}} else {{ 'NOTFOUND' }}\""
                )
                pwsh_cmd = pwsh_template.format(path=safe_path)
                output, error, exit_code = ssh_manager.execute_command(server_ip, pwsh_cmd)
                if exit_code != 0 or not output:
                    return jsonify({'success': False, 'error': error or '计算失败'})
                text = (output or '').strip()
                if text.upper().startswith('NOTFOUND'):
                    return jsonify({'success': False, 'error': '路径不存在'})
                try:
                    size_bytes = int(text)
                except Exception:
                    return jsonify({'success': False, 'error': f'解析大小失败: {text}'})
            else:
                # Remote Linux/NAS: use du -sh for human-readable size.
                du_human_cmd = f"du -sh {shlex.quote(file_path)} 2>/dev/null | awk '{{print $1}}'"
                output, error, exit_code = ssh_manager.execute_command(server_ip, du_human_cmd)
                if exit_code != 0 or not output:
                    return jsonify({'success': False, 'error': error or '计算失败'})
                human_size = (output or '').strip().splitlines()[0]
                return jsonify({
                    'success': True,
                    'size_bytes': None,
                    'human_size': human_size
                })

        return jsonify({
            'success': True,
            'size_bytes': size_bytes,
            'human_size': _human_readable_size(size_bytes)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/compress', methods=['POST'])
def compress_path():
    """Compress files/folders into a zip."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        file_path = data.get('path')
        if not server_ip or not file_path:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        base_dir = os.path.dirname(file_path)
        name = os.path.basename(file_path.rstrip('/\\'))
        zip_name = f"{name}.zip"
        target_path = os.path.join(base_dir, zip_name) if is_windows else os.path.join(base_dir, zip_name)

        if is_local:
            if not os.path.exists(file_path):
                return jsonify({'success': False, 'error': '路径不存在'})
            if is_windows:
                safe_src = _escape_pwsh_literal(file_path)
                safe_dst = _escape_pwsh_literal(target_path)
                cmd = [
                    "powershell", "-NoProfile", "-Command",
                    f"Compress-Archive -LiteralPath '{safe_src}' -DestinationPath '{safe_dst}' -Force"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    return jsonify({'success': False, 'error': result.stderr or '压缩失败'})
            else:
                try:
                    subprocess.check_output(['zip', '-r', '-q', target_path, name], cwd=base_dir, stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as e:
                    return jsonify({'success': False, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else '压缩失败'})
        else:
            if is_windows:
                safe_src = _escape_pwsh_literal(file_path)
                safe_dst = _escape_pwsh_literal(target_path)
                ps_cmd = (
                    "powershell -NoProfile -Command "
                    f"\"Compress-Archive -LiteralPath '{safe_src}' -DestinationPath '{safe_dst}' -Force\""
                )
                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, ps_cmd)
                if exit_code != 0:
                    return jsonify({'success': False, 'error': stderr or '压缩失败'})
            else:
                zip_cmd = f"cd {shlex.quote(base_dir)} && zip -r -q {shlex.quote(zip_name)} {shlex.quote(name)}"
                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, zip_cmd)
                if exit_code != 0:
                    return jsonify({'success': False, 'error': stderr or '压缩失败'})

        return jsonify({'success': True, 'message': f'已生成: {zip_name}', 'zip_name': zip_name, 'zip_path': target_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/extract', methods=['POST'])
def extract_archive():
    """Extract zip/tar.gz/tgz/tar.bz2/tar.xz archives."""
    try:
        data = request.get_json()
        server_ip = data.get('server')
        file_path = data.get('path')
        if not server_ip or not file_path:
            return jsonify({'success': False, 'error': '缺少必要参数'})

        is_windows = is_windows_server(server_ip)
        is_local = is_local_server(server_ip)

        base_dir = os.path.dirname(file_path)
        name = os.path.basename(file_path)

        def is_tar_like(n):
            return n.endswith('.tar.gz') or n.endswith('.tgz') or n.endswith('.tar.bz2') or n.endswith('.tar.xz') or n.endswith('.tar')

        if is_local:
            if not os.path.exists(file_path):
                return jsonify({'success': False, 'error': '文件不存在'})
            if is_windows:
                safe_src = _escape_pwsh_literal(file_path)
                safe_dst = _escape_pwsh_literal(base_dir)
                cmd = [
                    "powershell", "-NoProfile", "-Command",
                    f"Expand-Archive -LiteralPath '{safe_src}' -DestinationPath '{safe_dst}' -Force"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    return jsonify({'success': False, 'error': result.stderr or '解压失败'})
            else:
                try:
                    if name.endswith('.zip'):
                        subprocess.check_output(['unzip', '-o', file_path, '-d', base_dir], stderr=subprocess.STDOUT)
                    elif is_tar_like(name):
                        subprocess.check_output(['tar', '-xf', file_path, '-C', base_dir], stderr=subprocess.STDOUT)
                    else:
                        return jsonify({'success': False, 'error': '不支持的压缩格式'})
                except subprocess.CalledProcessError as e:
                    return jsonify({'success': False, 'error': e.output.decode('utf-8', errors='replace') if hasattr(e, 'output') else '解压失败'})
        else:
            if is_windows:
                safe_src = _escape_pwsh_literal(file_path)
                safe_dst = _escape_pwsh_literal(base_dir)
                ps_cmd = (
                    "powershell -NoProfile -Command "
                    f"\"Expand-Archive -LiteralPath '{safe_src}' -DestinationPath '{safe_dst}' -Force\""
                )
                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, ps_cmd)
                if exit_code != 0:
                    return jsonify({'success': False, 'error': stderr or '解压失败'})
            else:
                if name.endswith('.zip'):
                    cmd = f"unzip -o {shlex.quote(file_path)} -d {shlex.quote(base_dir)}"
                elif is_tar_like(name):
                    cmd = f"tar -xf {shlex.quote(file_path)} -C {shlex.quote(base_dir)}"
                else:
                    return jsonify({'success': False, 'error': '不支持的压缩格式'})
                stdout, stderr, exit_code = ssh_manager.execute_command(server_ip, cmd)
                if exit_code != 0:
                    return jsonify({'success': False, 'error': stderr or '解压失败'})

        return jsonify({'success': True, 'message': '解压完成'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/run_file/cancel', methods=['POST'])
def cancel_run_file():
    """Cancel a running script."""
    try:
        data = request.get_json()
        run_id = data.get('run_id')
        if not run_id:
            return jsonify({'success': False, 'error': '缺少 run_id'})

        with RUN_TASKS_LOCK:
            task = RUN_TASKS.get(run_id)

        if not task:
            return jsonify({'success': False, 'error': '未找到对应的运行任务，可能已结束'})

        # Client sid for targeted emits.
        client_sid = task.get('sid')

        if task['type'] == 'local':
            proc = task.get('process')
            if proc and proc.poll() is None:
                try:
                    if os.name != 'nt':
                        os.killpg(proc.pid, signal.SIGTERM)
                        time.sleep(0.5)
                        if proc.poll() is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.terminate()
                except Exception as e:
                    emit_run_output(run_id, f"中断失败: {e}\n", is_error=True, final=False, sid=client_sid)
                    return jsonify({'success': False, 'error': f'中断失败: {e}'})
        elif task['type'] == 'remote':
            channel = task.get('channel')
            try:
                if channel:
                    channel.close()
            except Exception as e:
                emit_run_output(run_id, f"远程中断失败: {e}\n", is_error=True, final=False, sid=client_sid)
                return jsonify({'success': False, 'error': f'远程中断失败: {e}'})

        emit_run_output(run_id, "⏹️ 已请求中断\n", is_error=True, final=False, sid=client_sid)
        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/run_file/input', methods=['POST'])
def send_run_input():
    """Send input to a running script."""
    try:
        data = request.get_json()
        run_id = data.get('run_id')
        user_input = data.get('data', '')
        if not run_id:
            return jsonify({'success': False, 'error': '缺少 run_id'})

        with RUN_TASKS_LOCK:
            task = RUN_TASKS.get(run_id)
        if not task:
            return jsonify({'success': False, 'error': '未找到对应的运行任务，可能已结束'})

        payload = (user_input or '') + '\n'

        if task['type'] == 'local':
            fd = task.get('fd')
            proc = task.get('process')
            try:
                if fd is not None:
                    os.write(fd, payload.encode('utf-8', errors='ignore'))
                elif proc and proc.stdin:
                    proc.stdin.write(payload)
                    proc.stdin.flush()
            except Exception as e:
                return jsonify({'success': False, 'error': f'发送输入失败: {e}'})
        elif task['type'] == 'remote':
            channel = task.get('channel')
            try:
                if channel:
                    channel.send(payload)
            except Exception as e:
                return jsonify({'success': False, 'error': f'远程输入失败: {e}'})

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/active_transfers', methods=['GET'])
def get_active_transfers():
    """Return active transfer tasks."""
    try:
        transfers = []
        for transfer_id, transfer_info in active_transfers.items():
            # Extract client IP from transfer info when available.
            client_ip = transfer_info.get('client_ip', '未知')

            # Compute transfer duration.
            start_time = transfer_info.get('start_time')
            if start_time:
                elapsed = (datetime.now() - start_time).total_seconds()
                elapsed_str = f"{int(elapsed // 3600):02d}:{int((elapsed % 3600) // 60):02d}:{int(elapsed % 60):02d}"
            else:
                elapsed_str = "未知"

            transfers.append({
                'transfer_id': transfer_id,
                'client_ip': client_ip,
                'source_server': transfer_info.get('source_server', '未知'),
                'target_server': transfer_info.get('target_server', '未知'),
                'file_count': len(transfer_info.get('source_files', [])),
                'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S') if start_time else '未知',
                'elapsed_time': elapsed_str,
                'mode': transfer_info.get('mode', 'copy')
            })

        return jsonify({
            'success': True,
            'active_count': len(transfers),
            'transfers': transfers
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@socketio.on('connect')
def handle_connect():
    print('客户端已连接')

@socketio.on('disconnect')
def handle_disconnect():
    print('客户端已断开连接')
