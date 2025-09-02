# 📁 TurboFile 项目结构

```
TurboFile/
├── 📄 app.py                    # 主应用程序文件
├── 📁 templates/                # HTML模板目录
│   └── 📄 index.html           # 主界面模板
├── 📁 static/                   # 静态资源目录（可选）
├── 📄 requirements.txt          # Python依赖列表
├── 📄 turbofile.service        # systemd服务配置文件
├── 🔧 install_service.sh       # 服务安装脚本
├── 🔧 manage_service.sh        # 服务管理脚本
├── 🔧 uninstall_service.sh     # 服务卸载脚本
├── 🔧 start_background.sh      # 后台启动脚本
├── 🔧 deploy.sh                # 部署脚本
├── 📄 README.md                # 项目说明文档
├── 📄 LICENSE                  # 开源许可证
├── 📄 .gitignore              # Git忽略文件配置
├── 📄 STRUCTURE.md             # 项目结构说明（本文件）
└── 📄 6.极速传.py              # 原始脚本文件（历史版本）
```

## 📋 文件说明

### 🔧 核心文件
- **app.py**: Flask主应用程序，包含所有后端逻辑
- **templates/index.html**: 前端界面模板，包含HTML、CSS和JavaScript
- **requirements.txt**: Python依赖包列表

### 🛠️ 部署脚本
- **install_service.sh**: 自动安装系统服务
- **manage_service.sh**: 服务管理工具（启动/停止/重启/状态查看）
- **uninstall_service.sh**: 卸载系统服务
- **start_background.sh**: 后台启动应用
- **deploy.sh**: 一键部署脚本

### 📚 文档文件
- **README.md**: 完整的项目说明和使用指南
- **LICENSE**: MIT开源许可证
- **STRUCTURE.md**: 项目结构说明

### ⚙️ 配置文件
- **turbofile.service**: systemd服务配置
- **.gitignore**: Git版本控制忽略规则

### 📁 目录结构
- **templates/**: Flask模板文件目录
- **static/**: 静态资源目录（CSS、JS、图片等）
- **__pycache__/**: Python字节码缓存（自动生成）

## 🚀 快速开始

1. **克隆项目**
   ```bash
   git clone <repository-url>
   cd TurboFile
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **启动服务**
   ```bash
   # 开发模式
   python app.py
   
   # 生产模式
   sudo bash install_service.sh
   ```

4. **访问系统**
   ```
   http://localhost:5000
   ```

## 🔧 开发说明

### 主要组件
- **Flask应用**: 提供Web服务和API接口
- **WebSocket**: 实现实时进度更新
- **SSH连接池**: 管理服务器连接
- **文件传输**: 基于rsync的高效传输

### 扩展指南
- 添加新服务器：修改`app.py`中的`servers`配置
- 自定义界面：编辑`templates/index.html`
- 添加新功能：在`app.py`中添加新的路由和处理函数

---

**TurboFile** - 让文件传输变得简单高效！ ⚡
