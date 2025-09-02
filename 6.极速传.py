import os
import argparse
import socket
import subprocess

def is_localhost(ip):
    return ip == "localhost" or ip == socket.gethostbyname(socket.gethostname())

def rsync_local(source_folder, target_folder):
    print(f"[本地] 正在使用 rsync 从 {source_folder} 传输到 {target_folder} ...")
    subprocess.run([
        "rsync", "-a", "--inplace", "--whole-file", "--info=progress2",
        f"{source_folder}/", target_folder
    ], check=True)

def rsync_remote(source_folder, target_folder, target_ip, user=None, fast_ssh=False):
    dest = f"{target_ip}:{target_folder}" if not user else f"{user}@{target_ip}:{target_folder}"
    print(f"[远程] 正在使用 rsync 从 {source_folder} 传输到 {dest} ...")

    ssh_cmd = "ssh"
    if fast_ssh:
        print("⚡️ SSH 加速模式已启用（由 ssh 自动选择最快加密）")
        # 不指定 cipher，让 ssh 自行选择最快可用加密算法（通常为 aes128-ctr）
        ssh_cmd += " -o Compression=no"

    subprocess.run([
        "rsync", "-a", "--inplace", "--whole-file", "--info=progress2",
        "-e", ssh_cmd, f"{source_folder}/", dest
    ], check=True)

def delete_local(source_folder):
    print(f"删除本地源文件夹：{source_folder}")
    subprocess.run(["rm", "-rf", source_folder], check=True)

def main():
    parser = argparse.ArgumentParser(description="使用 rsync 快速复制或移动文件夹，支持本地和远程服务器")
    parser.add_argument("--root_folder", default="/home/th/Work/fankun/data/", help="源根目录")
    parser.add_argument("--source_subfolders", nargs='*', default=["HuangDou"], help="要处理的子文件夹列表")
    parser.add_argument("--target", default="/home/th/work/fankun/test", help="目标路径")
    parser.add_argument("--ip", default="192.168.9.61", help="目标服务器IP(本地传输使用:localhost)")
    parser.add_argument("--user", default="th", help="远程服务器用户名")
    parser.add_argument("--mode", choices=["copy", "move"], default="copy", help="操作模式")
    parser.add_argument("--fast_ssh", action="store_true", default=True, help="启用 SSH 加速（禁用压缩）")

    args = parser.parse_args()
    is_local = is_localhost(args.ip)

    for subfolder in args.source_subfolders:
        source_path = os.path.join(args.root_folder, subfolder)
        target_path = os.path.join(args.target, subfolder)

        if not os.path.exists(source_path):
            print(f"❌ 源文件夹不存在：{source_path}")
            continue

        try:
            if is_local:
                rsync_local(source_path, target_path)
            else:
                rsync_remote(source_path, target_path, args.ip, user=args.user or None, fast_ssh=args.fast_ssh)

            if args.mode == "move":
                delete_local(source_path)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ 传输失败：{source_path} -> {target_path}")
            print(f"错误信息：{e}")

if __name__ == "__main__":
    main()
