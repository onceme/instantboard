/**
 * ConfirmationDialog: teleported to <body>, emits confirm/cancel (+
 * update:visible false), overlay click and Esc act as cancel, the confirm
 * button receives focus on open, and default/custom button texts + danger
 * styling are applied.
 */
import { afterEach, describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";

import ConfirmationDialog from "@/components/common/ConfirmationDialog.vue";

const wrappers: VueWrapper[] = [];

function mountDialog(props: Record<string, unknown> = {}): VueWrapper {
  const wrapper = mount(ConfirmationDialog, {
    props: {
      visible: true,
      title: "删除数据源",
      message: "该操作不可恢复",
      ...props,
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

function overlay(): HTMLElement | null {
  return document.body.querySelector(".dialog-overlay");
}

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount());
  wrappers.length = 0;
  document.body.innerHTML = "";
});

describe("ConfirmationDialog", () => {
  it("renders nothing while hidden", () => {
    mountDialog({ visible: false });

    expect(overlay()).toBeNull();
  });

  it("is teleported to body and shows title and message", async () => {
    mountDialog();
    await flushPromises();

    const root = overlay();
    expect(root).not.toBeNull();
    expect(root!.textContent).toContain("删除数据源");
    expect(root!.textContent).toContain("该操作不可恢复");
  });

  it("emits confirm and closes when the confirm button is clicked", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    (document.body.querySelector(".btn-confirm") as HTMLButtonElement).click();
    await flushPromises();

    expect(wrapper.emitted("confirm")).toHaveLength(1);
    expect(wrapper.emitted("update:visible")).toEqual([[false]]);
  });

  it("emits cancel and closes when the cancel button is clicked", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    (document.body.querySelector(".btn-cancel") as HTMLButtonElement).click();
    await flushPromises();

    expect(wrapper.emitted("cancel")).toHaveLength(1);
    expect(wrapper.emitted("update:visible")).toEqual([[false]]);
    expect(wrapper.emitted("confirm")).toBeUndefined();
  });

  it("treats a click on the overlay (outside the dialog) as cancel", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    // Click the dialog box itself first: must NOT cancel
    (document.body.querySelector(".dialog") as HTMLElement).dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );
    await flushPromises();
    expect(wrapper.emitted("cancel")).toBeUndefined();

    overlay()!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    expect(wrapper.emitted("update:visible")).toEqual([[false]]);
  });

  it("cancels on Escape but ignores Escape while hidden", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(wrapper.emitted("cancel")).toHaveLength(1);

    const hidden = mountDialog({ visible: false });
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(hidden.emitted("cancel")).toBeUndefined();
  });

  it("focuses the confirm button when opened", async () => {
    mountDialog();
    await flushPromises();

    const confirmBtn = document.body.querySelector(".btn-confirm");
    expect(document.activeElement).toBe(confirmBtn);
  });

  it("uses the default button texts and honours custom ones", async () => {
    const defaults = mountDialog();
    await flushPromises();
    expect(
      (document.body.querySelector(".btn-confirm") as HTMLElement).textContent,
    ).toBe("确认");
    expect(
      (document.body.querySelector(".btn-cancel") as HTMLElement).textContent,
    ).toBe("取消");
    defaults.unmount();

    mountDialog({ confirmText: "删除", cancelText: "再想想" });
    await flushPromises();
    expect(
      (document.body.querySelector(".btn-confirm") as HTMLElement).textContent,
    ).toBe("删除");
    expect(
      (document.body.querySelector(".btn-cancel") as HTMLElement).textContent,
    ).toBe("再想想");
  });

  it("applies danger styling to the confirm button when danger is set", async () => {
    mountDialog({ danger: true });
    await flushPromises();

    const confirmBtn = document.body.querySelector(".btn-confirm");
    expect(confirmBtn!.classList.contains("btn-danger")).toBe(true);
    expect(document.body.querySelector(".dialog-icon-danger")).not.toBeNull();
  });
});
