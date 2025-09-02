#!/bin/bash

# TurboFile 系统服务安装脚本
# 用于将极速传文件传输系统设置为系统服务，支持开机自启动

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_message() {
    echo -e "${2}${1}${NC}"
}

print_message "🚀 TurboFile 系统服务安装程序" $BLUE
print_message "================================" $BLUE

# 检查是否以root权限运行
if [ "$EUID" -ne 0 ]; then
    print_message "❌ 请使用sudo权限运行此脚本" $RED
    print_message "   sudo bash install_service.sh" $YELLOW
    exit 1
fi

# 获取当前目录
CURRENT_DIR=$(pwd)
SERVICE_FILE="turbofile.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_FILE"

print_message "📁 当前工作目录: $CURRENT_DIR" $BLUE

# 检查服务文件是否存在
if [ ! -f "$SERVICE_FILE" ]; then
    print_message "❌ 找不到服务配置文件: $SERVICE_FILE" $RED
    print_message "   请确保在正确的目录下运行此脚本" $YELLOW
    exit 1
fi

# 停止现有服务（如果存在）
if systemctl is-active --quiet turbofile; then
    print_message "⏹️  停止现有的TurboFile服务..." $YELLOW
    systemctl stop turbofile
fi

# 复制服务文件
print_message "📋 复制服务配置文件到系统目录..." $BLUE
cp "$SERVICE_FILE" "$SERVICE_PATH"

# 重新加载systemd配置
print_message "🔄 重新加载systemd配置..." $BLUE
systemctl daemon-reload

# 启用服务（开机自启动）
print_message "✅ 启用TurboFile服务（开机自启动）..." $GREEN
systemctl enable turbofile

# 启动服务
print_message "🚀 启动TurboFile服务..." $GREEN
systemctl start turbofile

# 等待服务启动
sleep 3

# 检查服务状态
if systemctl is-active --quiet turbofile; then
    print_message "✅ TurboFile服务安装成功！" $GREEN
    print_message "" 
    print_message "📊 服务状态信息:" $BLUE
    systemctl status turbofile --no-pager -l
    print_message ""
    print_message "🌐 访问地址: http://192.168.9.62:5000" $GREEN
    print_message ""
    print_message "📝 常用命令:" $BLUE
    print_message "   查看状态: sudo systemctl status turbofile" $YELLOW
    print_message "   停止服务: sudo systemctl stop turbofile" $YELLOW
    print_message "   启动服务: sudo systemctl start turbofile" $YELLOW
    print_message "   重启服务: sudo systemctl restart turbofile" $YELLOW
    print_message "   查看日志: sudo journalctl -u turbofile -f" $YELLOW
    print_message "   禁用自启: sudo systemctl disable turbofile" $YELLOW
else
    print_message "❌ TurboFile服务启动失败！" $RED
    print_message "📋 查看详细错误信息:" $YELLOW
    systemctl status turbofile --no-pager -l
    print_message ""
    print_message "🔍 查看日志: sudo journalctl -u turbofile -n 50" $YELLOW
    exit 1
fi

print_message ""
print_message "🎉 安装完成！TurboFile现在将在系统启动时自动运行。" $GREEN
