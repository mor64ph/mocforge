import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { SetSummary } from "@/lib/api/contract";
import { SetSearch } from "./SetSearch";

/**
 * These run against the real mock transport rather than a hand-stubbed one, so
 * they also cover the contract behaviour the entry field depends on: search
 * ranking, and a bare set number resolving to its canonical form.
 */
function setup(options: { added?: string[]; disabled?: boolean } = {}) {
  const added = new Set(options.added ?? []);
  const onAdd = vi.fn<(set: SetSummary) => void>();
  render(
    <SetSearch
      onAdd={onAdd}
      isAdded={(setNum) => added.has(setNum)}
      disabled={options.disabled ?? false}
    />,
  );
  return { onAdd, user: userEvent.setup() };
}

const combobox = () => screen.getByRole("combobox", { name: /add a set you own/i });

describe("SetSearch", () => {
  test("labels the field and describes what it accepts", () => {
    setup();
    expect(combobox()).toBeTruthy();
    expect(screen.getByText(/42151 resolves to\s+42151-1/i)).toBeTruthy();
  });

  test("resolves a bare set number when submitted with Enter", async () => {
    const { onAdd, user } = setup();

    await user.type(combobox(), "42151");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    await user.keyboard("{Enter}");

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd.mock.calls[0]?.[0]).toMatchObject({
      setNum: "42151-1",
      name: "Bugatti Bolide",
      year: 2023,
      numParts: 905,
    });
  });

  test("clears the field after adding, so the next set can be typed straight away", async () => {
    const { user } = setup();

    await user.type(combobox(), "42151");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    await user.keyboard("{Enter}");

    expect(combobox()).toHaveProperty("value", "");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  test("finds sets by name", async () => {
    const { onAdd, user } = setup();

    await user.type(combobox(), "creative brick box");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(1));

    const names = screen.getAllByRole("option").map((option) => option.textContent);
    expect(names.some((name) => name?.includes("Medium Creative Brick Box"))).toBe(true);
    expect(names.some((name) => name?.includes("Large Creative Brick Box"))).toBe(true);
    expect(onAdd).not.toHaveBeenCalled();
  });

  test("is operable by keyboard alone, tracking the active option in ARIA", async () => {
    const { onAdd, user } = setup();

    await user.type(combobox(), "10");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(1));
    expect(combobox().getAttribute("aria-activedescendant")).toBeNull();

    await user.keyboard("{ArrowDown}{ArrowDown}");
    const options = screen.getAllByRole("option");
    expect(combobox().getAttribute("aria-activedescendant")).toBe(options[1]?.id);
    expect(options[1]?.getAttribute("aria-selected")).toBe("true");
    expect(options[0]?.getAttribute("aria-selected")).toBe("false");

    await user.keyboard("{Enter}");
    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  test("wraps from the first option to the last with ArrowUp", async () => {
    const { user } = setup();

    await user.type(combobox(), "10");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(1));

    await user.keyboard("{ArrowUp}");
    const options = screen.getAllByRole("option");
    expect(combobox().getAttribute("aria-activedescendant")).toBe(options.at(-1)?.id);
  });

  test("Escape closes the list, and closes it without clearing the query", async () => {
    const { user } = setup();

    await user.type(combobox(), "42151");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(combobox()).toHaveProperty("value", "42151");
    expect(combobox().getAttribute("aria-expanded")).toBe("false");

    await user.keyboard("{Escape}");
    expect(combobox()).toHaveProperty("value", "");
  });

  test("marks an already-declared set and refuses to add it twice", async () => {
    const { onAdd, user } = setup({ added: ["42151-1"] });

    await user.type(combobox(), "42151");
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());

    const option = screen.getAllByRole("option")[0];
    expect(option?.getAttribute("aria-disabled")).toBe("true");
    expect(option?.textContent).toContain("Already in your inventory");

    await user.keyboard("{Enter}");
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByText("42151-1 is already in your inventory.")).toBeTruthy();
  });

  test("says nothing matched rather than showing an empty list", async () => {
    const { user } = setup();

    await user.type(combobox(), "99999");
    await waitFor(() =>
      expect(screen.getByText("No sets match 99999.")).toBeTruthy(),
    );
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  test("explains itself instead of failing silently when at capacity", () => {
    setup({ disabled: true });

    expect(combobox()).toHaveProperty("disabled", true);
    expect(
      screen.getByText("Inventory is full at 10 sets. Remove one to add another."),
    ).toBeTruthy();
  });

  test("announces search state in a live region", async () => {
    const { user } = setup();
    const status = screen.getByText("", { selector: "[aria-live='polite']" });

    await user.type(combobox(), "Bugatti");
    expect(status.textContent).toBe("Searching the catalogue…");

    await waitFor(() => expect(status.textContent).toBe("1 set found."));
  });
});
