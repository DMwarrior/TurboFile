# ⚡ TurboFile - 极速传文件传输系统 / Ultra-fast File Transfer

面向局域网内 Linux/Windows 设备的极速文件传输系统，传输速度可接近打满带宽（千兆网络常见 115MB/s+），相比 WinSCP/MobaXterm/Xshell 等工具在同网段内更快更稳定。
An ultra-fast LAN file transfer system for Linux/Windows devices that can saturate bandwidth (e.g., 115MB/s+ on Gigabit networks), delivering faster and more stable transfers than tools like WinSCP/MobaXterm/Xshell in the same subnet.

## 🚀 主要特性 / Features

- **多服务器传输**：支持多台服务器任意方向传输  
  **Multi-server transfer**: copy/move across multiple servers in any direction
  **Real-time progress**: speed, transferred bytes, ETA
- **复制/剪切模式**：支持复制与移动  
  **Copy/Move modes**
- **图片预览**：支持直接预览 Linux 端图片  
  **Image preview**: preview images stored on Linux servers directly
- **SSH 连接优化**：连接池与稳定性提升  
  **Optimized SSH connections**
- **现代化 UI**：响应式布局、快捷操作  
  **Modern responsive UI**

## 📋 系统要求 / Requirements

- **OS**: Linux (Ubuntu/Debian/CentOS)
- **Python**: 3.7+
- **Network**: SSH reachable between servers
- **System deps**: `rsync`, `sshpass` (optional)

## 🛠️ 安装部署 / Installation

### 1) 克隆项目 / Clone
```bash
git clone <your-repo-url>
cd TurboFile
```

### 2) 安装依赖 / Install dependencies
```bash
pip install -r requirements.txt

# system packages
sudo apt update
sudo apt install rsync sshpass
sudo apt install -y imagemagick
```

### Windows 端 SSH + rsync 配置 / Windows SSH + rsync setup
在 Windows 服务器上以管理员身份打开 PowerShell 执行以下命令：  
Run the following commands in PowerShell as Administrator:
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow

Set-ExecutionPolicy Bypass -Scope Process -Force
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
iwr https://community.chocolatey.org/install.ps1 -UseBasicParsing | iex
choco upgrade -y chocolatey
choco install -y rsync
```

### 3) 配置 / Configuration
复制示例配置并填写真实信息：
Copy the example config and fill your real values:
```bash
cp data/config.example.json data/config.json
```

> 注意：`data/config.json` 包含敏感信息，**不要提交到公开仓库**。  
> Note: `data/config.json` contains secrets. **Do not commit it to public repos**.

#### `data/config.json` 说明 / `data/config.json` notes

- **host_ip**: Web 服务对外访问 IP（显示用）
- **admin_mode_enabled** / **admin_client_ips**: 管理员模式开关与白名单 IP
- **transfer_bytes_config**: “已传输”显示开关与刷新间隔
- **servers**: 服务器列表（Linux/Windows），键是内部唯一 ID；配置项包含 `name/host/user/password/default_path/os_type`
- **visible_client_ips**: 可选，仅允许指定客户端 IP 看见该服务器；不配置则所有客户端可见

> 如果同一台机器需要配置多个用户，请给它们不同的服务器键，并在每个条目里写同一个 `host`。例如：
> `nas_algorithm -> host: 10.190.21.253`
> `zyxk -> host: 10.190.21.253`

> Windows 服务器需设置 `os_type: "windows"`，路径使用 `C:/` 形式。
> 如需让某台服务器只对特定终端显示，可在该服务器配置下加入 `visible_client_ips: ["10.190.129.29"]`。

### 4) 启动 / Run
开发模式：
```bash
python app.py
```

使用 systemd 服务管理：
```bash
./turbofile_manager.sh start
./turbofile_manager.sh status
./turbofile_manager.sh restart
```

### systemd 服务说明 / systemd service

如果你要用 systemd 管理服务，需安装 `turbofile.service`：  
To manage the service with systemd, install `turbofile.service`:
```bash
sudo cp turbofile.service /etc/systemd/system/turbofile.service
sudo systemctl daemon-reload
sudo systemctl enable --now turbofile
```

如路径不同，请修改 `turbofile.service` 中的 `WorkingDirectory` 与 `ExecStart`。  
Adjust `WorkingDirectory` and `ExecStart` if your paths are different.

## 🎯 使用说明 / Usage

1. 选择源服务器与目标服务器
2. 浏览目录并选择文件/文件夹
3. 选择复制或剪切
4. 点击开始传输并监控进度

Open the web UI, choose source/target, select files, then start transfer and monitor progress.

## 🧾 日志 / Logs

- 运行日志: `transfer.log`
- systemd: `journalctl -u turbofile -f`

## 🔒 安全建议 / Security

- 建议仅在内网使用
- 使用 SSH 密钥认证
- 不要公开提交 `data/config.json` 与 `data/client_paths.json`

## 🤝 贡献 / Contributing

欢迎提交 Issue / PR。建议先开分支并提供清晰的复现步骤。
Issues and PRs are welcome. Please include reproducible steps.

## 📄 许可证 / License

本项目使用 MIT 许可证，详见 [LICENSE](LICENSE)。  
This project is licensed under the MIT License.
