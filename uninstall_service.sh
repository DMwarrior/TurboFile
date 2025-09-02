#!/bin/bash

# TurboFile 系统服务卸载脚本

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

print_message "🗑️  TurboFile 系统服务卸载程序" $BLUE
print_message "================================" $BLUE

# 检查是否以root权限运行
if [ "$EUID" -ne 0 ]; then
    print_message "❌ 请使用sudo权限运行此脚本" $RED
    print_message "   sudo bash uninstall_service.sh" $YELLOW
    exit 1
fi

SERVICE_PATH="/etc/systemd/system/turbofile.service"

# 检查服务是否存在
if [ ! -f "$SERVICE_PATH" ]; then
    print_message "ℹ️  TurboFile服务未安装" $YELLOW
    exit 0
fi

# 停止服务
if systemctl is-active --quiet turbofile; then
    print_message "⏹️  停止TurboFile服务..." $YELLOW
    systemctl stop turbofile
fi

# 禁用服务
if systemctl is-enabled --quiet turbofile; then
    print_message "❌ 禁用TurboFile服务自启动..." $YELLOW
    systemctl disable turbofile
fi

# 删除服务文件
print_message "🗑️  删除服务配置文件..." $BLUE
rm -f "$SERVICE_PATH"

# 重新加载systemd配置
print_message "🔄 重新加载systemd配置..." $BLUE
systemctl daemon-reload

print_message "✅ TurboFile服务已成功卸载！" $GREEN
print_message "💡 如需重新安装，请运行: sudo bash install_service.sh" $BLUE
