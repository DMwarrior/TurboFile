#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web文件传输系统 - 主应用
基于现有的rsync传输脚本，提供Web界面控制
"""

from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
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

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# 服务器配置
SERVERS = {
    "192.168.9.62": {"name": "62服务器", "user": "th", "password": "th123456"},
    "192.168.9.61": {"name": "61服务器", "user": "th", "password": "th123456"},
    "192.168.9.60": {"name": "60服务器", "user": "th", "password": "taiho603656_0"},
    "192.168.9.57": {"name": "57服务器", "user": "thgd", "password": "123456"},
    "10.190.21.253": {"name": "NAS", "user": "Algorithm", "password": "Ai123456", "port": 8000},
    "10.190.129.29": {"name": "Windows服务器", "user": "warrior", "password": "Fkcay929", "os_type": "windows"}
}

# TurboFile运行的主机IP（当前运行在192.168.9.62上）
TURBOFILE_HOST_IP = "192.168.9.62"

# 获取当前主机的实际IP地址
def get_current_host_ip():
    """获取当前主机的IP地址"""
    try:
        import socket
        # 连接到一个远程地址来获取本机IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return TURBOFILE_HOST_IP  # 回退到配置的IP

def determine_transfer_mode(source_server, target_server):
    """
    智能判断传输模式，支持任意服务器作为源服务器

    返回值:
    - 'local_to_remote': 从TurboFile主机传输到远程服务器
    - 'remote_to_remote': 从远程服务器传输到另一个远程服务器
    - 'remote_to_local': 从远程服务器传输到TurboFile主机
    """
    current_host = get_current_host_ip()

    # 支持localhost别名
    local_aliases = ["localhost", "127.0.0.1", current_host, TURBOFILE_HOST_IP]

    is_source_local = source_server in local_aliases
    is_target_local = target_server in local_aliases

    if is_source_local and not is_target_local:
        return 'local_to_remote'
    elif not is_source_local and is_target_local:
        return 'remote_to_local'
    elif not is_source_local and not is_target_local:
        return 'remote_to_remote'
    else:
        # 本地到本地（同一台机器）
        return 'local_to_local'

def is_local_server(server_ip):
    """判断服务器是否为TurboFile运行的本地服务器"""
    current_host = get_current_host_ip()
    local_aliases = ["localhost", "127.0.0.1", current_host, TURBOFILE_HOST_IP]
    return server_ip in local_aliases

# 全局变量
ssh_connections = {}
active_transfers = {}
transfer_processes = {}  # 存储传输进程，用于取消操作

# 并行传输配置
PARALLEL_TRANSFER_CONFIG = {
    'max_workers': 4,  # 最大并行传输数
    'enable_parallel': True,  # 是否启用并行传输
    'instant_start': True,  # 立即开始传输，跳过所有预分析
    'enable_folder_parallel': False,  # 是否启用目录内部并行（实验性功能）
    'folder_parallel_threshold': 1000  # 启用目录内部并行的文件数阈值
}

# 🚀 传输性能优化配置
PERFORMANCE_CONFIG = {
    'speed_update_interval': 0.1,    # 速度更新间隔（秒）- 从0.01优化到0.1
    'progress_update_interval': 0.5, # 进度更新间隔（秒）
    'disable_progress_monitoring': True,  # 禁用进度监控以提升传输速度
    'reduce_websocket_traffic': True,     # 减少WebSocket通信量
    'optimize_rsync_params': True         # 优化rsync参数
}

# 模拟速度生成器
class SpeedSimulator:
    def __init__(self):
        self.transfer_speeds = {}  # 每个传输的速度状态
        self.lock = threading.Lock()

    def init_transfer_speed(self, transfer_id, min_speed: float = 110.0, max_speed: float = 114.0):
        """初始化传输速度；可按场景设置波动区间"""
        with self.lock:
            # 初始速度在[min_speed, max_speed]之间
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
        """获取模拟的传输速度 - 支持每个传输自定义速度区间"""
        with self.lock:
            if transfer_id not in self.transfer_speeds:
                self.init_transfer_speed(transfer_id)

            speed_data = self.transfer_speeds[transfer_id]
            current_time = time.time()

            # 区间参数
            min_s = speed_data.get('min_speed', 110.0)
            max_s = speed_data.get('max_speed', 114.0)
            width = max(0.1, max_s - min_s)
            edge = max(0.2, 0.25 * width)  # 边缘阈值

            # 🚀 性能优化：降低更新频率从10ms到100ms，减少CPU占用
            if current_time - speed_data['last_update'] >= 0.1:  # 100ms间隔
                speed_data['last_update'] = current_time
                speed_data['trend_duration'] += 1

                # 🚀 简化趋势变化逻辑
                if speed_data['trend_duration'] >= 20:  # 每2秒改变趋势
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
        """清理传输速度数据"""
        with self.lock:
            if transfer_id in self.transfer_speeds:
                del self.transfer_speeds[transfer_id]

# 全局速度模拟器
speed_simulator = SpeedSimulator()

# 传输时间跟踪器
class TransferTimeTracker:
    def __init__(self):
        self.transfer_start_times = {}
        self.lock = threading.Lock()

    def start_transfer(self, transfer_id):
        """开始传输计时"""
        with self.lock:
            self.transfer_start_times[transfer_id] = time.time()

    def get_elapsed_time(self, transfer_id):
        """获取已用时间"""
        with self.lock:
            if transfer_id in self.transfer_start_times:
                elapsed = time.time() - self.transfer_start_times[transfer_id]
                return self.format_time(elapsed)
            return "00:00:00"

    def end_transfer(self, transfer_id):
        """结束传输计时"""
        with self.lock:
            if transfer_id in self.transfer_start_times:
                elapsed = time.time() - self.transfer_start_times[transfer_id]
                del self.transfer_start_times[transfer_id]
                return self.format_time(elapsed)
            return "00:00:00"

    def format_time(self, seconds):
        """格式化时间显示"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

# 全局时间跟踪器
time_tracker = TransferTimeTracker()

# 全局进度管理器
class ProgressManager:
    def __init__(self):
        self.transfer_progress = {}
        self.progress_lock = threading.Lock()

    def init_transfer(self, transfer_id, total_files, total_bytes=0):
        """初始化传输进度"""
        with self.progress_lock:
            self.transfer_progress[transfer_id] = {
                'total_files': total_files,
                'completed_files': 0,
                'failed_files': 0,
                'total_bytes': total_bytes,
                'transferred_bytes': 0,
                'file_progress': {},  # 每个文件的进度
                'last_update_time': time.time()
            }

    def update_file_progress(self, transfer_id, file_name, percentage, bytes_transferred=0, speed=''):
        """更新单个文件的进度"""
        with self.progress_lock:
            if transfer_id not in self.transfer_progress:
                return

            progress = self.transfer_progress[transfer_id]
            progress['file_progress'][file_name] = {
                'percentage': percentage,
                'bytes_transferred': bytes_transferred,
                'speed': speed
            }

            # 计算总体进度（基于文件数量，不是字节数）
            completed_files = progress['completed_files']
            total_files = progress['total_files']

            # 计算当前正在传输的文件的贡献
            current_file_contribution = 0
            for fname, fprogress in progress['file_progress'].items():
                if fprogress['percentage'] < 100:
                    current_file_contribution += fprogress['percentage'] / 100

            overall_percentage = int(((completed_files + current_file_contribution) / total_files) * 100)
            overall_percentage = min(100, max(0, overall_percentage))

            # 限制更新频率（每500ms最多更新一次）
            current_time = time.time()
            if current_time - progress['last_update_time'] >= 0.5:
                progress['last_update_time'] = current_time

                # 生成模拟速度和实时时间
                simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
                elapsed_time = time_tracker.get_elapsed_time(transfer_id)

                # 进度更新已移除以提升性能
                pass

    def complete_file(self, transfer_id, file_name, success=True):
        """标记文件传输完成"""
        with self.progress_lock:
            if transfer_id not in self.transfer_progress:
                return

            progress = self.transfer_progress[transfer_id]
            if success:
                progress['completed_files'] += 1
            else:
                progress['failed_files'] += 1

            # 移除文件进度记录
            if file_name in progress['file_progress']:
                del progress['file_progress'][file_name]

            # 进度更新已移除以提升性能
            pass

    def cleanup_transfer(self, transfer_id):
        """清理传输进度记录"""
        with self.progress_lock:
            if transfer_id in self.transfer_progress:
                del self.transfer_progress[transfer_id]

progress_manager = ProgressManager()

class SSHManager:
    def __init__(self):
        self.connections = {}
        self.connection_pool_size = 3  # 每个服务器保持3个连接
        self.connection_pools = {}

    def get_connection(self, server_ip):
        """获取SSH连接，使用连接池优化"""
        # 检查连接池
        if server_ip not in self.connection_pools:
            self.connection_pools[server_ip] = []

        # 尝试从连接池获取可用连接
        pool = self.connection_pools[server_ip]
        for i, ssh in enumerate(pool):
            if ssh and ssh.get_transport() and ssh.get_transport().is_active():
                # 将使用的连接移到池末尾（LRU策略）
                pool.append(pool.pop(i))
                return ssh
            else:
                # 移除无效连接
                if ssh:
                    try:
                        ssh.close()
                    except:
                        pass
                pool.remove(ssh)

        # 创建新连接
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            server_config = SERVERS[server_ip]

            # 优化SSH连接参数
            connect_kwargs = {
                'hostname': server_ip,
                'username': server_config["user"],
                'port': server_config.get("port", 22),  # 支持自定义端口，默认22
                'timeout': 5,  # 减少超时时间
                'compress': False,  # 局域网不需要压缩
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

            # 针对NAS服务器（通常未配置密钥认证）直接使用密码连接，避免密钥尝试导致的超时卡顿
            if is_nas_server(server_ip):
                connect_kwargs['password'] = server_config.get("password")
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
                ssh.connect(**connect_kwargs)
                print(f"✅ 使用密码直连到NAS服务器 {server_ip}")
            else:
                # 其他服务器：先尝试密钥，失败再回退密码（保持原有逻辑）
                try:
                    ssh.connect(**connect_kwargs)
                    print(f"✅ 使用密钥连接到服务器 {server_ip}")
                except:
                    # 密钥认证失败，使用密码认证
                    connect_kwargs['password'] = server_config["password"]
                    ssh.connect(**connect_kwargs)
                    print(f"✅ 使用密码连接到服务器 {server_ip}")

            # 添加到连接池
            if len(pool) >= self.connection_pool_size:
                # 池满时移除最旧的连接
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
        """在远程服务器执行命令"""
        ssh = self.get_connection(server_ip)
        if not ssh:
            return None, f"无法连接到服务器 {server_ip}"

        # 检查是否为Windows服务器，使用不同的编码
        is_win = is_windows_server(server_ip)
        encoding = 'gbk' if is_win else 'utf-8'

        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode(encoding, errors='ignore')
            error = stderr.read().decode(encoding, errors='ignore')
            return output, error
        except Exception as e:
            # 连接可能已断开，尝试重新连接
            print(f"⚠️  SSH连接异常，尝试重新连接到 {server_ip}: {e}")
            if server_ip in self.connections:
                try:
                    self.connections[server_ip].close()
                except:
                    pass
                del self.connections[server_ip]

            # 重新获取连接并执行命令
            ssh = self.get_connection(server_ip)
            if ssh:
                try:
                    stdin, stdout, stderr = ssh.exec_command(command)
                    output = stdout.read().decode(encoding, errors='ignore')
                    error = stderr.read().decode(encoding, errors='ignore')
                    return output, error
                except Exception as retry_e:
                    return None, f"重连后仍然失败: {str(retry_e)}"

            return None, str(e)

ssh_manager = SSHManager()

def get_ssh_command_with_port(server_ip, fast_ssh=True):
    """构建支持自定义端口的SSH命令字符串"""
    server_config = SERVERS[server_ip]
    port = server_config.get("port", 22)

    ssh_cmd_parts = [
        "ssh",
        "-p", str(port),  # 支持自定义端口
        "-o", "StrictHostKeyChecking=no",
        "-o", "PasswordAuthentication=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "TCPKeepAlive=yes",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath=/tmp/ssh-%r@%h:{port}",  # 端口相关的控制路径
        "-o", "ControlPersist=300"
    ]

    if fast_ssh:
        ssh_cmd_parts.extend([
            "-o", "Compression=no",
            "-o", "Ciphers=aes128-ctr,aes192-ctr,aes256-ctr",
            "-o", "MACs=hmac-sha2-256,hmac-sha2-512"
        ])

    return " ".join(ssh_cmd_parts)

def is_nas_server(server_ip):
    """判断是否为NAS服务器"""
    is_nas = server_ip == "10.190.21.253"
    print(f"🔍 检查是否为NAS服务器: {server_ip} -> {is_nas}")
    return is_nas

def is_windows_server(server_ip):
    """判断是否为Windows服务器"""
    server_config = SERVERS.get(server_ip, {})
    is_windows = server_config.get("os_type") == "windows"
    print(f"🔍 检查是否为Windows服务器: {server_ip} -> {is_windows}")
    return is_windows

def convert_windows_path_to_cygwin(windows_path):
    """将Windows路径转换为Cygwin格式
    例如: C:\\Users\\warrior\\Documents -> /cygdrive/c/Users/warrior/Documents
    """
    import re
    # 处理盘符路径 (C:\path 或 C:/path)
    match = re.match(r'^([A-Za-z]):[/\\](.*)$', windows_path)
    if match:
        drive = match.group(1).lower()
        path = match.group(2).replace('\\', '/')
        return f"/cygdrive/{drive}/{path}"
    # 如果已经是Unix风格路径，直接返回
    return windows_path.replace('\\', '/')

def convert_cygwin_path_to_windows(cygwin_path):
    """将Cygwin路径转换为Windows格式
    例如: /cygdrive/c/Users/warrior/Documents -> C:/Users/warrior/Documents
    """
    import re
    match = re.match(r'^/cygdrive/([a-z])/(.*)$', cygwin_path)
    if match:
        drive = match.group(1).upper()
        path = match.group(2)
        return f"{drive}:/{path}"
    return cygwin_path

# 规范化 Windows 路径用于传输（处理例如 "D:"、"/D:"、反斜杠等情况）
def normalize_windows_path_for_transfer(p: str) -> str:
    try:
        if not p:
            return p
        s = p.replace('\\', '/')
        import re
        # 去掉前导斜杠形式的盘符，如 "/D:" -> "D:"
        if s.startswith('/') and re.match(r'^/[A-Za-z]:/?$', s):
            s = s[1:]
        # 盘符根保证为 "D:/" 形式
        if re.match(r'^[A-Za-z]:$', s):
            s = s + '/'
        return s
    except Exception:
        return p


def transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id):
    """使用tar+ssh传输文件到NAS服务器（rsync替代方案）"""
    try:
        # 发送开始传输日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🚀 开始tar+ssh传输 {file_name} 到NAS...'
        })

        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')
        target_port = target_config.get('port', 22)

        # 创建远程目录
        ssh_cmd = f"ssh -p {target_port} -o StrictHostKeyChecking=no"
        if target_password:
            mkdir_cmd = f"sshpass -p '{target_password}' {ssh_cmd} {target_user}@{target_server} 'mkdir -p {target_path}'"
        else:
            mkdir_cmd = f"{ssh_cmd} {target_user}@{target_server} 'mkdir -p {target_path}'"

        print(f"🔧 创建目录命令: {mkdir_cmd}")
        mkdir_result = subprocess.run(mkdir_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if mkdir_result.returncode != 0:
            print(f"❌ 创建目录失败: {mkdir_result.stderr}")
            raise Exception(f"创建目录失败: {mkdir_result.stderr}")
        else:
            print(f"✅ 目录创建成功: {target_path}")

        # 使用tar+ssh传输，添加静默选项避免输出干扰
        if is_directory:
            # 目录传输
            if target_password:
                tar_cmd = f"tar -cf - -C {os.path.dirname(source_path)} {os.path.basename(source_path)} 2>/dev/null | sshpass -p '{target_password}' {ssh_cmd} {target_user}@{target_server} 'cd {target_path} && tar -xf -'"
            else:
                tar_cmd = f"tar -cf - -C {os.path.dirname(source_path)} {os.path.basename(source_path)} 2>/dev/null | {ssh_cmd} {target_user}@{target_server} 'cd {target_path} && tar -xf -'"
        else:
            # 文件传输
            if target_password:
                tar_cmd = f"tar -cf - -C {os.path.dirname(source_path)} {os.path.basename(source_path)} 2>/dev/null | sshpass -p '{target_password}' {ssh_cmd} {target_user}@{target_server} 'cd {target_path} && tar -xf -'"
            else:
                tar_cmd = f"tar -cf - -C {os.path.dirname(source_path)} {os.path.basename(source_path)} 2>/dev/null | {ssh_cmd} {target_user}@{target_server} 'cd {target_path} && tar -xf -'"

        print(f"🚀 执行tar+ssh传输: {file_name}")
        print(f"🔧 源路径: {source_path}")
        print(f"🔧 目标路径: {target_path}")
        print(f"🔧 是否目录: {is_directory}")
        print(f"🔧 源目录: {os.path.dirname(source_path)}")
        print(f"🔧 源文件名: {os.path.basename(source_path)}")

        # 环境调试信息
        import pwd
        import grp
        current_user = pwd.getpwuid(os.getuid()).pw_name
        current_group = grp.getgrgid(os.getgid()).gr_name
        current_cwd = os.getcwd()

        print(f"🔧 当前用户: {current_user}")
        print(f"🔧 当前组: {current_group}")
        print(f"🔧 当前工作目录: {current_cwd}")
        print(f"🔧 /tmp目录是否存在: {os.path.exists('/tmp')}")
        print(f"🔧 /tmp目录权限: {oct(os.stat('/tmp').st_mode) if os.path.exists('/tmp') else 'N/A'}")

        # 检查源文件是否存在
        if os.path.exists(source_path):
            print(f"✅ 源文件存在: {source_path}")
            file_stat = os.stat(source_path)
            file_size = file_stat.st_size if os.path.isfile(source_path) else "目录"
            file_mode = oct(file_stat.st_mode)
            file_owner = pwd.getpwuid(file_stat.st_uid).pw_name
            file_group = grp.getgrgid(file_stat.st_gid).gr_name
            print(f"🔧 文件大小: {file_size}")
            print(f"🔧 文件权限: {file_mode}")
            print(f"🔧 文件所有者: {file_owner}:{file_group}")
        else:
            print(f"❌ 源文件不存在: {source_path}")
            # 尝试列出父目录内容
            parent_dir = os.path.dirname(source_path)
            if os.path.exists(parent_dir):
                print(f"🔧 父目录内容: {os.listdir(parent_dir)}")
            else:
                print(f"🔧 父目录也不存在: {parent_dir}")

            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ 源文件不存在: {source_path}'
            })
            return False

        print(f"🔧 执行命令: {tar_cmd}")

        # 发送详细调试日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🔧 调试: 执行命令 {tar_cmd}'
        })

        # 进度更新已移除以提升性能

        # 记录开始时间
        import time
        start_time = time.time()

        result = subprocess.run(tar_cmd, shell=True, capture_output=True, text=True, timeout=300)

        print(f"🔧 命令返回码: {result.returncode}")
        if result.stdout:
            print(f"🔧 标准输出: {result.stdout}")
        if result.stderr:
            print(f"🔧 错误输出: {result.stderr}")

        if result.returncode == 0:
            # 计算传输耗时
            end_time = time.time()
            duration = end_time - start_time

            # 格式化耗时显示
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

            print(f"✅ tar+ssh传输成功: {file_name}")

            # 发送成功日志（包含耗时）
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'✅ {file_name} tar+ssh传输完成，耗时: {time_str}'
            })

            return True
        else:
            print(f"❌ tar+ssh传输失败: {result.stderr}")

            # 发送错误日志
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ {file_name} tar+ssh传输失败: {result.stderr}'
            })

            return False

    except Exception as e:
        print(f"❌ tar+ssh传输异常: {e}")

        # 发送异常日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'❌ {file_name} tar+ssh传输异常: {str(e)}'
        })

        return False

def transfer_remote_to_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id):
    """从远程服务器使用tar+ssh传输文件到NAS服务器"""
    try:
        source_config = SERVERS[source_server]
        source_user = source_config['user']
        source_password = source_config.get('password')
        source_port = source_config.get('port', 22)

        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')
        target_port = target_config.get('port', 22)

        print(f"🚀 执行远程到NAS tar+ssh传输: {file_name}")
        print(f"🔧 源服务器: {source_server}:{source_port}")
        print(f"🔧 目标服务器: {target_server}:{target_port}")
        print(f"🔧 源路径: {source_path}")
        print(f"🔧 目标路径: {target_path}")

        # 发送开始传输日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🚀 开始tar+ssh传输 {file_name} 到NAS...'
        })

        # 创建NAS目标目录
        ssh_cmd = f"ssh -p {target_port} -o StrictHostKeyChecking=no"
        if target_password:
            mkdir_cmd = f"sshpass -p '{target_password}' {ssh_cmd} {target_user}@{target_server} 'mkdir -p {target_path}'"
        else:
            mkdir_cmd = f"{ssh_cmd} {target_user}@{target_server} 'mkdir -p {target_path}'"

        print(f"🔧 创建NAS目录命令: {mkdir_cmd}")
        mkdir_result = subprocess.run(mkdir_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if mkdir_result.returncode != 0:
            print(f"❌ 创建NAS目录失败: {mkdir_result.stderr}")
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ 创建NAS目录失败: {mkdir_result.stderr}'
            })
            return False
        else:
            print(f"✅ NAS目录创建成功: {target_path}")

        # 构建tar+ssh传输命令，添加密码认证支持
        source_ssh_cmd = f"ssh -p {source_port} -o StrictHostKeyChecking=no"
        target_ssh_cmd = f"ssh -p {target_port} -o StrictHostKeyChecking=no"

        if is_directory:
            # 目录传输
            source_tar_cmd = f"cd {os.path.dirname(source_path)} && tar -cf - {os.path.basename(source_path)} 2>/dev/null"
            target_extract_cmd = f"cd {target_path} && tar -xf -"
        else:
            # 文件传输
            source_tar_cmd = f"cd {os.path.dirname(source_path)} && tar -cf - {os.path.basename(source_path)} 2>/dev/null"
            target_extract_cmd = f"cd {target_path} && tar -xf -"

        # 根据密码配置构建完整命令
        if source_password and target_password:
            tar_cmd = f"sshpass -p '{source_password}' {source_ssh_cmd} {source_user}@{source_server} '{source_tar_cmd}' | sshpass -p '{target_password}' {target_ssh_cmd} {target_user}@{target_server} '{target_extract_cmd}'"
        elif source_password:
            tar_cmd = f"sshpass -p '{source_password}' {source_ssh_cmd} {source_user}@{source_server} '{source_tar_cmd}' | {target_ssh_cmd} {target_user}@{target_server} '{target_extract_cmd}'"
        elif target_password:
            tar_cmd = f"{source_ssh_cmd} {source_user}@{source_server} '{source_tar_cmd}' | sshpass -p '{target_password}' {target_ssh_cmd} {target_user}@{target_server} '{target_extract_cmd}'"
        else:
            tar_cmd = f"{source_ssh_cmd} {source_user}@{source_server} '{source_tar_cmd}' | {target_ssh_cmd} {target_user}@{target_server} '{target_extract_cmd}'"

        print(f"🔧 执行命令: {tar_cmd}")

        # 发送详细调试日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🔧 调试: 执行命令 {tar_cmd}'
        })

        # 进度更新已移除以提升性能

        # 记录开始时间
        import time
        start_time = time.time()

        result = subprocess.run(tar_cmd, shell=True, capture_output=True, text=True, timeout=300)

        print(f"🔧 命令返回码: {result.returncode}")
        if result.stdout:
            print(f"🔧 标准输出: {result.stdout}")
        if result.stderr:
            print(f"🔧 错误输出: {result.stderr}")

        if result.returncode == 0:
            # 计算传输耗时
            end_time = time.time()
            duration = end_time - start_time

            # 格式化耗时显示
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

            print(f"✅ 远程到NAS tar+ssh传输成功: {file_name}")

            # 发送成功日志（包含耗时）
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'✅ {file_name} 远程到NAS tar+ssh传输完成，耗时: {time_str}'
            })

            return True
        else:
            print(f"❌ 远程到NAS tar+ssh传输失败: {result.stderr}")

            # 发送错误日志
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ {file_name} 远程到NAS tar+ssh传输失败: {result.stderr}'
            })

            return False

    except Exception as e:
        print(f"❌ 远程到NAS tar+ssh传输异常: {str(e)}")
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'❌ {file_name} 远程到NAS传输异常: {str(e)}'
        })
        return False

def transfer_file_from_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id):
    """从NAS服务器使用tar+ssh传输文件"""
    try:
        source_config = SERVERS[source_server]
        source_user = source_config['user']
        source_password = source_config.get('password')
        source_port = source_config.get('port', 22)

        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')
        target_port = target_config.get('port', 22)

        # 构建SSH命令
        source_ssh = f"ssh -p {source_port} -o StrictHostKeyChecking=no"
        target_ssh = f"ssh -p {target_port} -o StrictHostKeyChecking=no"

        # 创建目标目录
        if is_local_server(target_server):
            # 目标是本地
            os.makedirs(target_path, exist_ok=True)
            if is_directory:
                os.makedirs(os.path.join(target_path, file_name), exist_ok=True)
        else:
            # 目标是远程服务器
            if is_directory:
                remote_target = f"{target_path}/{file_name}"
            else:
                remote_target = target_path

            if target_password:
                mkdir_cmd = f"sshpass -p '{target_password}' {target_ssh} {target_user}@{target_server} 'mkdir -p {remote_target}'"
            else:
                mkdir_cmd = f"{target_ssh} {target_user}@{target_server} 'mkdir -p {remote_target}'"
            subprocess.run(mkdir_cmd, shell=True, check=True)

        # 构建传输命令，添加静默选项避免输出干扰
        if is_directory:
            source_tar_cmd = f"cd {os.path.dirname(source_path)} && tar -cf - {os.path.basename(source_path)} 2>/dev/null"
        else:
            source_tar_cmd = f"cd {os.path.dirname(source_path)} && tar -cf - {os.path.basename(source_path)} 2>/dev/null"

        if is_local_server(target_server):
            # NAS到本地
            if is_directory:
                target_extract_cmd = f"cd {target_path} && tar -xf -"
            else:
                target_extract_cmd = f"cd {target_path} && tar -xf -"

            if source_password:
                full_cmd = f"sshpass -p '{source_password}' {source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | ({target_extract_cmd})"
            else:
                full_cmd = f"{source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | ({target_extract_cmd})"
        else:
            # NAS到远程服务器
            if is_directory:
                target_extract_cmd = f"cd {target_path} && tar -xf -"
            else:
                target_extract_cmd = f"cd {target_path} && tar -xf -"

            if source_password and target_password:
                full_cmd = f"sshpass -p '{source_password}' {source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | sshpass -p '{target_password}' {target_ssh} {target_user}@{target_server} '{target_extract_cmd}'"
            elif source_password:
                full_cmd = f"sshpass -p '{source_password}' {source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | {target_ssh} {target_user}@{target_server} '{target_extract_cmd}'"
            elif target_password:
                full_cmd = f"{source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | sshpass -p '{target_password}' {target_ssh} {target_user}@{target_server} '{target_extract_cmd}'"
            else:
                full_cmd = f"{source_ssh} {source_user}@{source_server} '{source_tar_cmd}' | {target_ssh} {target_user}@{target_server} '{target_extract_cmd}'"

        print(f"🚀 执行NAS tar+ssh传输: {file_name}")
        print(f"🔧 执行命令: {full_cmd}")

        # 发送开始传输日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🚀 开始从NAS tar+ssh传输 {file_name}...'
        })

        # 记录开始时间
        import time
        start_time = time.time()

        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=300)

        print(f"🔧 命令返回码: {result.returncode}")
        if result.stdout:
            print(f"🔧 标准输出: {result.stdout}")
        if result.stderr:
            print(f"🔧 错误输出: {result.stderr}")

        if result.returncode == 0:
            # 计算传输耗时
            end_time = time.time()
            duration = end_time - start_time

            # 格式化耗时显示
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

            print(f"✅ NAS tar+ssh传输成功: {file_name}")

            # 发送成功日志（包含耗时）
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'✅ {file_name} 从NAS tar+ssh传输完成，耗时: {time_str}'
            })

            return True
        else:
            print(f"❌ NAS tar+ssh传输失败: {result.stderr}")

            # 发送错误日志
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ {file_name} 从NAS tar+ssh传输失败: {result.stderr}'
            })

            return False

    except Exception as e:
        print(f"❌ NAS tar+ssh传输异常: {e}")

        # 发送异常日志
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'❌ {file_name} 从NAS传输异常: {str(e)}'
        })

        return False

def get_default_path(server_ip):
    """获取服务器的默认路径"""
    server_config = SERVERS.get(server_ip, {})

    # Windows服务器使用Windows路径 - 动态获取用户主目录
    if is_windows_server(server_ip):
        try:
            # 通过SSH执行命令获取Windows用户主目录
            output, error = ssh_manager.execute_command(server_ip, 'echo %USERPROFILE%')
            if output and not error:
                # 转换为正斜杠格式
                user_profile = output.strip().replace('\\', '/')
                print(f"🏠 Windows用户主目录: {user_profile}")
                return user_profile
        except Exception as e:
            print(f"⚠️  无法获取Windows用户主目录: {e}")

        # 如果获取失败，使用C盘根目录作为默认值
        return "C:/"

    # NAS服务器使用不同的默认路径
    if server_ip == "10.190.21.253":  # NAS服务器
        return "/var/services/homes/Algorithm"

    # 其他服务器根据用户名确定默认路径
    user = server_config.get("user", "th")
    if user == "thgd":
        return "/home/thgd"
    else:
        return "/home/th"

class ParallelTransferManager:
    def __init__(self):
        self.active_transfers = {}
        self.transfer_stats = {}

    def get_file_size(self, server_ip, file_path):
        """获取文件大小"""
        if is_local_server(server_ip):
            try:
                return os.path.getsize(file_path)
            except:
                return 0
        else:
            output, error = ssh_manager.execute_command(server_ip, f"stat -c%s '{file_path}' 2>/dev/null || echo 0")
            try:
                return int(output.strip())
            except:
                return 0

    def analyze_directory_structure(self, source_server, dir_path):
        """分析目录结构，返回所有子文件的信息"""
        all_files = []

        print(f"🔍 分析目录结构: {source_server}:{dir_path}")

        # 智能判断传输模式
        is_local_source = is_local_server(source_server)

        if is_local_source:
            # 本地目录分析
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
            # 远程目录分析
            print(f"🌐 远程目录分析: {source_server}:{dir_path}")
            try:
                cmd = f"find '{dir_path}' -type f -exec stat -c '%n %s' {{}} \\;"
                print(f"🔧 执行命令: {cmd}")
                output, error = ssh_manager.execute_command(source_server, cmd)

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
        """将文件分类为小文件和大文件，并分析目录结构"""
        small_files = []
        large_files = []
        directory_files = []  # 目录中的所有文件

        threshold_bytes = PARALLEL_TRANSFER_CONFIG['small_file_threshold_mb'] * 1024 * 1024

        print(f"🔍 开始文件分类，源服务器: {source_server}, 文件数量: {len(source_files)}")

        try:
            for i, file_info in enumerate(source_files):
                print(f"📁 处理文件 {i+1}/{len(source_files)}: {file_info['name']} (目录: {file_info['is_directory']})")

                if file_info['is_directory']:
                    # 分析目录结构
                    print(f"🔍 分析目录: {file_info['path']}")

                    # 发送分析进度通知
                    if transfer_id:
                        socketio.emit('transfer_log', {
                            'transfer_id': transfer_id,
                            'message': f'📁 正在分析目录 {file_info["name"]} 的结构...'
                        })

                    try:
                        # 检查是否启用快速模式
                        if PARALLEL_TRANSFER_CONFIG['fast_mode']:
                            # 快速模式：不进行详细分析，直接估算
                            if transfer_id:
                                socketio.emit('transfer_log', {
                                    'transfer_id': transfer_id,
                                    'message': f'⚡ 快速模式：跳过目录 {file_info["name"]} 的详细分析'
                                })

                            # 目录本身作为一个传输单元，不分析子文件
                            large_files.append({
                                **file_info,
                                'sub_files_count': 1,  # 估算为1个单元
                                'total_size': 0
                            })
                        else:
                            # 正常模式：详细分析
                            dir_files = self.analyze_directory_structure(source_server, file_info['path'])
                            directory_files.extend(dir_files)

                            print(f"✅ 目录 {file_info['name']} 包含 {len(dir_files)} 个文件")

                            # 检查是否文件数量过多，建议启用快速模式
                            if len(dir_files) > PARALLEL_TRANSFER_CONFIG['max_analysis_files']:
                                if transfer_id:
                                    socketio.emit('transfer_log', {
                                        'transfer_id': transfer_id,
                                        'message': f'⚠️ 目录 {file_info["name"]} 包含 {len(dir_files)} 个文件，建议启用快速模式以提高性能'
                                    })

                            # 发送分析完成通知
                            if transfer_id:
                                socketio.emit('transfer_log', {
                                    'transfer_id': transfer_id,
                                    'message': f'✅ 目录 {file_info["name"]} 分析完成，包含 {len(dir_files)} 个文件'
                                })

                            # 目录本身作为一个传输单元
                            large_files.append({
                                **file_info,
                                'sub_files_count': len(dir_files),
                                'total_size': sum(f['size'] for f in dir_files)
                            })
                    except Exception as e:
                        print(f"❌ 分析目录 {file_info['name']} 失败: {e}")

                        # 发送分析失败通知
                        if transfer_id:
                            socketio.emit('transfer_log', {
                                'transfer_id': transfer_id,
                                'message': f'⚠️ 目录 {file_info["name"]} 分析失败: {str(e)}'
                            })

                        # 即使分析失败，也要添加目录到传输列表
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
                        # 默认当作大文件处理
                        large_files.append(file_info)

            print(f"✅ 文件分类完成: {len(small_files)}个小文件, {len(large_files)}个大文件/目录, {len(directory_files)}个子文件")

        except Exception as e:
            print(f"❌ 文件分类过程中出错: {e}")
            # 发生错误时，将所有文件都当作大文件处理
            large_files = source_files.copy()
            small_files = []
            directory_files = []

        return small_files, large_files, directory_files

    def create_file_batches(self, files, batch_size=10):
        """将小文件分批处理"""
        batches = []
        for i in range(0, len(files), batch_size):
            batches.append(files[i:i + batch_size])
        return batches

parallel_manager = ParallelTransferManager()

# 文件浏览缓存 - 优化缓存时间，专注双击响应速度
file_cache = {}
cache_timeout = 120  # 缓存120秒，大幅提升重复访问速度
instant_cache_timeout = 300  # 立即访问缓存5分钟，优化双击体验

def get_cache_key(server_ip, path, show_hidden):
    """生成缓存键"""
    return f"{server_ip}:{path}:{show_hidden}"

def is_cache_valid(cache_entry):
    """检查缓存是否有效"""
    return time.time() - cache_entry['timestamp'] < cache_timeout

def get_cached_listing(server_ip, path, show_hidden):
    """获取缓存的文件列表"""
    cache_key = get_cache_key(server_ip, path, show_hidden)
    if cache_key in file_cache:
        cache_entry = file_cache[cache_key]
        if is_cache_valid(cache_entry):
            return cache_entry['data']
    return None

def set_cached_listing(server_ip, path, show_hidden, data):
    """设置文件列表缓存"""
    cache_key = get_cache_key(server_ip, path, show_hidden)
    file_cache[cache_key] = {
        'data': data,
        'timestamp': time.time()
    }

    # 清理过期缓存
    current_time = time.time()
    expired_keys = [k for k, v in file_cache.items()
                   if current_time - v['timestamp'] > cache_timeout]
    for key in expired_keys:
        del file_cache[key]

def clear_cached_listing(server_ip, path, show_hidden=None):
    """清除指定路径的缓存"""
    if show_hidden is None:
        # 清除该路径的所有缓存（包括显示/隐藏隐藏文件的两种状态）
        keys_to_remove = []
        for cache_key in file_cache.keys():
            if cache_key.startswith(f"{server_ip}:{path}:"):
                keys_to_remove.append(cache_key)

        for key in keys_to_remove:
            del file_cache[key]

        return len(keys_to_remove)
    else:
        # 清除特定状态的缓存
        cache_key = get_cache_key(server_ip, path, show_hidden)
        if cache_key in file_cache:
            del file_cache[cache_key]
            return 1
        return 0

def clear_all_cache():
    """清除所有缓存"""
    cache_count = len(file_cache)
    file_cache.clear()
    return cache_count

def is_winscp_hidden_file(name, permissions="", path="/"):
    """判断文件是否应该按照WinSCP规则隐藏

    Args:
        name: 文件名
        permissions: 文件权限字符串（ls -l格式）
        path: 当前目录路径

    Returns:
        bool: True表示应该隐藏，False表示应该显示
    """
    # 1. 隐藏以点号开头的文件（传统隐藏文件）
    if name.startswith('.'):
        return True

    # 2. 隐藏系统符号链接（通常指向系统目录）
    system_symlinks = {
        'bin', 'sbin', 'lib', 'lib32', 'lib64', 'libx32'
    }
    if name in system_symlinks:
        return True  # 无论是否为符号链接都隐藏

    # 3. 隐藏系统目录（在任何位置都隐藏这些系统目录）
    system_dirs = {
        'proc', 'sys', 'dev', 'run', 'boot', 'etc', 'var', 'tmp',
        'lost+found', 'cdrom', 'media', 'mnt', 'opt', 'srv', 'usr'
    }
    if name in system_dirs:
        return True

    # 4. 隐藏交换文件和系统文件
    system_files = {
        'swapfile', 'vmlinuz', 'initrd.img'
    }
    if name in system_files:
        return True

    # 5. 隐藏回收站目录
    if name.startswith('.Trash-'):
        return True

    # 6. 隐藏root目录（在非根目录位置时）
    if name == 'root' and path != '/':
        return True

    # 7. 隐藏home目录（当不在根目录时，通常表示这是挂载的系统）
    if name == 'home' and path != '/':
        return True

    # 8. 隐藏snap目录（Ubuntu snap包目录）
    if name == 'snap':
        return True

    # 9. 特殊情况：如果路径包含Work但显示了系统级目录，说明这是特殊挂载
    # 在用户工作目录中，只显示用户创建的内容
    if '/Work' in path or path.endswith('/Work'):
        # 在Work目录中，进一步过滤系统相关内容
        work_hidden_dirs = {
            'home', 'root', 'snap', 'boot', 'etc', 'var', 'usr', 'opt',
            'proc', 'sys', 'dev', 'run', 'tmp', 'media', 'mnt', 'srv',
            'lost+found', 'cdrom'
        }
        if name in work_hidden_dirs:
            return True

        # 在Work目录中隐藏所有系统相关的符号链接
        if name in {'bin', 'sbin', 'lib', 'lib32', 'lib64', 'libx32'}:
            return True

    return False

def get_directory_listing(server_ip, path=None, show_hidden=False):
    """获取远程目录列表

    Args:
        server_ip: 服务器IP地址
        path: 目录路径
        show_hidden: 是否显示隐藏文件（包括WinSCP规则的隐藏文件）
    """
    # 如果没有指定路径，使用默认路径
    if path is None:
        path = get_default_path(server_ip)

    # 首先检查缓存
    cached_result = get_cached_listing(server_ip, path, show_hidden)
    if cached_result is not None:
        return cached_result
    if is_local_server(server_ip):
        # 本地目录
        try:
            items = []
            for item in os.listdir(path):
                # 应用WinSCP过滤规则
                if not show_hidden:
                    # 获取文件权限信息用于判断符号链接
                    item_path = os.path.join(path, item)
                    permissions = ""
                    if os.path.islink(item_path):
                        permissions = "l"  # 标记为符号链接

                    # 使用WinSCP过滤规则
                    if is_winscp_hidden_file(item, permissions, path):
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
                    "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
            # 缓存结果
            set_cached_listing(server_ip, path, show_hidden, items)
            return items
        except Exception:
            return []
    else:
        # 远程目录
        # 判断是否为Windows服务器
        if is_windows_server(server_ip):
            # Windows服务器使用dir命令
            # 先规范化Windows路径，避免出现如"/C:"或"C:"（无斜杠）等异常
            import re
            normalized_path = path or ''
            # 去掉可能的前导斜杠：/C: -> C:
            if normalized_path.startswith('/') and re.match(r'^/[A-Za-z]:', normalized_path):
                normalized_path = normalized_path[1:]
            # 驱动器根保持为 C:/ 形式
            if re.match(r'^[A-Za-z]:$', normalized_path):
                normalized_path = normalized_path + '/'
            # 构造用于CMD的反斜杠路径
            win_path = normalized_path.replace('/', '\\')
            # 使用/a显示所有文件，/-c去除千位分隔符，统一解析
            command = f'dir "{win_path}" /a /-c'

            output, error = ssh_manager.execute_command(server_ip, command)

            if error and "找不到文件" not in error and "File Not Found" not in error:
                print(f"Windows dir命令错误: {error}")
                return []

            items = []
            lines = output.strip().split('\n')

            # 解析Windows dir命令输出
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 跳过标题行和统计行
                if 'Directory of' in line or '个文件' in line or '个目录' in line or 'File(s)' in line or 'Dir(s)' in line or 'bytes free' in line:
                    continue

                # 解析dir输出格式: 日期 时间 <DIR>或大小 文件名
                # 例如: 2024-01-15  10:30    <DIR>          Documents
                #      2024-01-15  10:30         1,234 file.txt
                import re
                match = re.match(r'(\d{4}[-/]\d{2}[-/]\d{2})\s+(\d{2}:\d{2})\s+(<DIR>|\d[\d,]*)\s+(.+)$', line)

                if match:
                    date_str = match.group(1)
                    time_str = match.group(2)
                    size_or_dir = match.group(3)
                    name = match.group(4).strip()

                    # 跳过当前目录和父目录
                    if name in ['.', '..']:
                        continue

                    # 判断是否为目录
                    is_directory = (size_or_dir == '<DIR>')

                    # 解析大小
                    if is_directory:
                        size = 0
                    else:
                        try:
                            size = int(size_or_dir.replace(',', ''))
                        except:
                            size = 0

                    # 应用WinSCP过滤规则（Windows不需要permissions参数）
                    if not show_hidden:
                        if is_winscp_hidden_file(name, "", path):
                            continue

                    # 构建完整路径（使用正斜杠以保持一致性）
                    base_path = normalized_path if 'normalized_path' in locals() and normalized_path else path
                    full_path = f"{base_path.rstrip('/')}/{name}".replace('\\', '/')

                    items.append({
                        "name": name,
                        "path": full_path,
                        "is_directory": is_directory,
                        "size": size,
                        "modified": f"{date_str} {time_str}"
                    })

            # 缓存结果
            set_cached_listing(server_ip, path, show_hidden, items)
            return items
        else:
            # Linux服务器使用ls命令
            # 使用ls -la命令以便正确识别符号链接和隐藏文件
            command = f"ls -la '{path}' | tail -n +2"  # 总是使用-a选项以获取完整信息

            output, error = ssh_manager.execute_command(server_ip, command)

            if error:
                return []

            items = []
            for line in output.strip().split('\n'):
                if not line:
                    continue

                parts = line.split()
                if len(parts) < 9:
                    continue

                permissions = parts[0]
                size = parts[4]
                date_parts = parts[5:8]
                name = ' '.join(parts[8:])

                # 跳过当前目录和父目录
                if name in ['.', '..']:
                    continue

                # 应用WinSCP过滤规则
                if not show_hidden:
                    if is_winscp_hidden_file(name, permissions, path):
                        continue

                is_directory = permissions.startswith('d')

                items.append({
                    "name": name,
                    "path": os.path.join(path, name),
                    "is_directory": is_directory,
                    "size": int(size) if size.isdigit() else 0,
                    "modified": ' '.join(date_parts)
                })

            # 缓存结果
            set_cached_listing(server_ip, path, show_hidden, items)
            return items

def get_directory_listing_optimized(server_ip, path=None, show_hidden=False):
    """优化的目录列表获取函数 - 专注于响应速度"""

    # 如果没有指定路径，使用默认路径
    if path is None:
        path = get_default_path(server_ip)

    # 首先检查缓存 - 优先使用缓存
    cached_result = get_cached_listing(server_ip, path, show_hidden)
    if cached_result is not None:
        return cached_result

    # 如果没有缓存，使用原始函数但添加性能优化
    if is_local_server(server_ip):
        # 本地目录 - 优化版本
        try:
            items = []
            # 使用os.scandir代替os.listdir，性能更好
            with os.scandir(path) as entries:
                for entry in entries:
                    # 应用WinSCP过滤规则
                    if not show_hidden:
                        # 快速权限检查
                        permissions = "l" if entry.is_symlink() else ""
                        if is_winscp_hidden_file(entry.name, permissions, path):
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
                            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                        })
                    except (OSError, PermissionError):
                        # 跳过无法访问的文件
                        continue

            # 缓存结果
            set_cached_listing(server_ip, path, show_hidden, items)
            return items
        except Exception:
            return []
    else:
        # 远程目录 - 使用原始实现但添加缓存优化
        return get_directory_listing(server_ip, path, show_hidden)

def start_speed_update_timer(transfer_id, source_server, target_server):
    """启动速度更新定时器 - 优化传输性能"""
    def speed_updater():
        last_time_update = time.time()
        last_speed_update = time.time()

        while transfer_id in active_transfers:
            try:
                # 🚀 性能优化：降低更新频率从10ms到100ms，减少90%的网络开销
                time.sleep(0.1)  # 100ms - 平衡视觉效果和性能

                if transfer_id not in active_transfers:
                    break

                current_time = time.time()

                # 🚀 性能优化：减少速度更新频率，降低CPU占用
                simulated_speed = None
                if current_time - last_speed_update >= 0.1:  # 每100ms更新速度
                    simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
                    last_speed_update = current_time

                # 时间每1秒更新一次
                elapsed_time = None
                if current_time - last_time_update >= 1.0:
                    elapsed_time = time_tracker.get_elapsed_time(transfer_id)
                    last_time_update = current_time

                # 🚀 性能优化：只在有数据更新时才发送WebSocket消息
                if simulated_speed is not None or elapsed_time is not None:
                    # 判断传输模式（缓存结果避免重复计算）
                    is_local_source = is_local_server(source_server)
                    is_local_target = is_local_server(target_server)

                    if is_local_source and not is_local_target:
                        transfer_mode = 'local_to_remote'
                    elif not is_local_source and is_local_target:
                        transfer_mode = 'remote_to_local'
                    else:
                        transfer_mode = 'remote_to_remote'

                    # 构建更新数据
                    update_data = {
                        'transfer_id': transfer_id,
                        'source_server': source_server,
                        'target_server': target_server,
                        'transfer_mode': transfer_mode
                    }

                    # 只包含有更新的数据
                    if simulated_speed is not None:
                        update_data['speed'] = simulated_speed
                    if elapsed_time is not None:
                        update_data['elapsed_time'] = elapsed_time

                    socketio.emit('speed_update', update_data)

            except Exception as e:
                print(f"速度更新器出错: {e}")
                break

    # 启动速度更新线程
    speed_thread = threading.Thread(target=speed_updater)
    speed_thread.daemon = True
    speed_thread.start()

def start_instant_parallel_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """启动即时并行传输任务 - 无预分析，立即开始"""
    def transfer_worker():
        try:
            total_files = len(source_files)

            # 启动传输计时器
            time_tracker.start_transfer(transfer_id)

            # 初始化速度模拟器（NAS传输使用38~40MB/s波动）
            if is_nas_server(source_server) or is_nas_server(target_server):
                speed_simulator.init_transfer_speed(transfer_id, 38.0, 40.0)
            else:
                speed_simulator.init_transfer_speed(transfer_id)

            # 启动速度更新定时器
            start_speed_update_timer(transfer_id, source_server, target_server)

            # 立即初始化进度管理（基于选择的文件/文件夹数量）
            progress_manager.init_transfer(transfer_id, total_files)

            # 🚀 性能优化：减少WebSocket通信，只发送关键信息
            if not PERFORMANCE_CONFIG.get('reduce_websocket_traffic', True):
                socketio.emit('transfer_log', {
                    'transfer_id': transfer_id,
                    'message': f'� 立即开始传输 {total_files} 个项目...'
                })

            # 检查是否启用并行传输
            if not PARALLEL_TRANSFER_CONFIG['enable_parallel'] or total_files == 1:
                # 单文件或禁用并行时使用顺序传输
                return start_sequential_transfer(transfer_id, source_server, source_files, target_server, target_path, mode, fast_ssh)

            # 创建线程池
            max_workers = min(PARALLEL_TRANSFER_CONFIG['max_workers'], total_files)

            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'⚡ 启动 {max_workers} 个并行传输线程...'
            })

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []

                # 直接提交所有文件/目录传输任务（无分类，无预分析）
                for file_info in source_files:
                    future = executor.submit(
                        transfer_single_file_instant,
                        transfer_id, source_server, file_info, target_server, target_path, mode, fast_ssh
                    )
                    futures.append(future)

                # 等待所有任务完成
                completed_count = 0
                failed_count = 0

                for future in concurrent.futures.as_completed(futures):
                    # 检查是否被取消
                    if transfer_id not in active_transfers:
                        # 取消所有未完成的任务
                        for f in futures:
                            f.cancel()
                        return

                    try:
                        result = future.result()
                        # 🔧 BUG修复：添加详细日志以诊断返回值问题
                        print(f"[DEBUG] 传输任务返回值: {result}, 类型: {type(result)}")

                        # 🔧 BUG修复：健壮的返回值判断逻辑
                        # 确保result是字典且包含success字段
                        is_success = False
                        if result is not None:
                            if isinstance(result, dict):
                                is_success = result.get('success', False)
                                print(f"[DEBUG] 字典返回值，success={is_success}")
                            else:
                                # 如果返回值不是字典，记录警告
                                print(f"[WARNING] 传输函数返回了非字典值: {result}, 类型: {type(result)}")
                                # 假设非False/None的值表示成功
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

                        # 进度更新已移除以提升性能 - 只在传输完成时发送状态

                    except Exception as e:
                        # 🔧 BUG修复：区分future.result()的异常和判断逻辑的异常
                        failed_count += 1
                        print(f"[ERROR] 传输任务异常: {str(e)}, 类型: {type(e).__name__}")
                        import traceback
                        print(f"[ERROR] 异常堆栈: {traceback.format_exc()}")
                        socketio.emit('transfer_log', {
                            'transfer_id': transfer_id,
                            'message': f'❌ 传输任务失败: {str(e)}'
                        })

            # 发送传输完成通知
            # 🔧 BUG修复：添加详细日志以诊断完成状态
            print(f"[DEBUG] 传输完成统计 - 成功: {completed_count}, 失败: {failed_count}, 总数: {total_files}")

            # 🔧 BUG修复：验证所有任务都被处理
            processed_count = completed_count + failed_count
            if processed_count != total_files:
                print(f"[WARNING] 任务处理数量不匹配！已处理: {processed_count}, 总数: {total_files}")
                # 将未处理的任务计入失败
                failed_count += (total_files - processed_count)
                print(f"[WARNING] 调整后失败数: {failed_count}")

            if failed_count > 0:
                # 部分成功情况下也要显示总耗时
                total_time = time_tracker.end_transfer(transfer_id)

                print(f"[DEBUG] 发送部分成功事件: transfer_id={transfer_id}, status=partial_success")
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'partial_success',
                    'message': f'传输完成，成功: {completed_count}, 失败: {failed_count}',
                    'total_time': total_time
                })
            else:
                # 结束传输计时
                total_time = time_tracker.end_transfer(transfer_id)

                # 🚀 性能监控：记录传输性能数据
                print(f"[性能监控] 传输ID: {transfer_id}")
                print(f"[性能监控] 文件数量: {completed_count}")
                print(f"[性能监控] 传输时间: {total_time}")
                # [安全] 已移除平均速度计算，避免格式转换错误（total_time 为 HH:MM:SS 格式）
                print(f"[性能监控] 速度更新间隔: {PERFORMANCE_CONFIG['speed_update_interval']}秒")

                print(f"[DEBUG] 发送成功事件: transfer_id={transfer_id}, status=success")
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'success',
                    'message': f'成功传输 {completed_count} 个文件/文件夹',
                    'total_time': total_time
                })

        except Exception as e:
            # 即使传输失败，也要计算并显示总耗时
            total_time = time_tracker.end_transfer(transfer_id)

            # 🔧 BUG修复：添加详细异常日志
            print(f"[DEBUG] 传输异常: {str(e)}")
            print(f"[DEBUG] 发送错误事件: transfer_id={transfer_id}, status=error")

            socketio.emit('transfer_complete', {
                'transfer_id': transfer_id,
                'status': 'error',
                'message': str(e),
                'total_time': total_time
            })
        finally:
            # 清理传输记录
            if transfer_id in active_transfers:
                del active_transfers[transfer_id]
            if transfer_id in transfer_processes:
                del transfer_processes[transfer_id]
            progress_manager.cleanup_transfer(transfer_id)
            speed_simulator.cleanup_transfer(transfer_id)

    # 启动传输线程
    thread = threading.Thread(target=transfer_worker)
    thread.daemon = True
    thread.start()

def transfer_single_file_instant(transfer_id, source_server, file_info, target_server, target_path, mode="copy", fast_ssh=True):
    """即时传输单个文件或目录 - 无预分析"""
    try:
        source_path = file_info['path']
        file_name = file_info['name']
        is_directory = file_info['is_directory']

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🚀 开始传输 {file_name}...'
        })

        # 检查是否被取消
        if transfer_id not in active_transfers:
            return {'success': False, 'message': '传输被取消'}

        # 智能判断传输模式，支持任意服务器作为源服务器
        transfer_mode = determine_transfer_mode(source_server, target_server)

        print(f"🔄 传输模式: {transfer_mode} ({source_server} → {target_server})")

        # 发送传输模式信息到前端
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🔄 传输模式: {transfer_mode} ({source_server} → {target_server})'
        })

        if transfer_mode == 'local_to_remote':
            # 从TurboFile主机传输到远程服务器
            print(f"📍 调用函数: transfer_file_via_local_rsync_instant")
            success = transfer_file_via_local_rsync_instant(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)
            if not success:
                raise Exception("本地到远程传输失败")
        elif transfer_mode == 'remote_to_local':
            # 从远程服务器传输到TurboFile主机
            print(f"📍 调用函数: transfer_file_via_remote_to_local_rsync_instant")
            success = transfer_file_via_remote_to_local_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)
            if not success:
                raise Exception("远程到本地传输失败")
        elif transfer_mode == 'remote_to_remote':
            # 从远程服务器传输到另一个远程服务器
            print(f"📍 调用函数: transfer_file_via_remote_rsync_instant")
            success = transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)
            if not success:
                raise Exception("远程到远程传输失败")
        else:
            # 本地到本地（同一台机器）
            print(f"📍 调用函数: transfer_file_via_local_to_local_instant")
            print(f"[DEBUG] 参数: source_path={source_path}, target_path={target_path}, file_name={file_name}, is_directory={is_directory}")
            success = transfer_file_via_local_to_local_instant(source_path, target_path, file_name, is_directory, transfer_id)
            print(f"[DEBUG] transfer_file_via_local_to_local_instant返回值: {success}, 类型: {type(success)}")
            if not success:
                raise Exception("本地到本地传输失败")
            print(f"[DEBUG] 本地到本地传输成功，准备返回字典")

        # 如果是移动模式，删除源文件
        if mode == "move" and not is_local_server(source_server):
            delete_cmd = f"rm -rf '{source_path}'"
            ssh_manager.execute_command(source_server, delete_cmd)

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'✅ {file_name} 传输完成'
        })

        return {'success': True, 'message': f'{file_name} 传输完成'}

    except Exception as e:
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'❌ {file_info["name"]} 传输失败: {str(e)}'
        })
        return {'success': False, 'message': str(e)}

def transfer_file_via_local_rsync_instant(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """即时本地rsync传输 - 支持目录内部并行和NAS服务器"""

    # 如果目标是NAS服务器，使用tar+ssh方案
    if is_nas_server(target_server):
        return transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id)

    # 检查是否启用目录内部并行
    enable_folder_parallel = PARALLEL_TRANSFER_CONFIG.get('enable_folder_parallel', False)
    folder_parallel_threshold = PARALLEL_TRANSFER_CONFIG.get('folder_parallel_threshold', 1000)  # 文件数阈值

    if is_directory and enable_folder_parallel:
        # 快速检查目录文件数量
        try:
            file_count = sum(len(files) for _, _, files in os.walk(source_path))
            if file_count > folder_parallel_threshold:
                # 使用目录内部并行传输
                return transfer_directory_parallel(source_path, target_server, target_path, file_name, transfer_id, fast_ssh)
        except:
            pass  # 如果检查失败，回退到单rsync

    # 使用单rsync传输（原始实现）
    return transfer_single_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)

def transfer_single_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """单rsync传输实现"""
    # 如果目标是NAS服务器，使用tar+ssh方案
    if is_nas_server(target_server):
        return transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id)

    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    # 检查目标是否为Windows服务器
    target_is_windows = is_windows_server(target_server)

    # 🚀 极速优化：精简rsync参数，移除所有性能开销
    rsync_opts = [
        '-a',                    # 归档模式（必需）
        '--inplace',             # 就地更新，减少磁盘I/O
        '--whole-file',          # 整文件传输（局域网最快）
        '--no-compress',         # 禁用压缩（局域网环境）
        '--numeric-ids',         # 数字ID，避免用户名解析
        '--timeout=600',         # 增加超时时间，避免传输中断
    ]

    # 🚀 性能优化：移除可能影响速度的选项
    # 移除 --partial（断点续传）- 可能影响性能
    # 移除 --progress - 避免进度监控开销
    # 强制禁用压缩 - 局域网环境下压缩反而降低速度

    # 处理目标路径（如果是Windows，转换为Cygwin格式），并统一加上SSH参数
    rsync_target_path = target_path
    if target_is_windows:
        normalized_target = normalize_windows_path_for_transfer(target_path)
        rsync_target_path = convert_windows_path_to_cygwin(normalized_target)
        print(f"🔄 Windows目标路径转换: {target_path} -> {rsync_target_path}")

    # 构建完整命令（显式指定SSH，避免首次连接/known_hosts等交互问题）
    ssh_cmd = get_ssh_command_with_port(target_server, fast_ssh)
    if is_directory:
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, f'{source_path}/', f'{target_user}@{target_server}:{rsync_target_path}/{file_name}/']
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, f'{source_path}/', f'{target_user}@{target_server}:{rsync_target_path}/{file_name}/']
    else:
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + ['-e', ssh_cmd, source_path, f'{target_user}@{target_server}:{rsync_target_path}/']
        else:
            cmd = ['rsync'] + rsync_opts + ['-e', ssh_cmd, source_path, f'{target_user}@{target_server}:{rsync_target_path}/']

    # 执行rsync命令
    import subprocess
    import os
    import signal

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
        preexec_fn=os.setsid  # 创建新的进程组
    )

    # 存储进程用于取消操作
    transfer_processes[transfer_id] = {
        'type': 'subprocess',
        'process': process
    }

    # 等待传输完成（无进度读取，提升性能）
    try:
        return_code = process.wait()
        if return_code != 0:
            raise Exception(f"rsync传输失败，退出码: {return_code}")
    except KeyboardInterrupt:
        # 处理取消操作
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=2)
        except:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
            except:
                pass
        raise Exception("传输被用户取消")

    # 传输成功
    return True

def transfer_directory_parallel(source_path, target_server, target_path, file_name, transfer_id, fast_ssh):
    """目录内部并行传输实现"""
    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    socketio.emit('transfer_log', {
        'transfer_id': transfer_id,
        'message': f'📁 启用目录内部并行传输: {file_name}'
    })

    # 分析目录结构，制定并行策略
    parallel_tasks = []

    try:
        # 获取直接子目录和文件
        items = os.listdir(source_path)
        subdirs = []
        files = []

        for item in items:
            item_path = os.path.join(source_path, item)
            if os.path.isdir(item_path):
                subdirs.append(item)
            else:
                files.append(item)

        # 策略1: 每个子目录一个任务
        for subdir in subdirs:
            parallel_tasks.append({
                'type': 'subdir',
                'source': os.path.join(source_path, subdir),
                'target_subpath': f'{file_name}/{subdir}',
                'name': subdir
            })

        # 策略2: 根目录文件分组
        if files:
            # 将文件分成最多3组
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

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'📊 并行任务: {len(subdirs)}个子目录 + {len(files)}个文件 → {len(parallel_tasks)}个并行任务'
        })

        # 执行并行传输
        max_workers = min(4, len(parallel_tasks))

        def execute_parallel_task(task):
            """执行单个并行任务"""
            # 🚀 极速优化：统一使用最优rsync参数
            rsync_opts = ['-a', '--inplace', '--whole-file', '--no-compress', '--numeric-ids', '--timeout=600']

            if task['type'] == 'subdir':
                # 传输子目录
                if target_password:
                    cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + [
                        f"{task['source']}/", f"{target_user}@{target_server}:{target_path}/{task['target_subpath']}/"
                    ]
                else:
                    cmd = ['rsync'] + rsync_opts + [
                        f"{task['source']}/", f"{target_user}@{target_server}:{target_path}/{task['target_subpath']}/"
                    ]
            else:
                # 传输文件组
                file_paths = [os.path.join(task['source_dir'], f) for f in task['files']]
                if target_password:
                    cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + file_paths + [
                        f"{target_user}@{target_server}:{target_path}/{task['target_subpath']}/"
                    ]
                else:
                    cmd = ['rsync'] + rsync_opts + file_paths + [
                        f"{target_user}@{target_server}:{target_path}/{task['target_subpath']}/"
                    ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    return {'success': True, 'task_name': task['name']}
                else:
                    return {'success': False, 'task_name': task['name'], 'error': result.stderr}
            except Exception as e:
                return {'success': False, 'task_name': task['name'], 'error': str(e)}

        # 使用线程池执行并行任务
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(execute_parallel_task, task) for task in parallel_tasks]

            # 等待所有任务完成
            completed_tasks = 0
            failed_tasks = 0

            for future in concurrent.futures.as_completed(futures):
                # 检查是否被取消
                if transfer_id not in active_transfers:
                    # 取消所有未完成的任务
                    for f in futures:
                        f.cancel()
                    raise Exception("传输被用户取消")

                result = future.result()
                if result['success']:
                    completed_tasks += 1
                    socketio.emit('transfer_log', {
                        'transfer_id': transfer_id,
                        'message': f'✅ 并行任务完成: {result["task_name"]}'
                    })
                else:
                    failed_tasks += 1
                    socketio.emit('transfer_log', {
                        'transfer_id': transfer_id,
                        'message': f'❌ 并行任务失败: {result["task_name"]} - {result.get("error", "未知错误")}'
                    })

        if failed_tasks > 0:
            raise Exception(f"目录并行传输部分失败: {failed_tasks}/{len(parallel_tasks)} 任务失败")

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'🎉 目录并行传输完成: {completed_tasks}/{len(parallel_tasks)} 任务成功'
        })

    except Exception as e:
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'⚠️ 目录并行传输失败，回退到单rsync: {str(e)}'
        })
        # 回退到单rsync传输
        return transfer_single_rsync(source_path, target_server, target_path, file_name, True, transfer_id, fast_ssh)

def transfer_file_via_remote_to_local_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """从远程服务器传输到TurboFile主机 - 使用rsync拉取模式"""
    # 如果源是NAS服务器，使用tar+ssh方案
    if is_nas_server(source_server):
        return transfer_file_from_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id)

    source_user = SERVERS[source_server]['user']
    source_password = SERVERS[source_server].get('password')

    # 检查源是否为Windows服务器
    source_is_windows = is_windows_server(source_server)

    # 🚀 极速优化：构建本地rsync命令（拉取模式）
    rsync_opts = [
        '-a',                    # 归档模式（必需）
        '--inplace',             # 就地更新，减少磁盘I/O
        '--whole-file',          # 整文件传输（局域网最快）
        '--no-compress',         # 禁用压缩（局域网环境）
        '--numeric-ids',         # 数字ID，避免用户名解析
        '--timeout=600',         # 增加超时时间
    ]

    # 处理源路径（如果是Windows，转换为Cygwin格式）
    rsync_source_path = source_path
    if source_is_windows:
        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")

    # 构建完整命令（从远程拉取到本地）
    if is_directory:
        if source_password:
            cmd = ['sshpass', '-p', source_password, 'rsync'] + rsync_opts + [f'{source_user}@{source_server}:{rsync_source_path}/', f'{target_path}/{file_name}/']
        else:
            cmd = ['rsync'] + rsync_opts + [f'{source_user}@{source_server}:{rsync_source_path}/', f'{target_path}/{file_name}/']
    else:
        if source_password:
            cmd = ['sshpass', '-p', source_password, 'rsync'] + rsync_opts + [f'{source_user}@{source_server}:{rsync_source_path}', f'{target_path}/']
        else:
            cmd = ['rsync'] + rsync_opts + [f'{source_user}@{source_server}:{rsync_source_path}', f'{target_path}/']

    # 执行rsync命令
    import subprocess
    import os
    import signal

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
        preexec_fn=os.setsid  # 创建新的进程组
    )

    # 存储进程用于取消操作
    transfer_processes[transfer_id] = {
        'type': 'subprocess',
        'process': process
    }

    # 等待传输完成（无进度读取，提升性能）
    try:
        return_code = process.wait()
        if return_code != 0:
            raise Exception(f"rsync传输失败，退出码: {return_code}")

        # 🔧 BUG修复：添加返回True表示传输成功
        return True
    except KeyboardInterrupt:
        # 处理取消操作
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=2)
        except:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
            except:
                pass
        raise Exception("传输被用户取消")

def transfer_file_via_local_to_local_instant(source_path, target_path, file_name, is_directory, transfer_id):
    """本地到本地传输 - 使用cp命令"""
    import shutil
    import subprocess

    try:
        dest_path = os.path.join(target_path, file_name)

        if is_directory:
            # 🔧 BUG修复：使用rsync代替shutil.copytree，避免目标已存在时的异常
            # rsync更可靠，支持增量复制，不会因为目标已存在而失败
            print(f"[DEBUG] 本地目录复制: {source_path} -> {dest_path}")

            # 确保源路径以/结尾（rsync语法：复制目录内容而非目录本身）
            source_with_slash = source_path if source_path.endswith('/') else source_path + '/'

            # 使用rsync进行本地复制（更可靠）
            rsync_cmd = [
                'rsync', '-a',  # 归档模式（保留权限、时间戳等）
                '--delete',     # 删除目标中多余的文件
                source_with_slash,
                dest_path
            ]

            print(f"[DEBUG] 执行命令: {' '.join(rsync_cmd)}")
            result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "未知错误"
                print(f"[ERROR] rsync失败: returncode={result.returncode}, stderr={error_msg}")
                raise Exception(f"本地目录复制失败: {error_msg}")

            print(f"[DEBUG] rsync成功: {file_name}")
        else:
            # 文件复制 - 使用cp命令更可靠
            print(f"[DEBUG] 本地文件复制: {source_path} -> {dest_path}")

            # 使用cp命令（支持覆盖）
            cp_cmd = ['cp', '-f', source_path, dest_path]

            print(f"[DEBUG] 执行命令: {' '.join(cp_cmd)}")
            result = subprocess.run(cp_cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "未知错误"
                print(f"[ERROR] cp失败: returncode={result.returncode}, stderr={error_msg}")
                raise Exception(f"本地文件复制失败: {error_msg}")

            print(f"[DEBUG] cp成功: {file_name}")

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'📁 本地复制完成: {file_name}'
        })

        print(f"[DEBUG] transfer_file_via_local_to_local_instant返回True")
        return True  # 返回成功状态

    except subprocess.TimeoutExpired:
        error_msg = f"本地复制超时: {file_name}"
        print(f"[ERROR] {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"本地复制失败: {str(e)}"
        print(f"[ERROR] {error_msg}")
        raise Exception(error_msg)

def transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """即时远程rsync传输 - 无进度监控版本，专注性能"""
    print(f"🔍 远程传输检查: 源={source_server}, 目标={target_server}")

    # 如果涉及NAS服务器，使用tar+ssh方案
    source_is_nas = is_nas_server(source_server)
    target_is_nas = is_nas_server(target_server)

    print(f"🔍 NAS检测结果: 源是NAS={source_is_nas}, 目标是NAS={target_is_nas}")

    if source_is_nas or target_is_nas:
        print(f"🚀 使用tar+ssh传输方案")
        if source_is_nas:
            print(f"📤 从NAS传输: {source_server} -> {target_server}")
            return transfer_file_from_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id)
        else:
            print(f"📥 传输到NAS: {source_server} -> {target_server}")
            return transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id)

    print(f"🔄 使用rsync传输方案")

    # 检查是否涉及Windows服务器
    source_is_windows = is_windows_server(source_server)
    target_is_windows = is_windows_server(target_server)

    print(f"🔍 Windows检测结果: 源是Windows={source_is_windows}, 目标是Windows={target_is_windows}")

    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')
    source_user = SERVERS[source_server]['user']
    source_password = SERVERS[source_server].get('password')

    # 🚀 极速优化：精简rsync参数
    rsync_base_opts = [
        "-a",                    # 归档模式（必需）
        "--inplace",             # 就地更新，减少磁盘I/O
        "--whole-file",          # 整文件传输（局域网最快）
        "--no-compress",         # 禁用压缩（局域网环境）
        "--numeric-ids",         # 数字ID，避免用户名解析
        "--timeout=600",         # 增加超时时间
    ]

    # 如果是“Windows作为源、Linux作为目标”，改为在目标Linux上发起拉取
    if source_is_windows and not target_is_windows:
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': '🔁 检测到Windows作为源，切换为在目标Linux上运行rsync从Windows拉取'
        })

        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")

        # rsync通过SSH连接到Windows源服务器
        ssh_to_source = get_ssh_command_with_port(source_server, fast_ssh)
        if is_directory:
            if source_password:
                remote_cmd = f"sshpass -p '{source_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}/' '{target_path}/{file_name}/'"
            else:
                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}/' '{target_path}/{file_name}/'"
        else:
            if source_password:
                remote_cmd = f"sshpass -p '{source_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}' '{target_path}/'"
            else:
                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}' '{target_path}/'"

        print(f"🔄 目标服务器执行的拉取命令: {remote_cmd}")

        # 在目标服务器上执行命令
        ssh = ssh_manager.get_connection(target_server)
        if not ssh:
            raise Exception(f"无法连接到目标服务器 {target_server}")

        start_time = time.time()
        stdin, stdout, stderr = ssh.exec_command(remote_cmd)
        transfer_processes[transfer_id] = {'type': 'ssh', 'channel': stdout.channel}
        exit_status = stdout.channel.recv_exit_status()
        end_time = time.time()
        transfer_duration = end_time - start_time
        output = stdout.read().decode('utf-8', errors='ignore')
        error = stderr.read().decode('utf-8', errors='ignore')
        print(f"📊 拉取完成 - 耗时: {transfer_duration:.2f}秒, 状态: {exit_status}")
        if output:
            print(f"📊 输出: {output}")
        if error:
            print(f"⚠️ 错误信息: {error}")
        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'✅ {file_name} 传输完成 - 耗时: {transfer_duration:.2f}秒'
        })
        if exit_status != 0:
            raise Exception(f"rsync拉取失败，退出码: {exit_status}, 错误: {error}")
        return True

    # —— 其他情况依旧：在源服务器执行rsync推送到目标 ——

    # 处理路径格式
    rsync_source_path = source_path
    if source_is_windows:
        rsync_source_path = convert_windows_path_to_cygwin(source_path)
        print(f"🔄 Windows源路径转换: {source_path} -> {rsync_source_path}")

    rsync_target_path = target_path
    if target_is_windows:
        rsync_target_path = convert_windows_path_to_cygwin(target_path)
        print(f"🔄 Windows目标路径转换: {target_path} -> {rsync_target_path}")

    # 构建rsync命令，优先使用sshpass，回退到SSH密钥
    if is_directory:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} '{rsync_source_path}/' '{target_user}@{target_server}:{rsync_target_path}/{file_name}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} '{rsync_source_path}/' '{target_user}@{target_server}:{rsync_target_path}/{file_name}/'"
    else:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} '{rsync_source_path}' '{target_user}@{target_server}:{rsync_target_path}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} '{rsync_source_path}' '{target_user}@{target_server}:{rsync_target_path}/'"

    print(f"🔄 远程rsync命令: {remote_cmd}")

    start_time = time.time()
    ssh = ssh_manager.get_connection(source_server)
    if not ssh:
        raise Exception(f"无法连接到源服务器 {source_server}")
    stdin, stdout, stderr = ssh.exec_command(remote_cmd)
    transfer_processes[transfer_id] = {'type': 'ssh', 'channel': stdout.channel}
    exit_status = stdout.channel.recv_exit_status()
    end_time = time.time()
    transfer_duration = end_time - start_time
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')
    print(f"📊 传输完成 - 耗时: {transfer_duration:.2f}秒")
    print(f"📊 退出状态: {exit_status}")
    if output:
        print(f"📊 输出: {output}")
    if error:
        print(f"⚠️ 错误信息: {error}")
    socketio.emit('transfer_log', {'transfer_id': transfer_id,'message': f'✅ {file_name} 传输完成 - 耗时: {transfer_duration:.2f}秒'})
    if exit_status != 0:
        raise Exception(f"rsync传输失败，退出码: {exit_status}, 错误: {error}")
    return True

def transfer_file_batch(transfer_id, source_server, file_batch, target_server, target_path, mode="copy", fast_ssh=True):
    """批量传输小文件"""
    completed = 0
    failed = 0

    for file_info in file_batch:
        try:
            # 检查是否被取消
            if transfer_id not in active_transfers:
                break

            result = transfer_single_file(transfer_id, source_server, file_info, target_server, target_path, mode, fast_ssh)
            completed += result['completed_files']
            failed += result['failed_files']

        except Exception as e:
            failed += 1
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'❌ 批量传输失败: {str(e)}'
            })

    return {'completed_files': completed, 'failed_files': failed}

def transfer_file_via_remote_rsync(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """通过远程rsync传输文件"""
    # 如果涉及NAS服务器，使用tar+ssh方案
    if is_nas_server(source_server) or is_nas_server(target_server):
        if is_nas_server(source_server):
            return transfer_file_from_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id)
        else:
            return transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id)

    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    # 使用统一的SSH命令构建函数（支持自定义端口）
    ssh_cmd = get_ssh_command_with_port(target_server, fast_ssh)

    # 🚀 极速优化：精简rsync参数
    rsync_base_opts = [
        "-a",                    # 归档模式（必需）
        "--inplace",             # 就地更新，减少磁盘I/O
        "--whole-file",          # 整文件传输（局域网最快）
        "--no-compress",         # 禁用压缩（局域网环境）
        "--numeric-ids",         # 数字ID，避免用户名解析
        "--timeout=600",         # 增加超时时间
    ]

    # 构建rsync命令
    if is_directory:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_cmd}' '{source_path}/' '{target_user}@{target_server}:{target_path}/{file_name}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_cmd}' '{source_path}/' '{target_user}@{target_server}:{target_path}/{file_name}/'"
    else:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_cmd}' '{source_path}' '{target_user}@{target_server}:{target_path}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_cmd}' '{source_path}' '{target_user}@{target_server}:{target_path}/'"

    # 在源服务器上执行rsync命令
    ssh = ssh_manager.get_connection(source_server)
    if not ssh:
        raise Exception(f"无法连接到源服务器 {source_server}")

    start_time = time.time()

    # 执行rsync并实时读取进度
    _, stdout, stderr = ssh.exec_command(remote_cmd)

    # 存储SSH通道用于取消操作
    transfer_processes[transfer_id] = {
        'type': 'ssh',
        'channel': stdout.channel
    }

    # 等待传输完成（无进度读取，提升性能）
    exit_status = stdout.channel.recv_exit_status()

    # 读取输出和错误信息
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')

    if exit_status != 0:
        raise Exception(f"rsync传输失败 (退出码: {exit_status}): {error}")

def start_sequential_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """原始的顺序传输逻辑（作为备用）"""
    total_files = len(source_files)
    completed_files = 0

    # 启动传输计时器
    time_tracker.start_transfer(transfer_id)

    # 初始化速度模拟器（NAS传输使用38~40MB/s波动）
    if is_nas_server(source_server) or is_nas_server(target_server):
        speed_simulator.init_transfer_speed(transfer_id, 38.0, 40.0)
    else:
        speed_simulator.init_transfer_speed(transfer_id)

    for file_info in source_files:
        # 检查是否被取消
        if transfer_id not in active_transfers:
            print(f"传输 {transfer_id} 已被取消")
            return

        source_path = file_info['path']
        file_name = file_info['name']
        is_directory = file_info['is_directory']

        # 判断传输模式
        is_local_source = is_local_server(source_server)
        is_local_target = is_local_server(target_server)

        if is_local_source and not is_local_target:
            transfer_mode = 'local_to_remote'
        elif not is_local_source and is_local_target:
            transfer_mode = 'remote_to_local'
        else:
            transfer_mode = 'remote_to_remote'

        simulated_speed = speed_simulator.get_simulated_speed(transfer_id)
        elapsed_time = time_tracker.get_elapsed_time(transfer_id)

        # 进度更新已移除以提升性能

        # 构建rsync命令
        # 智能判断传输模式
        is_local_source = is_local_server(source_server)

        if is_local_source:
            # 🚀 本地传输模式：完全使用rsync，移除Paramiko SFTP开销
            success = transfer_file_via_local_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, completed_files, total_files)
            if not success:
                raise Exception("本地传输失败")
        else:
                    # 远程到远程传输
                    print(f"🔍 并行传输NAS检查: 源={source_server}, 目标={target_server}")

                    # 如果涉及NAS服务器，使用tar+ssh方案
                    source_is_nas = is_nas_server(source_server)
                    target_is_nas = is_nas_server(target_server)

                    print(f"🔍 并行传输NAS检测结果: 源是NAS={source_is_nas}, 目标是NAS={target_is_nas}")

                    if source_is_nas or target_is_nas:
                        print(f"🚀 并行传输使用tar+ssh方案")
                        if source_is_nas:
                            print(f"📤 并行传输从NAS: {source_server} -> {target_server}")
                            success = transfer_file_from_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id)
                        else:
                            print(f"📥 并行传输到NAS: {source_server} -> {target_server}")
                            # 对于远程到NAS的传输，使用专门的远程tar+ssh方法
                            success = transfer_remote_to_nas_via_tar_ssh(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id)

                        if not success:
                            raise Exception("NAS tar+ssh传输失败")

                        # 进度更新已移除以提升性能

                        # NAS传输成功，继续执行后续逻辑而不是直接返回
                        print(f"✅ NAS传输成功，继续处理后续逻辑")

                        # 跳过rsync逻辑，直接进入下一个文件
                        completed_files += 1
                        continue

                    print(f"🔄 并行传输使用rsync方案")
                    # 远程到远程：根据Windows参与方选择推送或拉取策略
                    target_user = SERVERS[target_server]['user']
                    target_password = SERVERS[target_server].get('password')
                    source_user = SERVERS[source_server]['user']
                    source_password = SERVERS[source_server].get('password')

                    # 使用统一的SSH命令构建函数（支持自定义端口）
                    ssh_to_target = get_ssh_command_with_port(target_server, fast_ssh)

                    # 优化的rsync参数（兼容性优先）- 移除进度监控以提升性能
                    rsync_base_opts = [
                        "-a",                    # 归档模式
                        "--inplace",             # 就地更新
                        "--whole-file",          # 整文件传输
                        "--timeout=300",         # 超时设置
                        "--partial",             # 断点续传
                        "--numeric-ids",         # 数字ID
                    ]

                    if fast_ssh:
                        rsync_base_opts.append("--no-compress")
                    else:
                        rsync_base_opts.append("-z")

                    source_is_windows = is_windows_server(source_server)
                    target_is_windows = is_windows_server(target_server)

                    # 情况A：Windows作为源，Linux作为目标 -> 在目标Linux上拉取
                    if source_is_windows and not target_is_windows:
                        ssh_to_source = get_ssh_command_with_port(source_server, fast_ssh)
                        rsync_source_path = convert_windows_path_to_cygwin(source_path)
                        if is_directory:
                            if source_password:
                                remote_cmd = f"sshpass -p '{source_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}/' '{target_path}/{file_name}/'"
                            else:
                                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}/' '{target_path}/{file_name}/'"
                        else:
                            if source_password:
                                remote_cmd = f"sshpass -p '{source_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}' '{target_path}/'"
                            else:
                                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_source}' '{source_user}@{source_server}:{rsync_source_path}' '{target_path}/'"

                        # 在目标服务器上执行拉取命令
                        ssh = ssh_manager.get_connection(target_server)
                        if not ssh:
                            raise Exception(f"无法连接到目标服务器 {target_server}")
                    else:
                        # 其他情况保持原逻辑：在源服务器上执行rsync推送到目标
                        # 路径适配：若目标为Windows则转换目标路径；若源为Windows则转换源路径
                        rsync_target_path = convert_windows_path_to_cygwin(target_path) if target_is_windows else target_path
                        rsync_source_path = convert_windows_path_to_cygwin(source_path) if source_is_windows else source_path

                        if is_directory:
                            if target_password:
                                remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_target}' '{rsync_source_path}/' '{target_user}@{target_server}:{rsync_target_path}/{file_name}/'"
                            else:
                                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_target}' '{rsync_source_path}/' '{target_user}@{target_server}:{rsync_target_path}/{file_name}/'"
                        else:
                            if target_password:
                                remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} -e '{ssh_to_target}' '{rsync_source_path}' '{target_user}@{target_server}:{rsync_target_path}/'"
                            else:
                                remote_cmd = f"rsync {' '.join(rsync_base_opts)} -e '{ssh_to_target}' '{rsync_source_path}' '{target_user}@{target_server}:{rsync_target_path}/'"

                        # 在源服务器上执行推送命令
                        ssh = ssh_manager.get_connection(source_server)
                        if not ssh:
                            raise Exception(f"无法连接到源服务器 {source_server}")

                    import time
                    start_time = time.time()

                    socketio.emit('transfer_log', {
                        'transfer_id': transfer_id,
                        'message': f'⚡️ 开始传输 {file_name}...'
                    })

                    # 执行rsync
                    _, stdout, stderr = ssh.exec_command(remote_cmd)

                    # 存储SSH通道用于取消操作
                    transfer_processes[transfer_id] = {
                        'type': 'ssh',
                        'channel': stdout.channel
                    }

                    # 等待传输完成
                    exit_status = stdout.channel.recv_exit_status()

                    # 读取输出和错误信息
                    output = stdout.read().decode('utf-8', errors='ignore')
                    error = stderr.read().decode('utf-8', errors='ignore')

                    if exit_status != 0:
                        raise Exception(f"传输 {file_name} 失败: {error}")

                    # 计算传输耗时
                    end_time = time.time()
                    duration = end_time - start_time

                    # 格式化耗时显示
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

                    socketio.emit('transfer_log', {
                        'transfer_id': transfer_id,
                        'message': f'✅ {file_name} 传输完成，耗时: {time_str}'
                    })

        completed_files += 1

        # 如果是移动模式，删除源文件
        if mode == "move" and not is_local_server(source_server):
            delete_cmd = f"rm -rf '{source_path}'"
            ssh_manager.execute_command(source_server, delete_cmd)

    # 结束传输计时
    total_time = time_tracker.end_transfer(transfer_id)

    # 🚀 性能监控：记录传输性能数据
    print(f"[性能监控] 传输ID: {transfer_id}")
    print(f"[性能监控] 文件数量: {len(source_files)}")
    print(f"[性能监控] 传输时间: {total_time}")

    # 传输完成
    socketio.emit('transfer_complete', {
        'transfer_id': transfer_id,
        'status': 'success',
        'message': f'成功传输 {len(source_files)} 个文件/文件夹',
        'total_time': total_time
    })

def format_file_size(bytes_str):
    """将字节数转换为人性化的文件大小显示"""
    try:
        # 移除逗号并转换为整数
        bytes_num = int(bytes_str.replace(',', ''))

        # 转换为不同单位
        if bytes_num < 1024 * 1024:  # 小于1MB
            return f"{bytes_num / 1024:.1f} KB"
        elif bytes_num < 1024 * 1024 * 1024:  # 小于1GB
            return f"{bytes_num / (1024 * 1024):.1f} MB"
        elif bytes_num < 1024 * 1024 * 1024 * 1024:  # 小于1TB
            return f"{bytes_num / (1024 * 1024 * 1024):.2f} GB"
        else:  # 1TB及以上
            return f"{bytes_num / (1024 * 1024 * 1024 * 1024):.2f} TB"
    except (ValueError, AttributeError):
        return bytes_str

def parse_rsync_progress(line):
    """解析rsync进度输出，支持--info=progress2格式"""
    import re

    # 解析--info=progress2格式，例如：
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

    # 解析详细进度行，例如：
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

    # 解析传输完成信息
    if "sent" in line and "received" in line and "bytes/sec" in line:
        return {
            'type': 'summary',
            'message': f"传输完成: {line}"
        }

    return None

# Web路由
@app.route('/')
def index():
    return render_template('index.html', servers=SERVERS)

@app.route('/api/image/stream')
def api_image_stream():
    server_ip = request.args.get('server')
    path = request.args.get('path')
    if not server_ip or not path:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    try:
        # 本地读取
        if is_local_server(server_ip):
            def generate():
                with open(path, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            return Response(generate(), mimetype='application/octet-stream')
        # 远程读取
        ssh = ssh_manager.get_connection(server_ip)
        if not ssh:
            return jsonify({'success': False, 'error': 'SSH连接失败'}), 500
        sftp = ssh.open_sftp()
        def generate_sftp():
            try:
                with sftp.file(path, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk if isinstance(chunk, (bytes, bytearray)) else bytes(chunk)
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        return Response(generate_sftp(), mimetype='application/octet-stream')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/file/read', methods=['GET'])
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

@app.route('/api/file/save', methods=['POST'])
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


@app.route('/api/servers')
def get_servers():
    return jsonify(SERVERS)

@app.route('/api/windows_drives/<server_ip>')
def get_windows_drives(server_ip):
    """获取Windows服务器的磁盘列表"""
    if not is_windows_server(server_ip):
        return jsonify({
            'success': False,
            'error': '不是Windows服务器'
        })

    try:
        # 使用wmic命令获取逻辑磁盘列表
        command = 'wmic logicaldisk get caption,drivetype,volumename'
        output, error = ssh_manager.execute_command(server_ip, command)

        if error:
            print(f"获取磁盘列表失败: {error}")
            # 如果wmic失败，返回常见的磁盘列表
            return jsonify({
                'success': True,
                'drives': [
                    {'letter': 'C:', 'name': 'C盘', 'type': 'local'},
                    {'letter': 'D:', 'name': 'D盘', 'type': 'local'},
                    {'letter': 'E:', 'name': 'E盘', 'type': 'local'}
                ]
            })

        drives = []
        lines = output.strip().split('\n')

        # 跳过标题行
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) >= 2:
                caption = parts[0]  # 例如: C:
                drive_type = parts[1]  # 3=本地磁盘, 4=网络驱动器, 5=CD-ROM
                volume_name = ' '.join(parts[2:]) if len(parts) > 2 else ''

                # 只返回本地磁盘和网络驱动器
                if drive_type in ['3', '4']:
                    drive_name = f"{caption}"
                    if volume_name:
                        drive_name += f" ({volume_name})"

                    drives.append({
                        'letter': caption,
                        'name': drive_name,
                        'type': 'local' if drive_type == '3' else 'network'
                    })

        # 如果没有找到磁盘，返回默认列表
        if not drives:
            drives = [
                {'letter': 'C:', 'name': 'C盘', 'type': 'local'},
                {'letter': 'D:', 'name': 'D盘', 'type': 'local'}
            ]

        return jsonify({
            'success': True,
            'drives': drives
        })
    except Exception as e:
        print(f"获取Windows磁盘列表异常: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/browse/<server_ip>')
def browse_directory(server_ip):
    # 使用动态默认路径
    default_path = get_default_path(server_ip)
    path = request.args.get('path', default_path)
    show_hidden = request.args.get('show_hidden', 'false').lower() == 'true'
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'

    # 性能监控
    start_time = time.time()

    try:
        # 如果是强制刷新，先清除缓存
        cleared_count = 0
        if force_refresh:
            cleared_count = clear_cached_listing(server_ip, path)
            print(f"🔄 强制刷新: 清除了 {cleared_count} 个缓存项 - {server_ip}:{path}")

        # 获取目录列表（如果清除了缓存，将重新获取）
        files = get_directory_listing_optimized(server_ip, path, show_hidden)

        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # 转换为毫秒

        return jsonify({
            'success': True,
            'path': path,
            'files': files,
            'show_hidden': show_hidden,
            'force_refresh': force_refresh,
            'cache_cleared': cleared_count if force_refresh else 0,
            'response_time': round(response_time, 2),  # 添加响应时间信息
            'file_count': len(files)
        })
    except Exception as e:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000

        return jsonify({
            'success': False,
            'error': str(e),
            'response_time': round(response_time, 2)
        })

@socketio.on('start_transfer')
def handle_start_transfer(data):
    transfer_id = f"transfer_{int(time.time())}"

    # 更新并行传输配置
    PARALLEL_TRANSFER_CONFIG['enable_parallel'] = data.get('parallel_transfer', True)

    # 记录传输任务
    active_transfers[transfer_id] = {
        'source_server': data['source_server'],
        'source_files': data['source_files'],
        'target_server': data['target_server'],
        'target_path': data['target_path'],
        'mode': data.get('mode', 'copy'),
        'parallel_enabled': data.get('parallel_transfer', True),
        'start_time': datetime.now()
    }

    # 启动即时并行传输
    start_instant_parallel_transfer(
        transfer_id,
        data['source_server'],
        data['source_files'],
        data['target_server'],
        data['target_path'],
        data.get('mode', 'copy'),
        data.get('fast_ssh', True)
    )

    emit('transfer_started', {'transfer_id': transfer_id})

@socketio.on('cancel_transfer')
def handle_cancel_transfer(data):
    """处理取消传输请求"""
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

    # 立即强制终止相关进程
    if transfer_id in transfer_processes:
        process_info = transfer_processes[transfer_id]
        try:
            if process_info['type'] == 'subprocess':
                # 强制终止subprocess进程和整个进程组
                process = process_info['process']
                import os
                import signal

                try:
                    if force_cancel:
                        # 强制取消：立即使用SIGKILL
                        print(f"强制取消模式，立即杀死进程组: {transfer_id}")
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait()
                        print(f"已强制杀死subprocess进程组: {transfer_id}")
                    else:
                        # 普通取消：先尝试优雅终止
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        try:
                            process.wait(timeout=1)  # 只等待1秒
                            print(f"已优雅终止subprocess进程组: {transfer_id}")
                        except subprocess.TimeoutExpired:
                            # 1秒内没有终止，立即强制杀死
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            process.wait()
                            print(f"已强制杀死subprocess进程组: {transfer_id}")
                except ProcessLookupError:
                    # 进程已经不存在
                    print(f"进程组已不存在: {transfer_id}")
                except Exception as e:
                    # 如果进程组操作失败，回退到单进程终止
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
                    except:
                        pass

            elif process_info['type'] == 'ssh':
                # 强制关闭SSH通道和连接
                channel = process_info['channel']
                try:
                    # 发送中断信号到远程命令
                    channel.send('\x03')  # Ctrl+C
                    channel.close()
                    print(f"已发送中断信号并关闭SSH通道: {transfer_id}")
                except:
                    try:
                        channel.close()
                        print(f"已强制关闭SSH通道: {transfer_id}")
                    except:
                        pass
        except Exception as e:
            print(f"终止进程时出错: {e}")

    # 清理传输记录
    if transfer_id in active_transfers:
        del active_transfers[transfer_id]
    if transfer_id in transfer_processes:
        del transfer_processes[transfer_id]

    # 发送取消确认
    emit('transfer_cancelled', {
        'transfer_id': transfer_id,
        'status': 'success',
        'message': '传输已取消'
    })

    print(f"传输 {transfer_id} 已成功取消")

@socketio.on('connect')
def handle_connect():
    print('客户端已连接')

@socketio.on('disconnect')
def handle_disconnect():
    print('客户端已断开连接')

def transfer_file_via_local_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, completed_files=0, total_files=1):
    """使用本地rsync高速传输（与原始脚本相同的方式）"""
    try:
        # 如果目标是NAS服务器，使用tar+ssh方案
        if is_nas_server(target_server):
            return transfer_file_via_tar_ssh(source_path, target_server, target_path, file_name, is_directory, transfer_id)

        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')

        # 使用统一的SSH命令构建函数（支持自定义端口）
        ssh_opts_str = get_ssh_command_with_port(target_server, fast_ssh)

        # 目标为Windows时，规范化并转换为Cygwin路径
        final_target_path = target_path
        if is_windows_server(target_server):
            normalized = normalize_windows_path_for_transfer(target_path)
            final_target_path = convert_windows_path_to_cygwin(normalized)
            print(f"🔄 Windows目标路径转换(本地rsync): {target_path} -> {final_target_path}")

        # 构建rsync命令
        if is_directory:
            # 目录传输，确保以/结尾
            source_with_slash = source_path.rstrip('/') + '/'
            target_full_path = f"{final_target_path}/{file_name}/"
        else:
            # 文件传输
            source_with_slash = source_path
            target_full_path = f"{final_target_path}/"

        # 🚀 极速优化：精简rsync参数，最大化传输速度
        rsync_opts = [
            '-a',                    # 归档模式（必需）
            '--inplace',             # 就地更新，减少磁盘I/O
            '--whole-file',          # 整文件传输（局域网最快）
            '--no-compress',         # 禁用压缩（局域网环境）
            '--numeric-ids',         # 数字ID，避免用户名解析
            '--timeout=600',         # 增加超时时间
        ]

        if target_password:
            # 使用密码认证
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + [
                '-e', ssh_opts_str,
                source_with_slash,
                f"{target_user}@{target_server}:{target_full_path}"
            ]
        else:
            # 使用SSH密钥认证（最快）
            cmd = ['rsync'] + rsync_opts + [
                '-e', ssh_opts_str,
                source_with_slash,
                f"{target_user}@{target_server}:{target_full_path}"
            ]



        # 使用subprocess执行本地命令，实时获取输出
        import subprocess
        import os
        import signal

        # 创建新的进程组，便于强制终止
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            preexec_fn=os.setsid  # 创建新的进程组
        )

        # 存储进程用于取消操作
        transfer_processes[transfer_id] = {
            'type': 'subprocess',
            'process': process
        }

        import time
        start_time = time.time()

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'⚡️ 开始传输 {file_name}...'
        })

        # 实时显示传输进度
        # 等待传输完成（无进度读取，提升性能）
        try:
            return_code = process.wait()
            if return_code != 0:
                raise Exception(f"本地rsync传输失败，退出码: {return_code}")
        except KeyboardInterrupt:
            # 处理取消操作
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=2)
            except:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait()
                except:
                    pass
            raise Exception("传输被用户取消")

        # 计算传输耗时
        end_time = time.time()
        duration = end_time - start_time

        # 格式化耗时显示
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

        socketio.emit('transfer_log', {
            'transfer_id': transfer_id,
            'message': f'✅ {file_name} 传输完成，耗时: {time_str}'
        })

        return True  # 返回成功状态

    except Exception as e:
        raise Exception(f"本地rsync传输失败: {str(e)}")

def transfer_file_via_paramiko(source_path, target_server, target_path, file_name, is_directory, transfer_id):
    """使用paramiko传输文件（本地到远程）"""
    ssh = ssh_manager.get_connection(target_server)
    if not ssh:
        raise Exception(f"无法连接到目标服务器 {target_server}")

    sftp = ssh.open_sftp()

    try:
        if is_directory:
            # 传输目录
            remote_dir_path = f"{target_path}/{file_name}"
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'正在传输目录: {file_name}'
            })
            transfer_directory_to_remote(sftp, source_path, remote_dir_path, transfer_id)
        else:
            # 传输文件
            remote_file_path = f"{target_path}/{file_name}"
            socketio.emit('transfer_log', {
                'transfer_id': transfer_id,
                'message': f'正在传输文件: {file_name}'
            })
            sftp.put(source_path, remote_file_path)
    finally:
        sftp.close()



def transfer_directory_to_remote(sftp, local_dir, remote_dir, transfer_id):
    """递归传输目录到远程"""
    try:
        sftp.mkdir(remote_dir)
    except:
        pass  # 目录可能已存在

    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = f"{remote_dir}/{item}"

        if os.path.isfile(local_path):
            sftp.put(local_path, remote_path)
        elif os.path.isdir(local_path):
            transfer_directory_to_remote(sftp, local_path, remote_path, transfer_id)

def transfer_directory_from_remote(sftp, remote_dir, local_dir, transfer_id):
    """递归从远程传输目录到本地"""
    os.makedirs(local_dir, exist_ok=True)

    for item in sftp.listdir(remote_dir):
        remote_path = f"{remote_dir}/{item}"
        local_path = os.path.join(local_dir, item)

        try:
            stat = sftp.stat(remote_path)
            if stat.st_mode & 0o040000:  # 目录
                transfer_directory_from_remote(sftp, remote_path, local_path, transfer_id)
            else:  # 文件
                sftp.get(remote_path, local_path)
        except:
            pass

if __name__ == '__main__':
    # 确保模板目录存在
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)

    # 检查是否在生产环境中运行
    import sys
    is_production = len(sys.argv) > 1 and sys.argv[1] == '--production'

    print("🚀 启动Web文件传输系统...")
    print("📱 访问地址: http://192.168.9.62:5000")
    print("🔧 确保所有服务器SSH密钥已配置")

    if is_production:
        print("🏭 生产模式启动")
        # 生产环境配置 - 使用简单的开发服务器但关闭调试
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    else:
        print("🛠️  开发模式启动")
        # 开发环境配置
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
