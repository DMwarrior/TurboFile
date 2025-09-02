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
from datetime import datetime

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

class SSHManager:
    def __init__(self):
        self.connections = {}
    
    def get_connection(self, server_ip):
        """获取SSH连接，如果不存在则创建新连接"""
        if server_ip not in self.connections:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                server_config = SERVERS[server_ip]
                # 先尝试密钥认证，失败后使用密码认证
                try:
                    ssh.connect(
                        server_ip,
                        username=server_config["user"],
                        timeout=10
                    )
                    print(f"✅ 使用密钥连接到服务器 {server_ip}")
                except:
                    # 密钥认证失败，使用密码认证
                    ssh.connect(
                        server_ip,
                        username=server_config["user"],
                        password=server_config["password"],
                        timeout=10
                    )
                    print(f"✅ 使用密码连接到服务器 {server_ip}")

                self.connections[server_ip] = ssh
            except Exception as e:
                print(f"❌ 连接服务器 {server_ip} 失败: {e}")
                return None

        return self.connections.get(server_ip)
    
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
            return items
        except Exception as e:
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

        return items

def start_rsync_transfer(transfer_id, source_server, source_files, target_server, target_path, mode="copy", fast_ssh=True):
    """启动rsync传输任务"""
    def transfer_worker():
        try:
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

                    ssh_cmd = "ssh -o StrictHostKeyChecking=no -o PasswordAuthentication=yes"
                    if fast_ssh:
                        ssh_cmd += " -o Compression=no"

                    # 构建rsync命令，使用sshpass进行密码认证
                    if is_directory:
                        if target_password:
                            remote_cmd = f"sshpass -p '{target_password}' rsync -avz --progress --inplace --whole-file -e '{ssh_cmd}' '{source_path}/' '{target_user}@{target_server}:{target_path}/{file_name}/'"
                        else:
                            remote_cmd = f"rsync -avz --progress --inplace --whole-file -e '{ssh_cmd}' '{source_path}/' '{target_user}@{target_server}:{target_path}/{file_name}/'"
                    else:
                        if target_password:
                            remote_cmd = f"sshpass -p '{target_password}' rsync -avz --progress --inplace --whole-file -e '{ssh_cmd}' '{source_path}' '{target_user}@{target_server}:{target_path}/'"
                        else:
                            remote_cmd = f"rsync -avz --progress --inplace --whole-file -e '{ssh_cmd}' '{source_path}' '{target_user}@{target_server}:{target_path}/'"

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
                            print(f"传输 {transfer_id} 已被取消，终止SSH命令")
                            try:
                                stdout.channel.close()
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
                'message': f'成功传输 {total_files} 个文件/文件夹'
            })
            
        except Exception as e:
            socketio.emit('transfer_complete', {
                'transfer_id': transfer_id,
                'status': 'error',
                'message': str(e)
            })
        finally:
            # 清理活动传输记录和进程记录
            if transfer_id in active_transfers:
                del active_transfers[transfer_id]
            if transfer_id in transfer_processes:
                del transfer_processes[transfer_id]
    
    # 启动传输线程
    thread = threading.Thread(target=transfer_worker)
    thread.daemon = True
    thread.start()

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

    try:
        files = get_directory_listing(server_ip, path, show_hidden)
        return jsonify({
            'success': True,
            'path': path,
            'files': files,
            'show_hidden': show_hidden
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@socketio.on('start_transfer')
def handle_start_transfer(data):
    transfer_id = f"transfer_{int(time.time())}"

    # 记录传输任务
    active_transfers[transfer_id] = {
        'source_server': data['source_server'],
        'source_files': data['source_files'],
        'target_server': data['target_server'],
        'target_path': data['target_path'],
        'mode': data.get('mode', 'copy'),
        'start_time': datetime.now()
    }

    # 启动传输
    start_rsync_transfer(
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

    if not transfer_id:
        emit('transfer_cancelled', {'status': 'error', 'message': '无效的传输ID'})
        return

    if transfer_id not in active_transfers:
        emit('transfer_cancelled', {'status': 'error', 'message': '传输任务不存在或已完成'})
        return

    print(f"收到取消传输请求: {transfer_id}")

    # 终止相关进程
    if transfer_id in transfer_processes:
        process_info = transfer_processes[transfer_id]
        try:
            if process_info['type'] == 'subprocess':
                # 终止subprocess进程
                process = process_info['process']
                process.terminate()
                try:
                    process.wait(timeout=3)
                except:
                    process.kill()
                print(f"已终止subprocess进程: {transfer_id}")
            elif process_info['type'] == 'ssh':
                # 关闭SSH通道
                channel = process_info['channel']
                channel.close()
                print(f"已关闭SSH通道: {transfer_id}")
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

        # 构建SSH选项（与原始脚本相同）
        ssh_opts = "ssh -o StrictHostKeyChecking=no -o PasswordAuthentication=yes"
        if fast_ssh:
            ssh_opts += " -o Compression=no"

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
        if target_password:
            # 使用密码认证
            cmd = [
                'sshpass', '-p', target_password,
                'rsync', '-a', '--inplace', '--whole-file', '--info=progress2',
                '-e', ssh_opts,
                source_with_slash,
                f"{target_user}@{target_server}:{target_full_path}"
            ]
        else:
            # 使用SSH密钥认证（最快）
            cmd = [
                'rsync', '-a', '--inplace', '--whole-file', '--info=progress2',
                '-e', ssh_opts,
                source_with_slash,
                f"{target_user}@{target_server}:{target_full_path}"
            ]



        # 使用subprocess执行本地命令，实时获取输出
        import subprocess

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
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
                print(f"传输 {transfer_id} 已被取消，终止进程")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except:
                    try:
                        process.kill()
                    except:
                        pass
                return

            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
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
