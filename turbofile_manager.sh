#!/bin/bash
# TurboFile service management script.
# Provides convenience commands to manage the TurboFile systemd service.

# Color definitions.
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SERVICE_NAME="turbofile"
SERVICE_PORT="5000"
SERVICE_URL_LOCAL="http://127.0.0.1:${SERVICE_PORT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_public_service_url() {
    # Resolve a URL that is reachable by other machines in the LAN.
    # Falls back to the previous hard-coded IP when config is missing.

    # Fall back to config.json host_ip when available.
    if [ -f "${SCRIPT_DIR}/data/config.json" ]; then
        host_ip=$(python3 -c "import json;print(json.load(open('${SCRIPT_DIR}/data/config.json','r',encoding='utf-8')).get('host_ip',''))" 2>/dev/null)
        if [ -n "$host_ip" ]; then
            echo "http://${host_ip}:${SERVICE_PORT}"
            return 0
        fi
    fi

    # Final fallback (keep previous default).
    echo "http://192.168.9.64:${SERVICE_PORT}"
}

SERVICE_URL_PUBLIC="$(resolve_public_service_url)"
SERVICE_URL="${SERVICE_URL_PUBLIC}"

show_status() {
    echo -e "${BLUE}📊 TurboFile服务状态${NC}"
    echo "=" * 40
    
    # Check service status.
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "服务状态: ${GREEN}✅ 运行中${NC}"
    else
        echo -e "服务状态: ${RED}❌ 已停止${NC}"
    fi
    
    # Check systemd autostart status.
    if systemctl is-enabled --quiet $SERVICE_NAME; then
        echo -e "开机自启: ${GREEN}✅ 已启用${NC}"
    else
        echo -e "开机自启: ${RED}❌ 未启用${NC}"
    fi
    
    # Check port status.
    if ss -tlnp | grep -q ":5000"; then
        echo -e "端口5000: ${GREEN}✅ 正在监听${NC}"
    else
        echo -e "端口5000: ${RED}❌ 未监听${NC}"
    fi
    
    # Check web access.
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
        echo -e "${GREEN}✅ 服务启动命令已执行${NC}"
        echo -e "${YELLOW}⏳ 等待服务完全启动...${NC}"

        # Wait up to 10 seconds and verify the service is active.
        for i in {1..10}; do
            sleep 1
            if systemctl is-active --quiet $SERVICE_NAME; then
                echo -e "${GREEN}✅ 服务已成功启动 (耗时 ${i}秒)${NC}"
                sleep 1  # 再等1秒确保端口监听
                show_status
                return 0
            fi
        done

        echo -e "${RED}❌ 服务启动超时${NC}"
        sudo systemctl status $SERVICE_NAME
    else
        echo -e "${RED}❌ 服务启动失败${NC}"
        sudo systemctl status $SERVICE_NAME
    fi
}

check_active_transfers() {
    # Check whether there are active transfers.
    # Return 0 when none, 1 when active transfers exist, 2 when unable to determine.

    # Prefer loopback; fall back to configured URL.
    api_base="${SERVICE_URL_LOCAL}"
    if ! curl -s -f "${api_base}/" > /dev/null 2>&1; then
        api_base="${SERVICE_URL_PUBLIC}"
    fi

    if ! curl -s -f "${api_base}/" > /dev/null 2>&1; then
        # Service is not running; nothing to check.
        return 0
    fi

    # Query active transfers via API.
    response=$(curl -s "${api_base}/api/active_transfers" 2>/dev/null)
    if [ -z "$response" ]; then
        echo -e "${YELLOW}⚠️  无法获取活跃传输信息（接口无响应），将按“未知状态”处理。${NC}"
        return 2
    fi

    # Parse JSON response using python (robust to whitespace/pretty-print).
    active_count=$(echo "$response" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('active_count',''))" 2>/dev/null)
    success_flag=$(echo "$response" | python3 -c "import sys, json; d=json.load(sys.stdin); print('1' if d.get('success') else '0')" 2>/dev/null)

    if [ "$success_flag" != "1" ]; then
        echo -e "${YELLOW}⚠️  活跃传输接口返回失败，无法确认是否有传输任务。${NC}"
        return 2
    fi

    if [ -z "$active_count" ] || [ "$active_count" -eq 0 ] 2>/dev/null; then
        return 0
    fi

    # Active transfers detected; show details.
    echo -e "${YELLOW}⚠️  检测到 ${active_count} 个正在进行的传输任务！${NC}"
    echo ""
    echo -e "${BLUE}活跃传输列表：${NC}"
    echo "----------------------------------------"

    # Extract and print transfer details.
    echo "$response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('success') and data.get('transfers'):
        for i, t in enumerate(data['transfers'], 1):
            print(f\"{i}. 客户端IP: {t.get('client_ip', '未知')}\")
            print(f\"   源服务器: {t.get('source_server', '未知')}\")
            print(f\"   目标服务器: {t.get('target_server', '未知')}\")
            print(f\"   文件数量: {t.get('file_count', 0)}\")
            print(f\"   开始时间: {t.get('start_time', '未知')}\")
            print(f\"   已用时间: {t.get('elapsed_time', '未知')}\")
            print(f\"   传输模式: {t.get('mode', 'copy')}\")
            print()
except:
    pass
" 2>/dev/null

    echo "----------------------------------------"
    return 1
}

stop_service() {
    echo -e "${YELLOW}🛑 停止TurboFile服务...${NC}"

    # Check active transfers.
    check_active_transfers
    status=$?
    if [ $status -ne 0 ]; then
        echo ""
        if [ $status -eq 1 ]; then
            echo -e "${RED}❌ 检测到活跃传输任务，停止服务可能会中断这些传输！${NC}"
        else
            echo -e "${RED}❌ 无法确认是否存在传输任务（检测失败/未知状态），停止服务可能会中断传输！${NC}"
        fi
        read -p "是否确认停止服务？(yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            echo -e "${YELLOW}⏸️  已取消停止操作${NC}"
            return 1
        fi
    fi

    sudo systemctl stop $SERVICE_NAME

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ systemd服务已停止${NC}"

        # Clean up possible leftover processes.
        echo -e "${YELLOW}🧹 清理残留进程...${NC}"
        pids=$(ps aux | grep "python.*app.py" | grep -v grep | awk '{print $2}')
        if [ -n "$pids" ]; then
            echo -e "${YELLOW}发现残留进程: $pids${NC}"
            echo "$pids" | xargs -r kill -9 2>/dev/null
            sleep 1
            echo -e "${GREEN}✅ 残留进程已清理${NC}"
        else
            echo -e "${GREEN}✅ 无残留进程${NC}"
        fi
    else
        echo -e "${RED}❌ 服务停止失败${NC}"
    fi
}

restart_service() {
    echo -e "${YELLOW}🔄 重启TurboFile服务...${NC}"

    # Check active transfers.
    check_active_transfers
    status=$?
    if [ $status -ne 0 ]; then
        echo ""
        if [ $status -eq 1 ]; then
            echo -e "${RED}❌ 检测到活跃传输任务，重启服务会中断这些传输！${NC}"
        else
            echo -e "${RED}❌ 无法确认是否存在传输任务（检测失败/未知状态），重启服务可能会中断传输！${NC}"
        fi
        read -p "是否确认重启服务？(yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            echo -e "${YELLOW}⏸️  已取消重启操作${NC}"
            return 1
        fi
    fi

    # Stop the systemd service first.
    echo -e "${YELLOW}🛑 停止systemd服务...${NC}"
    sudo systemctl stop $SERVICE_NAME
    sleep 1

    # Clean up leftover Python processes (port 5000).
    echo -e "${YELLOW}🧹 清理残留进程...${NC}"
    pids=$(ps aux | grep "python.*app.py" | grep -v grep | awk '{print $2}')
    if [ -n "$pids" ]; then
        echo -e "${YELLOW}发现残留进程: $pids${NC}"
        echo "$pids" | xargs -r kill -9 2>/dev/null
        sleep 1
    fi

    # Verify the port is released.
    if ss -tlnp | grep -q ":5000"; then
        echo -e "${RED}⚠️  端口5000仍被占用，尝试强制释放...${NC}"
        port_pid=$(ss -tlnp | grep ":5000" | grep -oP 'pid=\K[0-9]+' | head -1)
        if [ -n "$port_pid" ]; then
            kill -9 $port_pid 2>/dev/null
            sleep 1
        fi
    fi

    # Start the service.
    echo -e "${YELLOW}🚀 启动服务...${NC}"
    sudo systemctl start $SERVICE_NAME

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 服务启动命令已执行${NC}"
        echo -e "${YELLOW}⏳ 等待服务完全启动...${NC}"

        # Wait up to 10 seconds and verify the service is active.
        for i in {1..10}; do
            sleep 1
            if systemctl is-active --quiet $SERVICE_NAME; then
                echo -e "${GREEN}✅ 服务已成功重启 (耗时 ${i}秒)${NC}"
                sleep 1  # 再等1秒确保端口监听
                show_status
                return 0
            fi
        done

        echo -e "${RED}❌ 服务重启超时${NC}"
        echo -e "${YELLOW}📋 查看最近日志：${NC}"
        sudo journalctl -u $SERVICE_NAME -n 20 --no-pager
    else
        echo -e "${RED}❌ 服务启动失败${NC}"
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

show_active_transfers() {
    echo -e "${BLUE}📊 检查活跃传输任务${NC}"
    echo "=" * 40

    check_active_transfers
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 当前没有活跃的传输任务${NC}"
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
    echo "  transfers   查看活跃传输任务"
    echo "  web         打开Web界面"
    echo "  help        显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 status    # 查看服务状态"
    echo "  $0 restart   # 重启服务"
    echo "  $0 transfers # 查看活跃传输"
    echo "  $0 logs      # 查看日志"
}

# Main entry.
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
    transfers)
        show_active_transfers
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
