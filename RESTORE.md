# Meraki 配置快照恢复

备份是API可读取配置集合，不是整机镜像。离线恢复计划不会执行API写入。manifest.json记录成功、失败、HTTP状态及未覆盖接口；有缺口时不能视为完整备份。

## 核实备份

通过Docker执行（替换备份ID）：

```sh
docker-compose exec console python backup.py --verify /data/state/backups/备份ID
docker-compose exec console python backup.py --plan /data/state/backups/备份ID
```

核对organizationId、networkId、序列号及备份时间。新网络的ID不能直接套用。先备份当前现网，保留独立管理通道，再逐项恢复；不要先写可能中断网络的WAN、VLAN或默认路由。

## 按范围恢复

仅VPN排除规则误改时，只恢复对应规则。完整保留protocol、destination、port及source.vlanId/source.port，避免把特定VLAN扩大成所有客户端。保留不属于本应用管理的手工规则和应用规则。

配置下发前必须确认API当前schema、设备能力及没有其他管理员并发修改。提交后读回核对，若结果不确定不要自动重试覆盖。

组织级设置、VPN Hub、模板、策略对象可能关联多个网络，需要按依赖关系单独恢复。掩码密码、证书私钥及GUI独有配置不能依靠API快照恢复。不要将备份文件直接上传到GitHub。

## 容器迁移

停止应用后私下备份console-data卷，再恢复到新机器相同/data路径。该卷含API Key、登录哈希、TLS私钥和设备配置；不得公开。保留旧环境可回退，在新环境验证后再切换，避免两个实例对同一网络同时自动下发。
