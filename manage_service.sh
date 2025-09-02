#!/bin/bash

# TurboFile 服务管理脚本
# 提供便捷的服务管理命令

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

# 显示帮助信息
show_help() {
    print_message "🚀 TurboFile 服务管理工具" $BLUE
    print_message "=========================" $BLUE
    print_message ""
    print_message "用法: $0 [命令]" $YELLOW
    print_message ""
    print_message "可用命令:" $BLUE
    print_message "  status    - 查看服务状态" $GREEN
    print_message "  start     - 启动服务" $GREEN
    print_message "  stop      - 停止服务" $GREEN
    print_message "  restart   - 重启服务" $GREEN
    print_message "  enable    - 启用开机自启动" $GREEN
    print_message "  disable   - 禁用开机自启动" $GREEN
    print_message "  logs      - 查看实时日志" $GREEN
    print_message "  install   - 安装系统服务" $GREEN
    print_message "  uninstall - 卸载系统服务" $GREEN
    print_message ""
    print_message "示例:" $YELLOW
    print_message "  $0 status     # 查看服务状态"
    print_message "  $0 logs       # 查看实时日志"
    print_message "  $0 restart    # 重启服务"
}

# 检查服务是否存在
check_service_exists() {
    if ! systemctl list-unit-files | grep -q "turbofile.service"; then
        print_message "❌ TurboFile服务未安装" $RED
        print_message "💡 请先运行: $0 install" $YELLOW
        exit 1
    fi
}

# 需要sudo权限的命令
require_sudo() {
    if [ "$EUID" -ne 0 ]; then
        print_message "❌ 此命令需要sudo权限" $RED
        print_message "   sudo $0 $1" $YELLOW
        exit 1
    fi
}

# 主逻辑
case "${1:-help}" in
    "status")
        check_service_exists
        print_message "📊 TurboFile服务状态:" $BLUE
        systemctl status turbofile --no-pager -l
        ;;
    
    "start")
        require_sudo $1
        check_service_exists
        print_message "🚀 启动TurboFile服务..." $GREEN
        systemctl start turbofile
        print_message "✅ 服务已启动" $GREEN
        ;;
    
    "stop")
        require_sudo $1
        check_service_exists
        print_message "⏹️  停止TurboFile服务..." $YELLOW
        systemctl stop turbofile
        print_message "✅ 服务已停止" $GREEN
        ;;
    
    "restart")
        require_sudo $1
        check_service_exists
        print_message "🔄 重启TurboFile服务..." $BLUE
        systemctl restart turbofile
        print_message "✅ 服务已重启" $GREEN
        ;;
    
    "enable")
        require_sudo $1
        check_service_exists
        print_message "✅ 启用TurboFile开机自启动..." $GREEN
        systemctl enable turbofile
        print_message "✅ 开机自启动已启用" $GREEN
        ;;
    
    "disable")
        require_sudo $1
        check_service_exists
        print_message "❌ 禁用TurboFile开机自启动..." $YELLOW
        systemctl disable turbofile
        print_message "✅ 开机自启动已禁用" $GREEN
        ;;
    
    "logs")
        check_service_exists
        print_message "📝 TurboFile服务日志 (按Ctrl+C退出):" $BLUE
        journalctl -u turbofile -f
        ;;
    
    "install")
        require_sudo $1
        if [ -f "install_service.sh" ]; then
            bash install_service.sh
        else
            print_message "❌ 找不到install_service.sh文件" $RED
            exit 1
        fi
        ;;
    
    "uninstall")
        require_sudo $1
        if [ -f "uninstall_service.sh" ]; then
            bash uninstall_service.sh
        else
            print_message "❌ 找不到uninstall_service.sh文件" $RED
            exit 1
        fi
        ;;
    
    "help"|*)
        show_help
        ;;
esac
