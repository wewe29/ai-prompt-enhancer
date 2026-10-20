// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { ConfirmClearModal } from "./ConfirmClearModal";

/**
 * 清空是可能**部分成功**的操作：本地数据删了，Windows 凭据管理器里的 API Key 删不掉
 * （src-tauri/src/storage.rs:364-376，Entry::new 失败时整块被跳过却仍返回 Ok）。
 * 后端为此返回 Err，弹窗必须把它显示出来，而不是让它变成未处理的 rejection。
 */
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function mount(node: ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(node);
  });
  return { container, root };
}

describe("清空确认弹窗：失败必须看得见", () => {
  it("后端返回 Err 时显示错误原文，且弹窗保持打开", async () => {
    const message = "本地数据已清空，但删除 Windows 凭据失败：请打开凭据管理器手动确认。";
    const { container, root } = mount(
      <ConfirmClearModal
        onCancel={() => undefined}
        onConfirm={() => Promise.reject(new Error(message))}
      />,
    );
    const confirm = container.querySelector("button.primary") as HTMLButtonElement;
    expect(confirm).toBeTruthy();
    await act(async () => {
      confirm.click();
    });
    expect(container.textContent).toContain(message);
    // 弹窗不能自己关掉：关不关由上层决定，失败时必须留在原地等用户看清原因。
    expect(container.querySelector("h2")?.textContent).toBe("彻底清空应用数据");
    act(() => root.unmount());
  });

  it("清空成功时不显示任何错误", async () => {
    const { container, root } = mount(
      <ConfirmClearModal onCancel={() => undefined} onConfirm={() => Promise.resolve()} />,
    );
    const confirm = container.querySelector("button.primary") as HTMLButtonElement;
    await act(async () => {
      confirm.click();
    });
    expect(container.querySelector(".error-banner")).toBeNull();
    act(() => root.unmount());
  });
});
