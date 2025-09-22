import os
import argparse
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

def is_localhost(ip):
    try:
        return ip == "localhost" or socket.gethostbyname(socket.gethostname()) == ip
    except Exception:
        return False

def nc_send_pigz(source_folder, target_ip, target_folder, user=None):
    """
    使用 tar + pigz + ssh 发送目录到远程服务器，加速传输
    """
    dest_path = target_folder
    ssh_prefix = []
    if user:
        ssh_prefix = ["ssh", f"{user}@{target_ip}"]

    # 创建远程目录
    if ssh_prefix:
        subprocess.run(ssh_prefix + ["mkdir", "-p", dest_path], check=True)
    else:
        os.makedirs(dest_path, exist_ok=True)

    # 使用 tar + pigz 发送
    if ssh_prefix:
        cmd = (
            f"tar -cf - -C {os.path.dirname(source_folder)} {os.path.basename(source_folder)} "
            f"| pigz -1 -p {os.cpu_count()} "
            f"| ssh -c aes128-ctr -o Compression=no {user}@{target_ip} "
            f"'pigz -d -p {os.cpu_count()} | tar -xf - -C {dest_path}'"
        )
        print(f"[远程] 执行：{cmd}")
        subprocess.run(cmd, shell=True, check=True)
    else:
        cmd = (
            f"tar -cf - -C {os.path.dirname(source_folder)} {os.path.basename(source_folder)} "
            f"| pigz -1 -p {os.cpu_count()} "
            f"| pigz -d -p {os.cpu_count()} | tar -xf - -C {dest_path}"
        )
        print(f"[本地] 执行：{cmd}")
        subprocess.run(cmd, shell=True, check=True)

def delete_local(source_folder):
    print(f"删除本地源文件夹：{source_folder}")
    subprocess.run(["rm", "-rf", source_folder], check=True)

def size_of_folder(folder):
    return sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(folder)
        for f in files
    )

def transfer_subfolder(source_path, target_path, is_local, user, mode):
    size_bytes = size_of_folder(source_path)
    size_mb = size_bytes / (1024 * 1024)

    start_time = time.time()
    try:
        if is_local:
            # 本地复制
            os.makedirs(target_path, exist_ok=True)
            cmd = f"tar -cf - -C {os.path.dirname(source_path)} {os.path.basename(source_path)} | tar -xf - -C {target_path}"
            subprocess.run(cmd, shell=True, check=True)
        else:
            nc_send_pigz(source_path, args.ip, target_path, user=user)

        if mode == "move":
            delete_local(source_path)

    except subprocess.CalledProcessError as e:
        print(f"⚠️ 传输失败：{source_path} -> {target_path}")
        print(f"错误信息：{e}")
        return

    elapsed = time.time() - start_time
    speed = size_mb / elapsed if elapsed > 0 else 0
    print(f"✅ 完成 {os.path.basename(source_path)}，耗时 {elapsed:.2f} 秒，平均速度 {speed:.2f} MB/s\n")

def main():
    global args
    start_time_total = time.time()

    parser = argparse.ArgumentParser(description="使用 tar+pigz+ssh 快速复制或移动文件夹（局域网）")
    parser.add_argument("--root_folder", default="/home/th/Work/fankun/test", help="源根目录")
    parser.add_argument("--source_subfolders", nargs='*', default=["1024x40"], help="要处理的子文件夹列表")
    parser.add_argument("--target", default="/home/th/Project_ssd/fankun/test", help="目标路径")
    parser.add_argument("--ip", default="192.168.9.60", help="目标服务器IP(本地传输使用:localhost)")
    parser.add_argument("--user", default="th", help="远程服务器用户名")
    parser.add_argument("--mode", choices=["copy", "move"], default="copy", help="操作模式")
    parser.add_argument("--parallel", type=int, default=2, help="并行传输子目录数量")
    args = parser.parse_args()

    is_local = is_localhost(args.ip)

    tasks = []
    for subfolder in args.source_subfolders:
        source_path = os.path.join(args.root_folder, subfolder)
        target_path = os.path.join(args.target, subfolder)

        if not os.path.exists(source_path):
            print(f"❌ 源文件夹不存在：{source_path}")
            continue

        tasks.append((source_path, target_path))

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = [executor.submit(transfer_subfolder, src, tgt, is_local, args.user, args.mode) for src, tgt in tasks]
        for f in futures:
            f.result()  # 等待所有完成

    end_time_total = time.time()
    print("🏁 所有任务完成，总耗时：{:.2f} 秒".format(end_time_total - start_time_total))

if __name__ == "__main__":
    main()
