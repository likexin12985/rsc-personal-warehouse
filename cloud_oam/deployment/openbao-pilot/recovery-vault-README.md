# 希捷移动盘上的空恢复容器准备

用户当前决定：恢复材料由本人保管，保存到当前希捷移动硬盘。**单人、单盘；没有第二复核人、第二副本或异地副本。** 本文件不得写成双人分持或灾备已完成。该准备不生成或搬运任何正式 root token、解封份额、SecretID 或密钥文件。

2026-10-09 本机只读核验的固定介质：

- `/Volumes/Seagate Backup Plus Drive`
- Volume/Disk UUID `4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC`
- USB 外置 APFS，可写；当时设备节点 `disk7s1`。设备节点可变，UUID 与挂载点必须同时重验。
- `FileVault=false`、`Encryption=false`；卷本身未加密，`GlobalPermissionsEnabled=false`。不得把外层目录的 chmod 当作恢复材料保密保障。

最小方案是在此盘创建一个全新专用目录，里面只放一个 **AES-256 加密、Journaled HFS+ 的小型单文件读写磁盘映像**和不含秘密的 JSON 回执。CLI 原计划 64 MiB；后来 GUI 实际成功创建 100 MB，仍小于检查器 128 MiB 上限。HFS+ 仅用于这个小容器，不改现有整盘 APFS。新容器先保持空白且卸载，正式恢复材料须在后续受控初始化/导入和恢复验证中单独处理。

## 密码边界

脚本只调用系统 `/usr/bin/hdiutil ... -encryption AES-256 -agentpass`。该选项请求系统处理密码提示，但**不保证出现图形密码窗口**；脚本断开 stdin，也不提供交互终端。脚本不询问、读取、复制、输出或存储密码，没有 AppleScript 收集、`-stdinpass`、密码命令参数、密码环境变量、剪贴板或 Keychain 导出。

用户只在获准使用的 macOS 系统界面输入密码，不发到聊天；用户自己保存密码，不勾选自动保存到钥匙串。若系统密码窗口未出现、用户取消或执行中断，应保留当前结果并使用 `status` 精确读取，不能改为要求用户发密码，也不能自动重建。

2026-10-09 首次 CLI 空容器尝试 `7ab884150e2e` exit 1；用户确认未见密码窗口。当时最终源码的独立 `status` 回读为 `imageExists=false`。原 stderr 未保留，因此不能声称已确定失败原因，也不能记为用户取消。该环境不继续尝试 CLI 创建，转为用户授权后的磁盘工具界面；电脑操作工具曾返回未获准访问磁盘工具，该历史结果保留。

随后应用授权实际生效，GUI 在同一已核验目录创建此前不存在的 `RSC-recovery.dmg`，报告成功；系统 `imageinfo` 返回 `CEncryptedEncoding / AES-256 / UDRW`。精确卸载本映像后 `isencrypted` 返回 `encrypted=true`，冻结脚本 `status` 返回 `encrypted_detached`。实际映像宗卷 UUID 为 `67A771D2-90A4-3F23-B837-F2EB70BFC7F8`，大小 `100016640` 字节，关闭摘要 `d204aa49c2ec72b7c7da52d1c85274ecf3e1cec9c976ee271f77c3c68cb668eb`。未卸载物理硬盘或其他映像，原失败 `receipt.json` 未覆盖，另有不含秘密的 `gui-creation-receipt.json`。

**当前仍待用户确认密码保管并实际重开解锁，正式恢复材料尚未生成。** GUI 密码输入过程未观察到；加密头和成功提示不能替代密码保管/恢复证明。脚本 `status` 的 `passwordHandledBy` 是静态 CLI 实现标签，不能据此推断本次 GUI 密码来源。不要自动重建、覆盖、读取钥匙串或要求用户在聊天发送密码。

后续已用磁盘工具重新打开同一映像并核对相同宗卷 UUID，然后再次精确卸载。未观察到密码窗口或用户输入，因此仅是当前系统会话可重开，不算保管人独立解锁/冷恢复通过。当前关闭后的摘要更新为 `fd9a84e061039721b06951f483a8a64c67adbd8b78ae472bf205cde0bacaf4e3`，回执 `gui-reopen-receipt.json`；原初次关闭摘要保留为历史，正式材料仍为空。

## 命令

只读预检（不会建目录或映像）：

```sh
python3 cloud_oam/deployment/openbao-pilot/recovery-vault.py preflight
```

以下保留 CLI 创建命令作为实现说明；本机非交互执行路径尚未验证可用，不应据此自动再试。未来重新启用前需确认受支持的系统密码交互方式；12 位小写十六进制 ID 必须是本次新值：

```sh
python3 cloud_oam/deployment/openbao-pilot/recovery-vault.py create --vault-id NEW12HEXVALUE
```

中断/失败或完成后只读回查同一 ID：

```sh
python3 cloud_oam/deployment/openbao-pilot/recovery-vault.py status --vault-id SAME12HEXVALUE
```

任何已有专用目录均拒绝重建；创建命令不使用覆盖参数，不删除未知映像、失败文件或回执，不格式化、不整盘加密、不 eject 硬盘，也不自动 detach 不明确归属的映像。硬盘未挂载、UUID 改变、路径含 symlink 或不可写都失败关闭，绝不建立同名本地替代目录。

成功证据由 `hdiutil isencrypted -plist` 确认加密标记，`hdiutil info -plist` 精确确认此映像未挂载，文件大小/设备/单链接核验和封闭映像 SHA-256 组成。GUI 本次另通过 `imageinfo` 的 `Backing Store Information.Encryption` 字段独立确认 AES-256；不能给 `isencrypted` 虚构算法字段。挂载时 `isencrypted` 曾返回资源暂时不可用，关闭后成功，不应将前者判断为未加密。回执只含这些非密信息以及当前单人单盘事实。

`hdiutil verify` 不适用于该读写映像（系统手册明确读写映像无内置校验和），所以使用独立 SHA-256，且在映像挂载时不计算稳定摘要。以后每次写入正式材料并准确关闭映像后，须重新记录摘要、保存不含秘密的物料清单，并实际演练解锁/恢复；空容器创建成功不等于正式恢复安排全部完成。
