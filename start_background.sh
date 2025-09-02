#!/bin/bash

# TurboFile 后台启动脚本（使用screen）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="turbofile"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_message() {
    echo -e "${2}${1}${NC}"
}

# 检查screen是否安装
if ! command -v screen &> /dev/null; then
    print_message "❌ screen未安装，正在安装..." $YELLOW
    sudo apt update && sudo apt install -y screen
fi

# 检查是否已经在运行
if screen -list | grep -q "$SESSION_NAME"; then
    print_message "⚠️  TurboFile已在后台运行" $YELLOW
    print_message "📋 查看运行状态: screen -r $SESSION_NAME" $BLUE
    print_message "🛑 停止服务: screen -S $SESSION_NAME -X quit" $BLUE
    exit 1
fi

# 激活conda环境并启动服务
print_message "🚀 启动TurboFile后台服务..." $GREEN
cd "$SCRIPT_DIR"

# 创建screen会话并运行应用
screen -dmS "$SESSION_NAME" bash -c "
    source /home/th/miniconda3/etc/profile.d/conda.sh
    conda activate torch2.4
    python app.py --production
"

sleep 2

# 检查是否启动成功
if screen -list | grep -q "$SESSION_NAME"; then
    print_message "✅ TurboFile已成功启动在后台" $GREEN
    print_message "🌐 访问地址: http://192.168.9.62:5000" $BLUE
    print_message ""
    print_message "📋 管理命令:" $BLUE
    print_message "  查看后台: screen -r $SESSION_NAME" $YELLOW
    print_message "  分离会话: Ctrl+A, D" $YELLOW
    print_message "  停止服务: screen -S $SESSION_NAME -X quit" $YELLOW
else
    print_message "❌ 启动失败" $RED
fi
