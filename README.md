# ⚡ TurboFile - 极速传文件传输系统

一个基于Web的高速文件传输系统，支持多服务器间的文件传输，具有实时进度显示、可靠的取消机制和现代化的用户界面。

## 🚀 主要特性

### 📁 文件传输功能
- **多服务器支持**: 支持4台服务器间任意方向的文件传输
- **实时进度显示**: 精确的进度条、传输速度、已传输数据量和剩余时间
- **人性化数据显示**: 智能单位转换（KB/MB/GB/TB）
- **可靠取消机制**: 带确认对话框的安全传输取消功能
- **双传输模式**: 支持复制和移动两种传输模式
- **SSH加速模式**: 优化SSH连接，提升传输速度

### 🌐 用户界面
- **现代化设计**: 渐变背景、毛玻璃效果、响应式布局
- **单屏显示**: 完全适配标准分辨率，无需滚动操作
- **实时文件浏览**: 支持目录导航和文件选择
- **拖拽支持**: 支持文件拖拽操作
- **移动端适配**: 完美的响应式设计

### 🔧 技术特性
- **SSH连接池**: 高效的SSH连接管理和自动重连
- **WebSocket实时通信**: 实时进度更新和状态同步
- **系统服务**: 支持开机自启动和自动重启
- **进程管理**: 可靠的传输进程控制和清理

## 📋 系统要求

### 服务器环境
- **操作系统**: Linux (Ubuntu/CentOS/Debian)
- **Python**: 3.7+
- **网络**: 服务器间SSH连通性

### 依赖软件
- `rsync`: 文件同步工具
- `sshpass`: SSH密码认证（可选）
- `python3-pip`: Python包管理器

## 🛠️ 安装部署

### 1. 克隆项目
```bash
git clone <repository-url>
cd TurboFile
```

### 2. 安装依赖
```bash
# 安装Python依赖
pip install flask flask-socketio paramiko

# 安装系统依赖
sudo apt update
sudo apt install rsync sshpass
```

### 3. 配置服务器信息
编辑 `app.py` 中的服务器配置：
```python
servers = {
    "192.168.9.62": {"name": "训练服务器1", "user": "th", "password": "your_password"},
    "192.168.9.61": {"name": "训练服务器2", "user": "th", "password": "your_password"},
    "192.168.9.60": {"name": "数据服务器", "user": "th", "password": "your_password"},
    "192.168.9.57": {"name": "备份服务器", "user": "thgd", "password": "your_password"}
}
```

### 4. 启动服务

#### 开发模式
```bash
python app.py
```

#### 生产模式（系统服务）
```bash
# 设置执行权限
chmod +x *.sh

# 安装系统服务
sudo bash install_service.sh

# 管理服务
./manage_service.sh status    # 查看状态
./manage_service.sh logs      # 查看日志
sudo ./manage_service.sh restart  # 重启服务
```

## 🎯 使用说明

### 访问系统
部署完成后，通过以下地址访问：
- **本地访问**: http://localhost:5000
- **局域网访问**: http://192.168.9.62:5000

### 基本操作流程
1. **访问系统**: 打开浏览器访问系统地址
2. **选择服务器**: 分别选择源服务器和目标服务器
3. **浏览文件**: 在文件浏览器中选择要传输的文件或文件夹
4. **配置传输**: 选择传输模式（复制/移动）和性能选项
5. **开始传输**: 点击"开始传输"按钮启动传输任务
6. **监控进度**: 实时查看传输进度和状态信息

### 高级功能
- **SSH加速模式**: 启用SSH连接优化，提高传输速度
- **传输取消**: 随时安全取消正在进行的传输
- **路径导航**: 点击路径导航快速跳转到上级目录
- **文件拖拽**: 支持文件拖拽选择和操作

## 📊 服务器配置

### 当前支持的服务器
| 服务器 | IP地址 | 用途 | 用户 |
|--------|--------|------|------|
| 训练服务器1 | 192.168.9.62 | 主服务器/Web服务 | th |
| 训练服务器2 | 192.168.9.61 | 训练节点 | th |
| 数据服务器 | 192.168.9.60 | 数据存储 | th |
| 备份服务器 | 192.168.9.57 | 备份存储 | thgd |

### SSH配置建议
为了获得最佳性能，建议配置SSH密钥认证：
```bash
# 生成SSH密钥
ssh-keygen -t ed25519 -C "turbofile@server"

# 复制公钥到目标服务器
ssh-copy-id user@target-server

# 测试连接
ssh user@target-server "echo 'Connection OK'"
```

### 防火墙配置
确保5000端口可以被访问：
```bash
# Ubuntu/Debian
sudo ufw allow 5000

# CentOS/RHEL
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload
```

## 故障排除

### 1. 连接问题
- 检查SSH密钥是否正确配置
- 确认目标服务器SSH服务正常运行
- 检查网络连通性

### 2. 传输失败
- 检查目标路径是否存在
- 确认有足够的磁盘空间
- 检查文件权限

### 3. 性能优化
- 启用SSH加速模式
- 确保网络带宽充足
- 检查服务器负载情况

## 日志查看

```bash
# 查看应用日志
tail -f app.log

# 查看系统服务日志
sudo journalctl -u web-file-transfer -f
```

## 技术栈

- **后端**: Flask + Flask-SocketIO
- **前端**: Bootstrap 5 + JavaScript
- **传输**: rsync + SSH
- **实时通信**: WebSocket
- **SSH连接**: paramiko

## 🔧 管理命令

### 服务管理
```bash
# 查看服务状态
./manage_service.sh status

# 启动/停止/重启服务
sudo ./manage_service.sh start
sudo ./manage_service.sh stop
sudo ./manage_service.sh restart

# 查看实时日志
./manage_service.sh logs

# 安装/卸载服务
sudo ./manage_service.sh install
sudo ./manage_service.sh uninstall
```

## 🎨 界面特性

### 设计亮点
- **⚡ 闪电主题**: 体现"极速传"的快速传输特色
- **🎨 现代化UI**: 渐变背景、毛玻璃效果、圆角设计
- **📱 响应式布局**: 完美适配桌面和移动设备
- **🎯 单屏设计**: 所有功能在一屏内完整显示

### 用户体验
- **零学习成本**: 直观的操作界面，无需培训
- **实时反馈**: 所有操作都有即时的视觉反馈
- **错误预防**: 确认对话框防止误操作
- **状态清晰**: 明确的状态指示和进度信息

## 📝 更新日志

### v1.0.0 (当前版本)
- ✅ 完整的文件传输功能
- ✅ 现代化用户界面
- ✅ 系统服务支持
- ✅ 实时进度显示
- ✅ 可靠的取消机制
- ✅ 响应式设计
- ✅ SSH连接池管理

## 🔒 安全注意事项

1. 建议在内网环境使用
2. 定期更新SSH密钥
3. 监控传输日志
4. 限制用户访问权限
5. 使用强密码或密钥认证
6. 定期备份重要数据

## 🤝 贡献指南

欢迎提交Issue和Pull Request来改进项目！

### 开发环境设置
1. Fork项目到你的GitHub账户
2. 克隆你的Fork到本地
3. 创建新的功能分支
4. 进行开发和测试
5. 提交Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

感谢所有为这个项目做出贡献的开发者和用户！

---

**TurboFile** - 让文件传输变得简单高效！ ⚡
