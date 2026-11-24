# UnityNetech Meraki Console

在 Mac 上通过 Docker 运行的 Meraki 分流管理平台。支持中国 IPv4 地址库预览、现有规则对比、配置备份、带保护的规则下发、域名/IP 查询和 SmartDNS 国内/海外 DoH 分组。

本版本提供 Mac 双击启动入口，界面运行于本机浏览器；不是原生 Swift App。iPhone App 暂不开发。Docker 容器中不需要 systemd，不挂载宿主机 Docker socket，不修改原有服务器或路由器。

## 安装后如何登录（默认账号与初始密码）

| 项目 | 内容 |
| --- | --- |
| 登录地址 | https://localhost:8443 |
| 默认用户名 | `admin` |
| 初始密码 | 首次启动时自动生成，每个新数据卷独立；没有统一的固定密码 |
| 在哪里看密码 | 双击 Meraki Console 启动后，终端窗口会显示初始密码 |

如果已关闭启动窗口，在 Mac 的“终端”中执行下面命令即可再次查看（无需切换目录，应用容器需正在运行）：

```bash
docker exec meraki-split-console-console-1 cat /data/private/gui-initial-password
```

复制命令输出的密码登录，然后进入 **“管理员密码”** 修改为自己的密码。改密后初始密码文件会自动删除，此后使用修改后的密码；升级或重装应用且保留原数据卷时，仍使用原密码。

## 安装入口

**Mac 安装包：** 在 [GitHub Releases](https://github.com/boxie283555/meraki-split-console/releases) 下载 `.pkg`，安装后从“应用程序”启动。详见 [安装包说明](docs/Mac安装包.md)。仍需 Docker Desktop 或 Colima，首次运行需联网构建。

第一次使用请按 **[Mac 完整安装指南](docs/安装指南.md)** 操作，包含Docker准备、代码下载、配置、首次登录、Mac双击入口、升级、数据备份和常见问题。

已有Docker环境的快速安装：

```bash
git clone https://github.com/boxie283555/meraki-split-console.git
cd meraki-split-console
cp .env.example .env
# 先编辑 .env，填写自己的 Meraki 组织 ID 和网络名称
./scripts/start-mac.command
```

页面地址：**https://localhost:8443**；初始账号`admin`，密码获取方式见下文。没有API Key也可以先打开页面，再配置Meraki接入。

## Mac 安装

安装 Docker Desktop 并启动，或使用免费的 Colima：

```sh
brew install colima docker docker-compose
colima start --cpu 2 --memory 2 --disk 20 --vm-type vz
```

Apple Silicon 原生运行；Intel Mac 可移除不支持的虚拟化参数并采用对应 Colima 默认设置。镜像基于 Python3.12/Debian，运行构建需能访问 Docker Hub 和 Debian 软件源。

复制环境模板并填写组织 ID、网络名称：

```sh
cp .env.example .env
# 编辑 .env 的 MERAKI_ORG_ID、MERAKI_NETWORK_NAME
./scripts/start-mac.command
```

打开 **https://localhost:8443**。首次自动生成本机自签名证书，浏览器会提示不受信任；仅在确认是本机地址后继续。也可以将自己的可信证书放入数据卷的private/gui-cert.pem和gui-key.pem，再重启。

默认账号 `admin`，首次密码只保存在私有数据卷。用下面命令在自己的终端查看，不要把输出上传 GitHub：

```sh
docker-compose exec console cat /data/private/gui-initial-password
```

支持`docker compose`插件的安装可用空格写法替换`docker-compose`。首次登录后，建议自行保管或删除初始明文密码文件；认证使用独立加盐PBKDF2哈希。

进入“接入设置”，输入自己的 Meraki API Key。读取使用只读权限即可；规则下发需要拥有相应写权限的账号。不要复制别人的API Key。新数据卷无自动下发授权；自动下发需要成功完成完整国内库下发验证后，在页面明确打开开关。

## 管理员密码与多站点（v1.1.0）

登录后进入“管理员密码”，输入当前密码、新密码和确认密码。新密码为12～128个字符；修改成功后所有已登录会话退出，初始明文密码文件自动删除，请用新密码重新登录。

进入“站点管理”添加组织ID、网络名称（可指定Network ID）与该站点API Key。保存前会只读验证目标并固定Network ID。用页面顶部站点选择器切换，各站点独立保存凭据、预览、计划、备份、规则归属及自动下发开关。下发前确认当前站点、组织和网络。新增站点不会立即下发；多个站点分别创建计划、确认下发，不提供跨站点一键批量写入。SmartDNS为整个实例共享，不随站点切换。

升级会保留原有默认站点、密码和数据。Mac用户安装新版安装包并重新启动应用；源码部署执行 `docker-compose up -d --build`，不要删除数据卷。新增站点的配置和密钥也保存在数据卷中，备份须涵盖整个数据卷。

## Mac 应用入口

```sh
python3 scripts/install-mac-app.py
```

生成 `~/Applications/Meraki Console.app`。双击会打开终端、启动 Docker/Colima 和应用，并打开本机浏览器。此入口引用当前源码目录，移动目录后需重新安装入口。不需要Apple开发者账号。Docker服务仍需Mac开机且运行；睡眠期间任务不会持续执行。

## 数据与任务

`console-data` 命名卷存放配置、API Key、登录认证、证书、预览、备份、任务日志和DNS域名版本。源码、镜像及仓库均不包含这些运行数据。不要使用`docker compose down -v`，否则会删除该卷。

- 每天北京时间05:00执行预览和DNS更新；若Mac关机，恢复后补做当天日任务。
- 每周六05点时段，在本轮预览成功后才调用自动下发；应用自身还检查automatic开关及所有原有下发保护。
- 不补做错过时间窗口的周下发；正在运行的同一任务不会重复启动。
- 下发保持原程序的计划ID、备份、现网指纹检查、只管理自有规则、提交后读回核对及不确定事务保护。

组织ID/网络名环境变量只在新卷第一次初始化时使用。之后修改`/data/config.json`并重启应用；切换组织或网络建议使用全新数据卷，防止混用规则归属与计划。

运行日志：`docker-compose logs --tail 100`；任务日志在`/data/logs/`。停止：`docker-compose stop`；重新启动：`docker-compose up -d`。

## SmartDNS

容器内DNS监听5353，国内域名走AliDNS/DNSPod DoH，默认组走Cloudflare DoH；下载域名库后先做健康及隔离验证，再加载，失败保留旧版。初次DNS更新依赖公网访问，未成功前不会伪报DNS已运行。

默认不发布DNS到局域网、不修改Mac的DNS设置。需要给客户端提供DNS时，确认Mac固定地址、53端口空闲，再修改`.env`的BIND_IP，并运行：

```sh
docker-compose -f compose.yaml -f compose.dns.yaml up -d
```

同时设置CONSOLE_HOST为允许访问的Mac地址。应用Host/Origin校验仍开启。Docker on Mac提供的是应用和可选DNS服务，**不会自动成为VPN路由器、不会自动安装OpenConnect、不会改变Mac或客户端路由**。

## 验证与迁移

```sh
python3 -m unittest discover -s tests -v
docker-compose build
docker-compose up -d
docker-compose ps
```

迁移运行数据时先停任务，将整个命名卷离线备份，再在新环境恢复到同一路径/data。备份包含敏感凭据及网络配置，应私下保存，不能提交GitHub。不要在两个实例同时开启同一Meraki网络的自动下发。

参考RESTORE.md了解Meraki配置快照的恢复限制。快照不是厂商可直接导入的整机镜像。
