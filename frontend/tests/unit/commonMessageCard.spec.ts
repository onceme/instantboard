/**
 * MessageCard generic card: renders title/summary meta, formats valid
 * timestamps as relative time (and leaves unparseable values unchanged),
 * turns the title into a new-tab link only when a url is provided, and
 * exposes default + footer slots.
 */
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import MessageCard from "@/components/common/MessageCard.vue";

const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();

describe("MessageCard", () => {
  it("renders title, summary and source", () => {
    const wrapper = mount(MessageCard, {
      props: {
        title: "市场快讯",
        summary: "美股三大指数集体高开",
        source: "Reuters",
      },
    });

    expect(wrapper.find(".card-title").text()).toBe("市场快讯");
    expect(wrapper.find(".card-summary").text()).toBe("美股三大指数集体高开");
    expect(wrapper.find(".card-source").text()).toBe("Reuters");
  });

  it("shows a valid timestamp as relative time", () => {
    const wrapper = mount(MessageCard, {
      props: { title: "标题", timestamp: fiveMinutesAgo },
    });

    expect(wrapper.find(".card-time").text()).toBe("5分钟前");
  });

  it("shows an unparseable timestamp as-is", () => {
    const wrapper = mount(MessageCard, {
      props: { title: "标题", timestamp: "稍后更新" },
    });

    expect(wrapper.find(".card-time").text()).toBe("稍后更新");
  });

  it("renders the title as a new-tab link only when url is provided", () => {
    const linked = mount(MessageCard, {
      props: { title: "标题", url: "https://example.com/a" },
    });

    const link = linked.find("a.card-title");
    expect(link.exists()).toBe(true);
    expect(link.attributes("href")).toBe("https://example.com/a");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toContain("noopener");

    const plain = mount(MessageCard, { props: { title: "标题" } });
    expect(plain.find("a.card-title").exists()).toBe(false);
    expect(plain.find("h3.card-title").text()).toBe("标题");
  });

  it("renders the default and footer slots", () => {
    const wrapper = mount(MessageCard, {
      props: { title: "标题" },
      slots: {
        default: '<div class="custom-body">正文插槽</div>',
        footer: '<button class="slot-action">已读</button>',
      },
    });

    expect(wrapper.find(".custom-body").text()).toBe("正文插槽");
    expect(wrapper.find(".card-actions .slot-action").text()).toBe("已读");
  });

  it("omits the meta row when source and timestamp are both missing", () => {
    const withoutMeta = mount(MessageCard, { props: { title: "标题" } });
    expect(withoutMeta.find(".card-meta").exists()).toBe(false);

    const withMeta = mount(MessageCard, {
      props: { title: "标题", source: "Bloomberg" },
    });
    expect(withMeta.find(".card-meta").exists()).toBe(true);
  });
});
