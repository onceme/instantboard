/**
 * Pagination control: totalPages math, page-number collapsing (first/last
 * pages stay, the middle folds into ellipses once there are more than 7
 * pages), update:page emissions, and disabled states (current page +
 * boundaries).
 */
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";

import Pagination from "@/components/common/Pagination.vue";

function mountPagination(props: {
  page: number;
  total: number;
  pageSize?: number;
}): VueWrapper {
  return mount(Pagination, {
    props: { pageSize: 10, ...props },
  });
}

function pageLabels(wrapper: VueWrapper): string[] {
  return wrapper.findAll(".page-btn").map((btn) => btn.text());
}

describe("Pagination", () => {
  it("computes totalPages from total/pageSize and never goes below 1", () => {
    expect(mountPagination({ page: 1, total: 95 }).vm.totalPages).toBe(10);
    expect(
      mountPagination({ page: 1, total: 91, pageSize: 13 }).vm.totalPages,
    ).toBe(7);
    expect(mountPagination({ page: 1, total: 0 }).vm.totalPages).toBe(1);
    expect(
      mountPagination({ page: 1, total: 42, pageSize: 0 }).vm.totalPages,
    ).toBe(1);
  });

  it("renders every page number when there are at most 7 pages", () => {
    const wrapper = mountPagination({ page: 1, total: 70 });

    expect(pageLabels(wrapper)).toEqual(["1", "2", "3", "4", "5", "6", "7"]);
    expect(wrapper.findAll(".page-ellipsis")).toHaveLength(0);
  });

  it("collapses the middle pages while keeping first and last", () => {
    // 20 pages, somewhere in the middle: 1 … 4 5 6 … 20
    const middle = mountPagination({ page: 5, total: 200 });
    expect(pageLabels(middle)).toEqual(["1", "4", "5", "6", "20"]);
    expect(middle.findAll(".page-ellipsis")).toHaveLength(2);

    // Near the start: 1 2 3 4 … 20
    const first = mountPagination({ page: 1, total: 200 });
    expect(pageLabels(first)).toEqual(["1", "2", "3", "4", "20"]);
    expect(first.findAll(".page-ellipsis")).toHaveLength(1);

    // Near the end: 1 … 17 18 19 20
    const last = mountPagination({ page: 20, total: 200 });
    expect(pageLabels(last)).toEqual(["1", "17", "18", "19", "20"]);
    expect(last.findAll(".page-ellipsis")).toHaveLength(1);
  });

  it("emits update:page when clicking page numbers, prev and next", async () => {
    const wrapper = mountPagination({ page: 5, total: 200 });

    await wrapper.findAll(".page-btn")[1].trigger("click"); // "4"
    expect(wrapper.emitted("update:page")).toEqual([[4]]);

    await wrapper.find(".nav-prev").trigger("click");
    await wrapper.find(".nav-next").trigger("click");
    expect(wrapper.emitted("update:page")).toEqual([[4], [4], [6]]);
  });

  it("disables the current page button", () => {
    const wrapper = mountPagination({ page: 5, total: 200 });

    const current = wrapper.find(".page-btn.active");
    expect(current.text()).toBe("5");
    expect(current.attributes("disabled")).toBeDefined();
  });

  it("disables prev on the first page and next on the last page", () => {
    const first = mountPagination({ page: 1, total: 30 });
    expect(first.find(".nav-prev").attributes("disabled")).toBeDefined();
    expect(first.find(".nav-next").attributes("disabled")).toBeUndefined();

    const last = mountPagination({ page: 3, total: 30 });
    expect(last.find(".nav-next").attributes("disabled")).toBeDefined();
    expect(last.find(".nav-prev").attributes("disabled")).toBeUndefined();
  });

  it("disables both prev and next on a single page", () => {
    const wrapper = mountPagination({ page: 1, total: 0 });

    expect(wrapper.find(".nav-prev").attributes("disabled")).toBeDefined();
    expect(wrapper.find(".nav-next").attributes("disabled")).toBeDefined();
    expect(pageLabels(wrapper)).toEqual(["1"]);
  });
});
