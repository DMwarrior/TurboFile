#!/bin/bash
# TurboFile 服务管理脚本
# 用于方便地管理TurboFile系统服务

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SERVICE_NAME="turbofile"
SERVICE_URL="http://192.168.9.62:5000"

show_status() {
    echo -e "${BLUE}📊 TurboFile服务状态${NC}"
    echo "=" * 40
    
    # 检查服务状态
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "服务状态: ${GREEN}✅ 运行中${NC}"
    else
        echo -e "服务状态: ${RED}❌ 已停止${NC}"
    fi
    
    # 检查开机自启动
    if systemctl is-enabled --quiet $SERVICE_NAME; then
        echo -e "开机自启: ${GREEN}✅ 已启用${NC}"
    else
        echo -e "开机自启: ${RED}❌ 未启用${NC}"
    fi
    
    # 检查端口
    if ss -tlnp | grep -q ":5000"; then
        echo -e "端口5000: ${GREEN}✅ 正在监听${NC}"
    else
        echo -e "端口5000: ${RED}❌ 未监听${NC}"
    fi
    
    # 检查Web访问
    if curl -s -f $SERVICE_URL > /dev/null; then
        echo -e "Web访问: ${GREEN}✅ 正常${NC}"
    else
        echo -e "Web访问: ${RED}❌ 无法访问${NC}"
    fi
    
    echo -e "访问地址: ${BLUE}$SERVICE_URL${NC}"
}

start_service() {
    echo -e "${YELLOW}🚀 启动TurboFile服务...${NC}"
    sudo systemctl start $SERVICE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 服务启动成功${NC}"
        sleep 2
        show_status
    else
        echo -e "${RED}❌ 服务启动失败${NC}"
        sudo systemctl status $SERVICE_NAME
    fi
}

stop_service() {
    echo -e "${YELLOW}🛑 停止TurboFile服务...${NC}"
    sudo systemctl stop $SERVICE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 服务停止成功${NC}"
    else
        echo -e "${RED}❌ 服务停止失败${NC}"
    fi
}

restart_service() {
    echo -e "${YELLOW}🔄 重启TurboFile服务...${NC}"
    sudo systemctl restart $SERVICE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 服务重启成功${NC}"
        sleep 2
        show_status
    else
        echo -e "${RED}❌ 服务重启失败${NC}"
        sudo systemctl status $SERVICE_NAME
    fi
}

enable_autostart() {
    echo -e "${YELLOW}⚙️  启用开机自启动...${NC}"
    sudo systemctl enable $SERVICE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 开机自启动已启用${NC}"
    else
        echo -e "${RED}❌ 开机自启动启用失败${NC}"
    fi
}

disable_autostart() {
    echo -e "${YELLOW}⚙️  禁用开机自启动...${NC}"
    sudo systemctl disable $SERVICE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 开机自启动已禁用${NC}"
    else
        echo -e "${RED}❌ 开机自启动禁用失败${NC}"
    fi
}

show_logs() {
    echo -e "${BLUE}📋 TurboFile服务日志 (最近20条)${NC}"
    echo "=" * 40
    sudo journalctl -u $SERVICE_NAME -n 20 --no-pager
}

follow_logs() {
    echo -e "${BLUE}📋 实时查看TurboFile服务日志 (按Ctrl+C退出)${NC}"
    echo "=" * 40
    sudo journalctl -u $SERVICE_NAME -f
}

open_web() {
    echo -e "${BLUE}🌐 打开TurboFile Web界面...${NC}"
    
    if command -v xdg-open &> /dev/null; then
        xdg-open $SERVICE_URL &
    elif command -v open &> /dev/null; then
        open $SERVICE_URL &
    else
        echo -e "${YELLOW}请手动打开浏览器访问: $SERVICE_URL${NC}"
    fi
}

show_help() {
    echo -e "${BLUE}🔧 TurboFile服务管理脚本${NC}"
    echo "=" * 40
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  status      显示服务状态"
    echo "  start       启动服务"
    echo "  stop        停止服务"
    echo "  restart     重启服务"
    echo "  enable      启用开机自启动"
    echo "  disable     禁用开机自启动"
    echo "  logs        查看服务日志"
    echo "  follow      实时查看日志"
    echo "  web         打开Web界面"
    echo "  help        显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 status   # 查看服务状态"
    echo "  $0 restart # 重启服务"
    echo "  $0 logs    # 查看日志"
}

# 主逻辑
case "$1" in
    status)
        show_status
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    enable)
        enable_autostart
        ;;
    disable)
        disable_autostart
        ;;
    logs)
        show_logs
        ;;
    follow)
        follow_logs
        ;;
    web)
        open_web
        ;;
    help|--help|-h)
        show_help
        ;;
    "")
        show_status
        ;;
    *)
        echo -e "${RED}❌ 未知选项: $1${NC}"
        echo ""
        show_help
        exit 1
        ;;
esac
