# QQ 宠物进程内直连接口（只读验证版）

目标版本：Android QQ 9.3.25（versionCode 15220）。

## 当前结论

接口可以直接调用，但不是普通 HTTP 接口。`PetPbDelegate` 会把 protobuf 请求交给
QQ 自己的 SSO/OIDB 通道，因此调用代码必须运行在已登录的 `com.tencent.mobileqq`
进程中。这样无需导出登录 token，也无需自行实现 QQ 的签名和加密层。

`QQPetDirectClient` 已实现两个只读请求：

- `queryOwnPet`：空请求调用 `0x95e1_0`，自动从响应 `Pet` 的 field 101 解析 `petId`。
- `queryStoryStatus`：调用 `0x975a_1`，查询当前打工/学习故事状态。

核心代码没有 Android 或 Xposed 的编译依赖；注入层只需把 QQ 的 `ClassLoader`
传入构造函数。当前没有提供导出的广播接收器，避免其他应用在未授权时触发宠物操作。

## 运行前提

普通 ADB shell 不在 QQ 进程里，无法直接加载这些类。需要以下任一运行入口：

1. 已 Root 手机上的 LSPosed 模块；
2. 调试/注入框架把入口加载到 QQ 进程；
3. 修改 QQ APK 后重签（风险最高，不建议用于主账号）。

真机在上次检查时未 Root，所以该版本只完成离线代码与协议验证，尚未向服务器发包。
接入时应先只运行 `queryOwnPet`，确认返回码为 0 且 `petId` 非空，再开放任何写操作。

## 已验证的 QQ 内部调用链

```text
QQPetDirectClient
  -> com.tencent.mobileqq.qqpet.delegate.l
     -> ProtoUtils.a(...)                  [OIDB]
     -> PetProtoServlet + startServlet(...) [SSO]
        -> QQ 当前 AppRuntime 登录会话
```

## 写操作的安全门

喂食、结算、行为上报尚未接入本类。它们可能消耗次数或改变服务端状态；必须在只读
请求验证成功并保存原始响应后，再逐项补充动态规则、storyId 和扩展字段。
