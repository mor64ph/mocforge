import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { SetSummary } from "@/lib/api/contract";
import { InventoryChips } from "./InventoryChips";

const BUGATTI: SetSummary = {
  setNum: "42151-1",
  name: "Bugatti Bolide",
  year: 2023,
  themeId: 1,
  themeName: "Technic",
  numParts: 905,
  imgUrl: null,
};

const UNDATED: SetSummary = {
  setNum: "10696-1",
  name: "Medium Creative Brick Box",
  year: null,
  themeId: 52,
  themeName: "Classic",
  numParts: 0,
  imgUrl: null,
};

describe("InventoryChips", () => {
  test("shows name, number, year and piece count for each set", () => {
    render(<InventoryChips sets={[BUGATTI]} onRemove={vi.fn()} />);

    expect(screen.getByText("Bugatti Bolide")).toBeTruthy();
    const chip = screen.getByRole("listitem").textContent;
    expect(chip).toContain("42151-1");
    expect(chip).toContain("2023");
    expect(chip).toContain("905 pieces");
  });

  test("says so rather than printing a zero when the catalogue lacks the counts", () => {
    render(<InventoryChips sets={[UNDATED]} onRemove={vi.fn()} />);

    const chip = screen.getByRole("listitem").textContent;
    expect(chip).toContain("piece count unknown");
    expect(chip).not.toContain("0 pieces");
  });

  test("gives every remove button a name that identifies its set", () => {
    render(<InventoryChips sets={[BUGATTI, UNDATED]} onRemove={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: "Remove Bugatti Bolide, set 42151-1" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Remove Medium Creative Brick Box, set 10696-1",
      }),
    ).toBeTruthy();
  });

  test("removes by set number, by keyboard as well as by pointer", async () => {
    const onRemove = vi.fn();
    const user = userEvent.setup();
    render(<InventoryChips sets={[BUGATTI, UNDATED]} onRemove={onRemove} />);

    await user.tab();
    await user.tab();
    await user.keyboard("{Enter}");

    expect(onRemove).toHaveBeenCalledExactlyOnceWith("10696-1");
  });

  test("renders the sets as a list, one item each", () => {
    render(<InventoryChips sets={[BUGATTI, UNDATED]} onRemove={vi.fn()} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
