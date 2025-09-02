#!/bin/bash
# Web文件传输系统部署脚本

echo "🚀 开始部署Web文件传输系统..."

# 检查Python版本
python3 --version
if [ $? -ne 0 ]; then
    echo "❌ 请先安装Python3"
    exit 1
fi

# 创建虚拟环境
echo "📦 创建Python虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 安装依赖
echo "📥 安装Python依赖包..."
pip install -r requirements.txt

# 检查rsync是否安装
echo "🔧 检查rsync工具..."
rsync --version
if [ $? -ne 0 ]; then
    echo "❌ 请先安装rsync工具"
    echo "Ubuntu/Debian: sudo apt-get install rsync"
    echo "CentOS/RHEL: sudo yum install rsync"
    exit 1
fi

# 检查SSH密钥配置
echo "🔑 检查SSH密钥配置..."
if [ ! -f ~/.ssh/id_rsa ]; then
    echo "⚠️  未找到SSH私钥，请确保已配置SSH密钥认证"
    echo "生成SSH密钥: ssh-keygen -t rsa"
    echo "复制公钥到其他服务器: ssh-copy-id user@server"
fi

# 创建启动脚本
echo "📝 创建启动脚本..."
cat > start.sh << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "🌐 启动Web文件传输系统..."
echo "📱 访问地址: http://$(hostname -I | awk '{print $1}'):5000"
python app.py
EOF

chmod +x start.sh

# 创建systemd服务文件（可选）
echo "⚙️  创建系统服务文件..."
cat > web-file-transfer.service << EOF
[Unit]
Description=Web File Transfer System
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin
ExecStart=$(pwd)/venv/bin/python $(pwd)/app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

echo "✅ 部署完成！"
echo ""
echo "🚀 启动方式："
echo "1. 手动启动: ./start.sh"
echo "2. 系统服务启动:"
echo "   sudo cp web-file-transfer.service /etc/systemd/system/"
echo "   sudo systemctl enable web-file-transfer"
echo "   sudo systemctl start web-file-transfer"
echo ""
echo "📱 访问地址: http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "⚠️  注意事项："
echo "1. 确保所有服务器之间已配置SSH密钥认证"
echo "2. 确保防火墙允许5000端口访问"
echo "3. 确保所有服务器都安装了rsync工具"
