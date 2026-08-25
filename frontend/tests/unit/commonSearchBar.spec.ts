/**
 * SearchBar generic input: v-model pass-through, debounced search event
 * (default 300ms, restarts while typing), Enter fires immediately and drops
 * the pending debounce, a clear button appears only with content, and the
 * loading prop renders a spinner.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

import SearchBar from "@/components/common/SearchBar.vue";

afterEach(() => {
  vi.useRealTimers();
});

describe("SearchBar", () => {
  it("emits update:modelValue while typing", async () => {
    const wrapper = mount(SearchBar, { props: { modelValue: "" } });

    await wrapper.find("input").setValue("ab");

    expect(wrapper.emitted("update:modelValue")).toEqual([["ab"]]);
  });

  it("debounces the search event by 300ms", async () => {
    vi.useFakeTimers();
    const wrapper = mount(SearchBar, { props: { modelValue: "" } });

    await wrapper.find("input").setValue("abc");
    vi.advanceTimersByTime(299);
    expect(wrapper.emitted("search")).toBeUndefined();

    vi.advanceTimersByTime(1);
    expect(wrapper.emitted("search")).toEqual([["abc"]]);
  });

  it("restarts the debounce when the user keeps typing", async () => {
    vi.useFakeTimers();
    const wrapper = mount(SearchBar, { props: { modelValue: "" } });
    const input = wrapper.find("input");

    await input.setValue("a");
    vi.advanceTimersByTime(200);
    await input.setValue("ab");
    vi.advanceTimersByTime(200);
    // 400ms since the first keystroke but only 200ms since the last one
    expect(wrapper.emitted("search")).toBeUndefined();

    vi.advanceTimersByTime(100);
    expect(wrapper.emitted("search")).toEqual([["ab"]]);
  });

  it("honours a custom debounceMs", async () => {
    vi.useFakeTimers();
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounceMs: 500 },
    });

    await wrapper.find("input").setValue("slow");
    vi.advanceTimersByTime(499);
    expect(wrapper.emitted("search")).toBeUndefined();
    vi.advanceTimersByTime(1);
    expect(wrapper.emitted("search")).toEqual([["slow"]]);
  });

  it("fires search immediately on Enter and drops the pending debounce", async () => {
    vi.useFakeTimers();
    const wrapper = mount(SearchBar, { props: { modelValue: "" } });
    const input = wrapper.find("input");

    await input.setValue("hello");
    await input.trigger("keydown.enter");
    expect(wrapper.emitted("search")).toEqual([["hello"]]);

    vi.advanceTimersByTime(1000);
    // The pending debounced emission must not double-fire after Enter
    expect(wrapper.emitted("search")).toEqual([["hello"]]);
  });

  it("shows the clear button only when there is a value, and clears on click", async () => {
    const empty = mount(SearchBar, { props: { modelValue: "" } });
    expect(empty.find(".search-clear").exists()).toBe(false);

    const wrapper = mount(SearchBar, { props: { modelValue: "abc" } });
    const clear = wrapper.find(".search-clear");
    expect(clear.exists()).toBe(true);

    await clear.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([[""]]);
    // Clearing also resets any search results right away
    expect(wrapper.emitted("search")).toEqual([[""]]);
  });

  it("uses the configurable placeholder", () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", placeholder: "搜索代码" },
    });

    expect(wrapper.find("input").attributes("placeholder")).toBe("搜索代码");
  });

  it("shows a spinner when loading", () => {
    const idle = mount(SearchBar, { props: { modelValue: "" } });
    expect(idle.find(".search-loading").exists()).toBe(false);

    const loading = mount(SearchBar, {
      props: { modelValue: "", loading: true },
    });
    expect(loading.find(".search-loading").exists()).toBe(true);
  });
});
