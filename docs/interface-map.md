# 手机 QQ 新版宠物接口清单

分析对象：Android QQ 9.3.25（versionCode 15220）。

## 结论

这些功能没有公开的 ADB 命令或普通 HTTP API。宠物请求通过 QQ 已登录进程内的 `PetPbDelegate` 发送，传输层是 QQ SSO/TRPC 或 OIDB。外部程序即使构造出 protobuf，也缺少 QQ 会话、签名和加密封包，不能直接向服务器提交。

可行实现分两种：

1. 安全模式：电脑命令调用 ADB，在官方 QQ 界面完成动作。无需 Root，不替换 QQ，但执行时需要手机连接、解锁并保持 QQ 登录。
2. 原生接口模式：代码运行在 QQ 进程内并调用 `PetPbDelegate`。不依赖界面，但需要 Root/LSPosed 或修改并重签 QQ；当前真机未 Root，因此暂不采用。

## 已确认接口

| 功能 | SSO/TRPC | OIDB | 请求要点 |
| --- | --- | --- | --- |
| 获取本人宠物 | `Sso_PetCache_GetUserPet` | `0x95e1_0` / 38369 / 0 | 无缓存时请求为空；响应 `pet` 的 field 101 是 `petId` |
| 查询喂食次数 | `Sso_PetFeed_GetFeedTimesInfo` | `0x9949_1` / 39241 / 1 | self 查询时 field 4 为空字符串 |
| 喂食 | `Sso_PetFeed_Feeding` | `0x992d_1` / 39213 / 1 | 主人信息、`petId`、喂食类型、扩展字段 |
| 查询打工/学习状态 | `Sso_PetOutdoor_GetPetStoryStatus` | `0x975a_1` / 38746 / 1 | `petId`、查询类型 0 |
| 完成后的故事结算 | `Sso_PetOutdoor_DoAfterStoryInfo` | `0x9760_1` / 38752 / 1 | `storyId`、action 1000、`petId` |
| 获取行为规则 | `Sso_PetBehavior_GetPageRules` | `0x96a4_1` / 38564 / 1 | 页面、来源、扩展字段 |
| 上报行为 | `Sso_PetBehavior_ReportEvent` | `0x96a6_1` / 38566 / 1 | `petId`、执行路径、扩展字段、行为上下文 |
| 查询数值 | `Sso_PetGrowth_GetDisPlayValue` | 由 QQ 内部选择 | 返回心情、体力、清洁、总分、金币 |

SSO 名称的完整前缀均为 `trpc.qqone.gateway.Gateway.`；OIDB 三个数字依次表示命令名、十进制 command、subCommand。

## 行为编号

行为请求中的执行路径由 `(page, eventType, subEvent)` 组成：

| 动作 | page | eventType | subEvent |
| --- | ---: | ---: | ---: |
| 洗澡进度 | 5000 | 500 | 501 |
| 洗澡中断 | 5000 | 500 | 502 |
| 手动擦洗 | 5000 | 500 | 503 |
| 学习：文化 | 6000 | 6100 | 6101 |
| 学习：体能 | 6000 | 6100 | 6201 |
| 学习：艺术 | 6000 | 6100 | 6301 |
| 打工：文化 | 6000 | 6400 | 6401 |
| 打工：体能 | 6000 | 6400 | 6501 |
| 打工：艺术 | 6000 | 6400 | 6601 |

学习类型还存在 sport/art/culture 选择事件 8401、8402、8403。

## Protobuf 字段

### Feeding request (`xi5.b`)

- 1 string：宠物主人 UIN（本人场景可空）
- 2 string：昵称
- 3 string：头像 URL
- 4 string：`petId`
- 5 int32：喂食类型；客户端接受 0、1001、9990032、9990033、9990034
- 10 message：通用扩展字段
- 99 message：平台扩展字段（可选）

### GetPetStoryStatus request (`ej5.i`)

- 1 string：`petId`
- 2 int32：查询类型；本人查询使用 0

### DoAfterStoryInfo request (`ej5.b`)

- 1 string：`storyId`
- 2 int32：action；当前客户端固定发送 1000
- 3 string：`petId`
- 4 int32：附加枚举，默认 0

### ReportEvent request (`aj5.d`)

- 1 string：`petId`
- 2 string：好友 UIN，本人场景为空
- 3 message：执行路径，含 page/eventType/subEvent
- 4 message：执行扩展信息
- 5 message：业务扩展信息（可选）
- 6 map<string,string>：未读数等附加参数（特定页面）
- 90 message：行为上下文（可选）
- 99 int32：平台字段

## 不能省略的动态数据

- 当前账号对应的 `petId`
- 打工或学习实例返回的 `storyId`
- 服务端下发的行为规则与执行扩展字段
- QQ 当前登录会话及 SSO 封包认证

因此不能安全地用固定十六进制包重放，也不能只修改客户端显示值。洗澡页面中的 `DisplayValueManager.updateSingleValue` 只更新本地缓存；最终清洁值仍以服务器推送、心跳或轮询结果为准。

## 已确认的进程内入口

QQ 9.3.25 的宿主实现为 `com.tencent.mobileqq.qqpet.delegate.l`：

- 方法 `a(data, cmd, observer)` 通过 `PetProtoServlet` 和当前 `AppRuntime` 发送 SSO；
- 方法 `c(data, oidbName, command, subCommand, observer)` 通过 `ProtoUtils.a` 发送 OIDB；
- 回调接口为 `PetPbDelegate$a.onResult(code, data, bundle)`。

这证明不需要从 QQ 中导出 token，但也限定了调用方必须在 QQ 进程内。只读反射调用样例位于 `direct-bridge`；目前不包含导出的广播入口，也不包含任何会改变宠物状态的请求。

## 电脑版 QQ / NapCat 验证

2026-08-02 已通过 NapCat 的 `send_packet` 和电脑版 QQ 9.9.26-44343 实测 `OidbSvcTrpcTcp.0x95e1_0`。OneBot 与 OIDB 返回码均为 0，并收到有效业务响应，证明可以由电脑版 QQ 处理登录会话、签名及加密封包，不再要求手机或 ADB。

命令字中的十六进制部分必须保持手机 QQ 使用的小写形式；大写 `0x95E1_0` 在本次环境中只返回空响应。随后已通过本人宠物数据取得并实测对应 `petId`，并写入本机私有的 `config.yaml`。本机为该账号增加了仅监听 `127.0.0.1:6201` 的专用 OneBot HTTP 入口，电脑端现在可直接读取宠物状态，不需要手机或 ADB。

`0x975a_1` 的当前任务响应也已实测校准：详情 field 1 是状态码，field 2 是递减的剩余秒数，field 3 是任务总秒数，field 4 是开始时间。当前账号返回的 `6400_...` 是打工任务，采样时总时长 14400 秒；接口助手会把它恢复成本地待计数任务，而不会误判为已经完成。

目前尚未确认打工地点/职位目录的收益字段以及雇佣好友所需动态字段。因此项目中这两个配置项只是调度偏好，尚不会被伪装成已经生效的固定封包。

学习、打工和冒险现已接入 `GetPageRules -> ReportEvent -> GetPetStoryStatus` 三段流程。`GetPageRules` 请求的 field 2 是 `petId`、field 3 是页面 6000、field 4 是客户端状态扩展；响应 field 1 是下一次请求使用的 trace，field 2 是可用事件列表。调度器只有在事件列表包含目标执行路径时才会上报启动事件，并以服务端返回的 `storyId` 作为最终成功凭证。
