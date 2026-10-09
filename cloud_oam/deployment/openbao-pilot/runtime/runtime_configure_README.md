# 阶段 B：正式 auth 与 OIDC 逐对象配置候选

**试点 MVP，不等同完整 V1。该工具已完成本地新测试，尚未实际读取正式 root token、配置正式对象、签发 JWT 或启动 Agent。** 本文推进 `runtime_bootstrap_plan.md` 的 B 阶段，不修改已冻结 A/init 工具及其历史回执。

`runtime_configure.py auth|oidc` 默认只读核对，必须显式 `--apply` 才允许创建缺失对象。两阶段各自执行和回读，不能仅以进程返回0宣称云信任、出站网络或业务可用。

- `auth` 建立并核验 `auth/rsc-runtime` AppRole mount；三用途 `rsc-transit/rsc-oss/rsc-pnvs` 的精确 ACL、20分钟 service periodic role、三独立 entity 和 role-id alias。显式要求无 default/额外 legacy policy、无 entity/group/合并历史带入的权限；role ID、entity ID 均独立。role ID只在内存用于alias，不落公开输出。
- `oidc` 将根issuer配置成 `https://rscwz.cn`（真实token issuer为 `/v1/identity/oidc`），建立 `rsc-runtime` RS256签名key（rotation/verification均86400秒、allowed clients仅两个不同audience），以及 `rsc-oss/rsc-pnvs` 600秒role。模板为空，不允许注入额外claims。audience必须由主任务选择且与真实RAM信任条件一致，工具不自行猜云账号或role ARN。
- 只允许明确列出的 GET/POST 路由，不含登录、token create、SecretID生成、Transit key/datakey操作、DELETE、挂载其他engine或任意admin接口。Transit wrapped DEK/独立DB pin是下一阶段，不能从此阶段推导通过。

每个对象先GET。当前对象完整相等才复用；缺失时，`--apply` 先在Bao私有 `/state` 独占创建0600公开操作marker并fsync，再只发一次POST，最后精确GET核对。任何不匹配都阻断，不覆盖旧配置。写超时留下marker；再次只读可判断对象是否已精确建立，缺失并且已有marker时即使apply也不能重放。没有删除marker、自动回滚或自动重试。OIDC根配置的初始空issuer是固定源码确认的唯一显式默认值例外，其他非空不同issuer不可覆盖。

root token来自已验证RW加密映像中的原PGP封存，解密只在内存；与init相同run/container/fingerprint绑定、前后本机mount身份与远端完整container/image/StartedAt/PID/源hash一致。公共helper源通过 `python3 -B -c` 传递；root token仅进入SSH/docker私有stdin，不进入命令参数、环境、常规日志或回执。远端先验证UID/UDS/SO_PEERCRED/swap0，再确认固定2.7.1 Shamir已经初始化且解封、lookup-self恰为原root身份。所有异常输出为闭集代码，不回显原HTTP错误或输入。不会自动解封或启动服务。

CLI公开坐标包括 `--ssh-config`、`--ssh-host`、`--container-id`、`--instance-id`、`--run-id`、`--vault-mount`、`--vault-binding`、`--oss-audience` 和 `--pnvs-audience`。首次正式容器ID须以主任务当轮的实际inspect为准，不使用示例/容器名；重建或重启后不可沿用旧发布证明。返回的entity ID为公开subject候选，必须再用于真实RAM trust/JWT验证。

固定源码证据位于 `artifacts/openbao-runtime-20261009/bootstrap-upstream/`，commit `a5db72cef75c24b920ade02065b18dd8eb666bac`，三源blob与既有完整tree精确匹配：

- `store_entities.go:82–100`：按name的更新和读取；421–437缺失逻辑；470–507 **GET** aliases是完整对象列表，而创建响应402–410是ID列表，不能混用。alias正向校验用GET结果。
- `store_aliases.go:31–71`：创建所用canonical_id/mount_accessor/name；343–346创建只回id/canonical_id；504–550按ID读取完整字段。
- `store_oidc.go:416–440,506–539`：issuer读/默认空值与有效路径拼接；685–711：key GET四字段；1345–1367：role GET client_id/key/template/ttl。没有把当日浮动文档当固定版本能力证明。
- 现有锁定AppRole `path_role.go:1758–1804`：read的秒值、local_secret_ids、legacy policies/period；本工具对这些潜在额外策略另行拒绝。

新增测试分三次，仅本批：首12节点通过（0.215秒），随后新增role-id重复反例+1直接受影响正例通过（0.230秒），再新增默认只读反例+4直接受影响节点通过（0.238秒），总计14个唯一新节点。每轮源前后SHA稳定，中间修改前源码归档；没有重跑无关旧套件，没有真实远端或正式秘密。unittest日志并非pytest XML/诊断JSONL，不能混称。正式逐对象配置、有效token策略、真实JWT签名/subject/aud、云STS仍各自待验。

常驻故障语义保持：现unit为oneshot/RemainAfterExit，container restart=no。`systemctl active (exited)`不能证明进程健康；必须检查精确container ID、Running/StartedAt/PID/OOM及实际seal/readiness。容器异常退出或主机重启后走人工受控解封/重新bootstrap，禁止拿已消费的SecretID自动重试。首次A工具的run marker也不是通用灾后恢复工作流，不删除原marker来凑重启成功。
