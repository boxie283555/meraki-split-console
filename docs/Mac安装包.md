# Mac 安装包

## 安装和打开

1. 从本项目 GitHub Releases 下载 `Meraki-Console-1.1.1.pkg`。
2. 双击安装，程序安装到 `/Applications/Meraki Console.app`。
3. 先安装并启动 Docker Desktop，或准备好 Colima、Docker 和 Docker Compose。
4. 在“应用程序”打开 Meraki Console；首次启动会打开终端和配置文件，填写组织ID及网络名称、保存后按回车。
5. 首次会联网构建Docker镜像，完成后自动打开 `https://localhost:8443`。终端显示本机生成的初始密码，用户名为admin。

这是Docker应用的Mac安装入口，不是完全独立的原生客户端。安装包包含平台源码和启动器，不包含Docker引擎、预构建镜像、你的API Key或现有密码。首次启动需联网，组织API Key仍在页面录入。

## 签名说明

当前包未使用Apple Developer ID签名、未公证；应用只有本地ad-hoc签名。macOS可能阻止打开，请确认下载来自本仓库并核对SHA256后，通过“系统设置 → 隐私与安全性”允许此安装包，不需要关闭系统整体安全保护。受企业设备策略限制时需由管理员批准。没有真实第二台Mac安装验收记录。

## 数据和升级

- 应用：`/Applications/Meraki Console.app`。
- 每个用户的可写程序副本与`.env`：`~/Library/Application Support/Meraki Console/project/`。
- 数据：当前Docker引擎中`meraki-split-console_console-data`命名卷。
- 固定项目名沿用源码版同名数据卷；若原来使用不同项目名或Docker引擎，不会自动迁移数据。
- 安装新版包会更新应用；下次启动同步程序文件，保留用户`.env`和数据卷。不要同时运行旧源码版重建与安装包版重建。
- 原来的`~/Applications/Meraki Console.app`是旧快捷入口，安装器不会删除它。安装后从系统`/Applications`打开新版，避免混淆。

停止：`docker stop meraki-split-console-console-1`。卸载仅需把应用移到废纸篓，数据不会自动删除。数据备份/恢复见[完整安装指南](安装指南.md)。

## 从源码构建

在macOS且已安装命令行工具的环境执行：

```bash
python3 scripts/build-mac-pkg.py --version 1.1.1
pkgutil --check-signature dist/Meraki-Console-1.1.1.pkg
shasum -a 256 dist/Meraki-Console-1.1.1.pkg
```

`pkgutil`会报告包未签名，这是当前发布方式。包内文件采用明确允许列表，不打包工作目录中的`.env`、密钥、运行数据或Git历史。

应用图标采用绿色云与网络节点的原创图形，表示 Meraki 云网络管理；并非 Cisco 官方应用标识。
