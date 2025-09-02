#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web文件传输系统 - 主应用
基于现有的rsync传输脚本，提供Web界面控制
"""

from flask import Flask, render_template, request, jsonify
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
from datetime import datetime
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# 服务器配置
SERVERS = {
    "192.168.9.62": {"name": "训练服务器1", "user": "th", "password": "th123456"},
    "192.168.9.61": {"name": "训练服务器2", "user": "th", "password": "th123456"},
    "192.168.9.60": {"name": "数据服务器", "user": "th", "password": "taiho603656_0"},
    "192.168.9.57": {"name": "备份服务器", "user": "thgd", "password": "123456"}
}

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

                socketio.emit('transfer_progress', {
                    'transfer_id': transfer_id,
                    'progress': {
                        'percentage': overall_percentage,
                        'completed_files': completed_files,
                        'total_files': total_files,
                        'current_file': file_name,
                        'current_file_progress': percentage,
                        'speed': speed
                    }
                })

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

            # 发送更新
            overall_percentage = int((progress['completed_files'] / progress['total_files']) * 100)
            socketio.emit('transfer_progress', {
                'transfer_id': transfer_id,
                'progress': {
                    'percentage': overall_percentage,
                    'completed_files': progress['completed_files'],
                    'total_files': progress['total_files'],
                    'failed_files': progress['failed_files']
                }
            })

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

            # 先尝试密钥认证
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

        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
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
                    output = stdout.read().decode('utf-8')
                    error = stderr.read().decode('utf-8')
                    return output, error
                except Exception as retry_e:
                    return None, f"重连后仍然失败: {str(retry_e)}"

            return None, str(e)

ssh_manager = SSHManager()

class ParallelTransferManager:
    def __init__(self):
        self.active_transfers = {}
        self.transfer_stats = {}

    def get_file_size(self, server_ip, file_path):
        """获取文件大小"""
        if server_ip == "localhost":
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

        # 判断是否为本地传输（包括localhost和本机IP 192.168.9.62）
        local_identifiers = ["localhost", "127.0.0.1", "192.168.9.62"]
        is_local_source = source_server in local_identifiers

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

def get_directory_listing(server_ip, path="/home/th", show_hidden=False):
    """获取远程目录列表

    Args:
        server_ip: 服务器IP地址
        path: 目录路径
        show_hidden: 是否显示隐藏文件（包括WinSCP规则的隐藏文件）
    """
    # 首先检查缓存
    cached_result = get_cached_listing(server_ip, path, show_hidden)
    if cached_result is not None:
        return cached_result
    if server_ip == "localhost" or server_ip == "192.168.9.62":
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

def get_directory_listing_optimized(server_ip, path="/home/th", show_hidden=False):
    """优化的目录列表获取函数 - 专注于响应速度"""

    # 首先检查缓存 - 优先使用缓存
    cached_result = get_cached_listing(server_ip, path, show_hidden)
    if cached_result is not None:
        return cached_result

    # 如果没有缓存，使用原始函数但添加性能优化
    if server_ip == "localhost" or server_ip == "192.168.9.62":
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

def start_instant_parallel_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """启动即时并行传输任务 - 无预分析，立即开始"""
    def transfer_worker():
        try:
            total_files = len(source_files)

            # 立即初始化进度管理（基于选择的文件/文件夹数量）
            progress_manager.init_transfer(transfer_id, total_files)

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
                        if result and result.get('success', False):
                            completed_count += 1
                        else:
                            failed_count += 1

                        # 更新总体进度
                        progress_percentage = int((completed_count / total_files) * 100)
                        socketio.emit('transfer_progress', {
                            'transfer_id': transfer_id,
                            'progress': {
                                'percentage': progress_percentage,
                                'completed_files': completed_count,
                                'total_files': total_files,
                                'failed_files': failed_count
                            }
                        })

                    except Exception as e:
                        failed_count += 1
                        socketio.emit('transfer_log', {
                            'transfer_id': transfer_id,
                            'message': f'❌ 传输任务失败: {str(e)}'
                        })

            # 发送传输完成通知
            if failed_count > 0:
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'partial_success',
                    'message': f'传输完成，成功: {completed_count}, 失败: {failed_count}'
                })
            else:
                socketio.emit('transfer_complete', {
                    'transfer_id': transfer_id,
                    'status': 'success',
                    'message': f'成功传输 {completed_count} 个文件/文件夹'
                })

        except Exception as e:
            socketio.emit('transfer_complete', {
                'transfer_id': transfer_id,
                'status': 'error',
                'message': str(e)
            })
        finally:
            # 清理传输记录
            if transfer_id in active_transfers:
                del active_transfers[transfer_id]
            if transfer_id in transfer_processes:
                del transfer_processes[transfer_id]
            progress_manager.cleanup_transfer(transfer_id)

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

        # 判断传输模式（包括本机IP）
        local_identifiers = ["localhost", "127.0.0.1", "192.168.9.62"]
        is_local_source = source_server in local_identifiers

        if is_local_source:
            # 本地传输
            transfer_file_via_local_rsync_instant(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)
        else:
            # 远程传输
            transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh)

        # 如果是移动模式，删除源文件
        if mode == "move" and not is_local_source:
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
    """即时本地rsync传输 - 支持目录内部并行"""

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
    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    # 构建rsync命令
    rsync_opts = [
        '-a',                    # 归档模式
        '--info=progress2',      # 进度信息
        '--inplace',             # 就地更新
        '--whole-file',          # 整文件传输
        '--timeout=300',         # 超时设置
        '--partial',             # 断点续传
        '--numeric-ids',         # 数字ID
    ]

    # 根据网络环境添加压缩选项
    if fast_ssh:
        rsync_opts.append('--no-compress')  # 局域网不压缩
    else:
        rsync_opts.append('-z')  # WAN环境使用压缩

    # 构建完整命令
    if is_directory:
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + [f'{source_path}/', f'{target_user}@{target_server}:{target_path}/{file_name}/']
        else:
            cmd = ['rsync'] + rsync_opts + [f'{source_path}/', f'{target_user}@{target_server}:{target_path}/{file_name}/']
    else:
        if target_password:
            cmd = ['sshpass', '-p', target_password, 'rsync'] + rsync_opts + [source_path, f'{target_user}@{target_server}:{target_path}/']
        else:
            cmd = ['rsync'] + rsync_opts + [source_path, f'{target_user}@{target_server}:{target_path}/']

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

    # 简化的进度读取（不阻塞）
    while True:
        # 检查是否被取消
        if transfer_id not in active_transfers:
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

        # 非阻塞读取
        import select
        if select.select([process.stdout], [], [], 0.1)[0]:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
        else:
            continue

    # 检查退出状态
    return_code = process.poll()
    if return_code != 0:
        raise Exception(f"rsync传输失败，退出码: {return_code}")

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
            rsync_opts = ['-a', '--inplace', '--whole-file', '--timeout=300', '--partial', '--numeric-ids']
            if fast_ssh:
                rsync_opts.append('--no-compress')
            else:
                rsync_opts.append('-z')

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

def transfer_file_via_remote_rsync_instant(source_server, source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh):
    """即时远程rsync传输 - 简化版"""
    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    # 优化的rsync参数
    rsync_base_opts = [
        "-a",                    # 归档模式
        "--info=progress2",      # 进度信息
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

    # 构建rsync命令
    if is_directory:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} '{source_path}/' '{target_user}@{target_server}:{target_path}/{file_name}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} '{source_path}' '{target_user}@{target_server}:{target_path}/{file_name}/'"
    else:
        if target_password:
            remote_cmd = f"sshpass -p '{target_password}' rsync {' '.join(rsync_base_opts)} '{source_path}' '{target_user}@{target_server}:{target_path}/'"
        else:
            remote_cmd = f"rsync {' '.join(rsync_base_opts)} '{source_path}' '{target_user}@{target_server}:{target_path}/'"

    # 在源服务器上执行rsync命令
    ssh = ssh_manager.get_connection(source_server)
    if not ssh:
        raise Exception(f"无法连接到源服务器 {source_server}")

    # 执行rsync
    _, stdout, stderr = ssh.exec_command(remote_cmd)

    # 存储SSH通道用于取消操作
    transfer_processes[transfer_id] = {
        'type': 'ssh',
        'channel': stdout.channel
    }

    # 简化的进度读取
    while True:
        # 检查是否被取消
        if transfer_id not in active_transfers:
            try:
                stdout.channel.send('\x03')  # Ctrl+C
                stdout.channel.close()
                stderr.channel.close()
            except:
                pass
            raise Exception("传输被用户取消")

        if stdout.channel.recv_ready():
            line = stdout.readline()
            if not line:
                break

        # 检查命令是否完成
        if stdout.channel.exit_status_ready():
            break

        time.sleep(0.1)

    # 检查退出状态
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error_output = stderr.read().decode('utf-8')
        raise Exception(f"rsync传输失败: {error_output}")

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
    target_user = SERVERS[target_server]['user']
    target_password = SERVERS[target_server].get('password')

    # 优化的SSH命令配置
    ssh_cmd_parts = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "PasswordAuthentication=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "TCPKeepAlive=yes",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=/tmp/ssh-%r@%h:%p",
        "-o", "ControlPersist=300"
    ]

    if fast_ssh:
        ssh_cmd_parts.extend([
            "-o", "Compression=no",
            "-o", "Ciphers=aes128-ctr,aes192-ctr,aes256-ctr",
            "-o", "MACs=hmac-sha2-256,hmac-sha2-512"
        ])

    ssh_cmd = " ".join(ssh_cmd_parts)

    # 优化的rsync参数（兼容性优先）
    rsync_base_opts = [
        "-a",                    # 归档模式
        "--info=progress2",      # 进度信息
        "--inplace",             # 就地更新
        "--whole-file",          # 整文件传输
        "--timeout=300",         # 超时设置
        "--partial",             # 断点续传
        "--numeric-ids",         # 数字ID
    ]

    # 根据网络环境添加压缩选项
    if fast_ssh:
        rsync_base_opts.append("--no-compress")  # 局域网不压缩
    else:
        rsync_base_opts.append("-z")  # WAN环境使用压缩

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

    # 实时读取输出显示进度
    while True:
        # 检查是否被取消
        if transfer_id not in active_transfers:
            try:
                stdout.channel.send('\x03')  # Ctrl+C
                stdout.channel.close()
                stderr.channel.close()
            except:
                pass
            raise Exception("传输被用户取消")

        if stdout.channel.recv_ready():
            line = stdout.readline()
            if line:
                line_text = line.strip()
                if line_text:
                    # 解析进度信息并更新进度管理器
                    progress_info = parse_rsync_progress(line_text)
                    if progress_info and progress_info.get('percentage', 0) > 0:
                        progress_manager.update_file_progress(
                            transfer_id,
                            file_name,
                            progress_info.get('percentage', 0),
                            progress_info.get('bytes_transferred', 0),
                            progress_info.get('speed', '')
                        )

        # 检查命令是否完成
        if stdout.channel.exit_status_ready():
            break

        time.sleep(0.1)

    # 检查退出状态
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error_output = stderr.read().decode('utf-8')
        raise Exception(f"rsync传输失败: {error_output}")

def start_sequential_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """原始的顺序传输逻辑（作为备用）"""
    total_files = len(source_files)
    completed_files = 0

    for file_info in source_files:
        # 检查是否被取消
        if transfer_id not in active_transfers:
            print(f"传输 {transfer_id} 已被取消")
            return

        source_path = file_info['path']
        file_name = file_info['name']
        is_directory = file_info['is_directory']

        socketio.emit('transfer_progress', {
            'transfer_id': transfer_id,
            'progress': {
                'current_file': file_name,
                'completed_files': completed_files,
                'total_files': total_files,
                'percentage': int((completed_files / total_files) * 100)
            }
        })

        # 构建rsync命令
        # 判断是否为本地传输（包括localhost和本机IP 192.168.9.62）
        local_identifiers = ["localhost", "127.0.0.1", "192.168.9.62"]
        is_local_source = source_server in local_identifiers

        if is_local_source:
            # 🚀 本地传输模式：完全使用rsync，移除Paramiko SFTP开销
            transfer_file_via_local_rsync(source_path, target_server, target_path, file_name, is_directory, transfer_id, fast_ssh, completed_files, total_files)
        else:
                    # 远程到远程传输，直接在源服务器执行rsync（就像原始脚本）
                    target_user = SERVERS[target_server]['user']
                    target_password = SERVERS[target_server].get('password')

                    # 优化的SSH命令配置
                    ssh_cmd_parts = [
                        "ssh",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "PasswordAuthentication=yes",
                        "-o", "ConnectTimeout=10",
                        "-o", "ServerAliveInterval=30",
                        "-o", "ServerAliveCountMax=3",
                        "-o", "TCPKeepAlive=yes",
                        "-o", "ControlMaster=auto",
                        "-o", "ControlPath=/tmp/ssh-%r@%h:%p",
                        "-o", "ControlPersist=300"
                    ]

                    if fast_ssh:
                        ssh_cmd_parts.extend([
                            "-o", "Compression=no",
                            "-o", "Ciphers=aes128-ctr,aes192-ctr,aes256-ctr",
                            "-o", "MACs=hmac-sha2-256,hmac-sha2-512"
                        ])

                    ssh_cmd = " ".join(ssh_cmd_parts)

                    # 优化的rsync参数（兼容性优先）
                    rsync_base_opts = [
                        "-a",                    # 归档模式
                        "--info=progress2",      # 进度信息
                        "--inplace",             # 就地更新
                        "--whole-file",          # 整文件传输
                        "--timeout=300",         # 超时设置
                        "--partial",             # 断点续传
                        "--numeric-ids",         # 数字ID
                    ]

                    # 根据网络环境添加压缩选项
                    if fast_ssh:
                        rsync_base_opts.append("--no-compress")  # 局域网不压缩
                    else:
                        rsync_base_opts.append("-z")  # WAN环境使用压缩

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

                    import time
                    start_time = time.time()

                    socketio.emit('transfer_log', {
                        'transfer_id': transfer_id,
                        'message': f'⚡️ 开始传输 {file_name}...'
                    })

                    # 执行rsync并实时读取进度
                    _, stdout, stderr = ssh.exec_command(remote_cmd)

                    # 存储SSH通道用于取消操作
                    transfer_processes[transfer_id] = {
                        'type': 'ssh',
                        'channel': stdout.channel
                    }

                    # 实时读取输出显示进度
                    while True:
                        # 检查是否被取消
                        if transfer_id not in active_transfers:
                            print(f"传输 {transfer_id} 已被取消，强制终止SSH命令")
                            try:
                                # 发送中断信号到远程命令
                                stdout.channel.send('\x03')  # Ctrl+C
                                stdout.channel.close()
                                stderr.channel.close()
                            except:
                                try:
                                    stdout.channel.close()
                                    stderr.channel.close()
                                except:
                                    pass
                            return

                        if stdout.channel.recv_ready():
                            line = stdout.readline()
                            if line:
                                line_text = line.strip()
                                if line_text:
                                    # 解析并显示进度信息
                                    progress_info = parse_rsync_progress(line_text)
                                    if progress_info:
                                        # 更新进度条
                                        socketio.emit('transfer_progress', {
                                            'transfer_id': transfer_id,
                                            'progress': {
                                                'percentage': progress_info.get('percentage', 0),
                                                'speed': progress_info.get('speed', ''),
                                                'bytes_transferred': progress_info.get('bytes_transferred_formatted', progress_info.get('bytes_transferred', '')),
                                                'eta': progress_info.get('eta', ''),
                                                'current_file': file_name,
                                                'completed_files': completed_files,
                                                'total_files': total_files
                                            }
                                        })

                                        # 禁用传输过程中的详细日志打印
                                        # 只保留错误日志和开始/完成消息

                        # 检查命令是否完成
                        if stdout.channel.exit_status_ready():
                            break

                        time.sleep(0.1)

                    # 检查退出状态
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status != 0:
                        # 只在出错时才读取错误信息
                        error_output = stderr.read().decode('utf-8')
                        raise Exception(f"传输 {file_name} 失败: {error_output}")

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
        if mode == "move" and source_server != "localhost":
            delete_cmd = f"rm -rf '{source_path}'"
            ssh_manager.execute_command(source_server, delete_cmd)

    # 传输完成
    socketio.emit('transfer_complete', {
        'transfer_id': transfer_id,
        'status': 'success',
        'message': f'成功传输 {len(source_files)} 个文件/文件夹'
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

@app.route('/api/servers')
def get_servers():
    return jsonify(SERVERS)

@app.route('/api/browse/<server_ip>')
def browse_directory(server_ip):
    path = request.args.get('path', '/home/th')
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
        target_config = SERVERS[target_server]
        target_user = target_config['user']
        target_password = target_config.get('password')

        # 优化的SSH选项配置
        ssh_opts = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "PasswordAuthentication=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "TCPKeepAlive=yes",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=/tmp/ssh-%r@%h:%p",
            "-o", "ControlPersist=300",  # 连接复用5分钟
        ]

        if fast_ssh:
            ssh_opts.extend([
                "-o", "Compression=no",
                "-o", "Ciphers=aes128-ctr,aes192-ctr,aes256-ctr",  # 快速加密算法
                "-o", "MACs=hmac-sha2-256,hmac-sha2-512",  # 快速MAC算法
            ])

        ssh_opts_str = " ".join(ssh_opts)

        # 构建rsync命令（与原始脚本完全相同）
        if is_directory:
            # 目录传输，确保以/结尾
            source_with_slash = source_path.rstrip('/') + '/'
            target_full_path = f"{target_path}/{file_name}/"
        else:
            # 文件传输
            source_with_slash = source_path
            target_full_path = f"{target_path}/"

        # 🚀 极速优化：优先使用SSH密钥，避免密码认证开销
        # 优化的rsync参数配置（兼容性优先）
        rsync_opts = [
            '-a',                    # 归档模式
            '--inplace',             # 就地更新，减少磁盘I/O
            '--whole-file',          # 局域网传输整个文件更快
            '--info=progress2',      # 进度信息格式
            '--timeout=300',         # 5分钟超时
            '--partial',             # 支持断点续传
            '--numeric-ids',         # 使用数字ID，避免用户名解析
        ]

        # 根据网络环境添加压缩选项
        if fast_ssh:
            rsync_opts.append('--no-compress')  # 局域网不压缩
        else:
            rsync_opts.append('-z')  # WAN环境使用压缩

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
        while True:
            # 检查是否被取消
            if transfer_id not in active_transfers:
                print(f"传输 {transfer_id} 已被取消，强制终止进程")
                try:
                    # 首先尝试终止整个进程组
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    try:
                        process.wait(timeout=2)  # 等待2秒
                    except subprocess.TimeoutExpired:
                        # 如果2秒内没有终止，强制杀死
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait()
                except Exception as e:
                    print(f"强制终止进程时出错: {e}")
                    try:
                        process.kill()
                        process.wait()
                    except:
                        pass
                return

            # 使用非阻塞读取，避免卡在readline上
            import select
            if select.select([process.stdout], [], [], 0.1)[0]:  # 100ms超时
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
            else:
                # 没有输出时继续检查取消状态
                continue

            if output:
                line = output.strip()
                if line:
                    # 解析并显示进度信息
                    progress_info = parse_rsync_progress(line)
                    if progress_info:
                        # 更新进度条
                        socketio.emit('transfer_progress', {
                            'transfer_id': transfer_id,
                            'progress': {
                                'percentage': progress_info.get('percentage', 0),
                                'speed': progress_info.get('speed', ''),
                                'bytes_transferred': progress_info.get('bytes_transferred_formatted', progress_info.get('bytes_transferred', '')),
                                'eta': progress_info.get('eta', ''),
                                'current_file': file_name,
                                'completed_files': completed_files,
                                'total_files': total_files
                            }
                        })

                        # 禁用传输过程中的详细日志打印
                        # 只保留错误日志和开始/完成消息

        # 检查退出状态
        return_code = process.poll()
        if return_code != 0:
            raise Exception(f"本地rsync传输失败，退出码: {return_code}")

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
