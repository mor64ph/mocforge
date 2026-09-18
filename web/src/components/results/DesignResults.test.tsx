import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { GenerateResponse } from "@/lib/api/contract";
import { generateFor, inventoryFor } from "@/lib/api/mock/engine";
import { DesignResults } from "./DesignResults";

/**
 * Built from the real fixture engine rather than hand-written literals, so the
 * assertions are made against the shape the transport actually produces —
 * including the inventory a design's rationale is derived from.
 */
function responseFor(setNums: string[]): GenerateResponse {
  const { designs, withheld } = generateFor(setNums);
  return { inventory: inventoryFor(setNums), designs, withheld, elapsedMs: 1487 };
}

/** A Classic brick box: plenty of bricks and plates, so designs come back. */
const POPULATED = responseFor(["10696-1"]);
/** Specialised shapes only: nothing tileable, so every variant is withheld. */
const EMPTY = responseFor(["31088-1"]);
/**
 * An inventory that runs short mid-build, so at least one design carries a
 * builder note and the diagnostic behind it. Most single-set inventories
 * complete cleanly and would make the note assertions vacuous.
 */
const NOTED = responseFor(["31058-1"]);

describe("DesignResults with designs", () => {
  test("the fixture it relies on does return designs", () => {
    expect(POPULATED.designs.length).toBeGreaterThan(0);
  });

  test("announces the outcome in a status region", () => {
    render(
      <DesignResults response={POPULATED} fromCache={false} onRegenerate={vi.fn()} />,
    );

    expect(screen.getByRole("status").textContent).toBe(
      `${POPULATED.designs.length} designs ready, ${POPULATED.withheld.length} withheld.`,
    );
  });

  test("renders every design with its title, piece count and rationale", () => {
    render(
      <DesignResults response={POPULATED} fromCache={false} onRegenerate={vi.fn()} />,
    );

    for (const design of POPULATED.designs) {
      const heading = screen.getByRole("heading", { name: design.title });
      expect(heading).toBeTruthy();
    }

    expect(
      screen.getAllByRole("heading", { name: "Why it suits this inventory" }),
    ).toHaveLength(POPULATED.designs.length);

    const first = POPULATED.designs[0];
    expect(first).toBeDefined();
    expect(screen.getByText(String(first?.pieceCount))).toBeTruthy();
  });

  test("links each design to its instructions", () => {
    render(
      <DesignResults response={POPULATED} fromCache={false} onRegenerate={vi.fn()} />,
    );

    const first = POPULATED.designs[0];
    expect(first).toBeDefined();
    const links = screen
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(links).toContain(`/designs/${encodeURIComponent(first?.id ?? "")}`);
  });

  test("offers a regenerate action", async () => {
    const onRegenerate = vi.fn();
    const user = userEvent.setup();
    render(
      <DesignResults
        response={POPULATED}
        fromCache={false}
        onRegenerate={onRegenerate}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Generate again" }));
    expect(onRegenerate).toHaveBeenCalledOnce();
  });

  test("says when a result is the stored one for an unchanged inventory", () => {
    const { unmount } = render(
      <DesignResults response={POPULATED} fromCache={false} onRegenerate={vi.fn()} />,
    );
    expect(screen.queryByText(/Showing the last result/)).toBeNull();
    unmount();

    render(
      <DesignResults response={POPULATED} fromCache onRegenerate={vi.fn()} />,
    );
    expect(screen.getByText("Showing the last result for this inventory.")).toBeTruthy();
  });
});

describe("DesignResults with a design that carries notes", () => {
  test("the fixture it relies on really does carry a note and a diagnostic", () => {
    // Without this guard the two assertions below pass vacuously - which is
    // exactly what happened on the first attempt, against an inventory whose
    // designs all completed cleanly.
    expect(NOTED.designs.some((design) => design.builderNotes.length > 0)).toBe(true);
    expect(NOTED.designs.some((design) => design.warnings.length > 0)).toBe(true);
  });

  test("shows the builder notes under a heading that matches the PDF", () => {
    render(<DesignResults response={NOTED} fromCache={false} onRegenerate={vi.fn()} />);

    for (const design of NOTED.designs) {
      for (const note of design.builderNotes) {
        expect(screen.getAllByText(note).length).toBeGreaterThan(0);
      }
    }
    expect(
      screen.getAllByText("Before you start").length,
    ).toBeGreaterThan(0);
  });

  test("never shows the raw diagnostics the notes were translated from", () => {
    // CONTRACT.md behaviour requirement 2: `warnings` is for debugging the
    // generator. "20.0 LDU vs ground plane 0" in front of a user reads as a
    // fault in a design the API has already certified buildable.
    render(<DesignResults response={NOTED} fromCache={false} onRegenerate={vi.fn()} />);

    for (const design of NOTED.designs) {
      for (const warning of design.warnings) {
        expect(screen.queryByText(warning)).toBeNull();
      }
    }
    expect(screen.queryByText("Quality notes from the generator")).toBeNull();
  });
});

describe("DesignResults with no designs", () => {
  test("the fixture it relies on really does yield nothing", () => {
    expect(EMPTY.designs).toHaveLength(0);
    expect(EMPTY.withheld.length).toBeGreaterThan(0);
  });

  test("presents the outcome as normal, not as a failure", () => {
    render(<DesignResults response={EMPTY} fromCache={false} onRegenerate={vi.fn()} />);

    expect(screen.getByRole("status").textContent).toContain(
      "No designs are possible from this inventory",
    );
    expect(
      screen.getByRole("heading", { name: "Nothing is buildable from these parts" }),
    ).toBeTruthy();
    // Nothing on this page may be announced as an error.
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  test("shows every withheld reason verbatim, since that is the only guidance", () => {
    render(<DesignResults response={EMPTY} fromCache={false} onRegenerate={vi.fn()} />);

    for (const entry of EMPTY.withheld) {
      expect(screen.getByText(entry.reason)).toBeTruthy();
    }
  });

  test("names the archetypes that were tried and what each one wanted", () => {
    render(<DesignResults response={EMPTY} fromCache={false} onRegenerate={vi.fn()} />);

    expect(screen.getByRole("heading", { name: /Brick building/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Stepped plate sculpture/ })).toBeTruthy();
    expect(screen.getAllByText(/Wants brick-height material/).length).toBeGreaterThan(0);
  });

  test("offers a way forward instead of a dead end", () => {
    render(<DesignResults response={EMPTY} fromCache={false} onRegenerate={vi.fn()} />);

    expect(screen.getByRole("link", { name: "Add another set" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate again" })).toBeTruthy();
  });

  test("renders no design cards", () => {
    render(<DesignResults response={EMPTY} fromCache={false} onRegenerate={vi.fn()} />);

    expect(
      screen.queryByRole("heading", { name: "Why it suits this inventory" }),
    ).toBeNull();
  });
});
