/**
 * 设置页的「保存结果」文案与「陈旧结果」提示。
 *
 * **为什么要抽出来**：这两条文案都曾是「把没做的事说成做了」——
 *   - G9：Key 留空时**一次网络请求都没发**，界面却显示「连接验证成功，配置已保存」；
 *   - G11：请求失败时上一版已付费的结果被清空，用户什么都拿不到。
 *
 * 逻辑放在组件里就没法测。抽成纯函数后，
 * `src/providerMessage.test.ts` 能直接断言文案本身。
 */

/** 保存供应商配置后，界面该怎么陈述「验证」与「保存」这两件事。 */
export function buildProviderSaveMessage(input: {
  /** 本次是否真的发起了连接验证（Key 留空时不发）。 */
  validated: boolean;
  /** 本次是否写入了 API Key 到 Windows 凭据管理器。 */
  keyWritten: boolean;
}): string {
  if (input.validated) {
    return input.keyWritten
      ? "已验证：API Key 可用（已用当前地址实测）。已保存：API Key → Windows 凭据管理器，地址与模型 → 本机配置。"
      : "已验证：API Key 可用（已用当前地址实测）。已保存：地址与模型 → 本机配置。本次没有改写已保存的 API Key。";
  }
  return input.keyWritten
    ? "已保存：API Key → Windows 凭据管理器，地址与模型 → 本机配置。未验证连接：未填写 API Key，本次没有测试过能否连通。"
    : "已保存：地址与模型 → 本机配置。未验证连接：未填写 API Key，本次没有测试过能否连通。";
}

/**
 * 请求失败后，界面上仍留着上一版结果 —— 必须说清「你看到的是上一次的」。
 *
 * G11：此前发请求之前就 `setResult(null)`，网络抖一次，
 * 用户上一条**已经付费跑出来的**结果就没了，而且没有任何提示。
 * 一次抖动变成真实的金钱损失。
 *
 * 诚实的定义是「用户知道自己在看什么」，不是把东西藏起来。
 */
export function staleResultNotice(resultAt: string): string {
  return `本次请求失败，下面显示的是上一次的结果${resultAt ? `（${resultAt}）` : ""}。`;
}
