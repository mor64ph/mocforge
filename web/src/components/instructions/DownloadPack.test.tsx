import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { Design, HealthResponse } from "@/lib/api/contract";
import { ApiError } from "@/lib/api/errors";
import { generateFor } from "@/lib/api/mock/engine";
import { DownloadPack } from "./DownloadPack";

/**
 * The transport is stubbed here rather than taken from the mock layer, which
 * is the exception in this suite. The component's entire job is turning two
 * transport outcomes into states a person can read - a capability probe and a
 * download that takes seconds - and the mock transport can only ever produce
 * one of them, because it reports no features by design.
 */
const transport = vi.hoisted(() => ({
  health: vi.fn(),
  getPackage: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: () => transport }));

function health(features: string[]): HealthResponse {
  return {
    status: "ok",
    catalogue: { sets: 28356, parts: 64649, updatedAt: "2026-09-17T00:00:00Z" },
    features,
  };
}

function aDesign(): Design {
  const design = generateFor(["10696-1"]).designs[0];
  if (!design) throw new Error("the fixture inventory yielded no design");
  return design;
}

const DESIGN = aDesign();
const SETS = ["10696-1"] as const;

function zip(bytes: number): Blob {
  return new Blob([new Uint8Array(bytes)], { type: "application/zip" });
}

/** A promise settled from the test, to hold the component in one state. */
function deferred<T>(): { promise: Promise<T>; settle: (value: T) => void } {
  let settle: (value: T) => void = () => {};
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

beforeAll(() => {
  // jsdom implements neither, and saving a file is the one thing this
  // component definitely does.
  URL.createObjectURL = vi.fn(() => "blob:pack");
  URL.revokeObjectURL = vi.fn();
});

beforeEach(() => {
  transport.health.mockReset();
  transport.getPackage.mockReset();
});

const downloadButton = () =>
  screen.getByRole("button", { name: /download the instruction pack/i });

describe("DownloadPack", () => {
  test("offers nothing when the service reports no renderer", async () => {
    transport.health.mockResolvedValue(health([]));
    render(<DownloadPack design={DESIGN} setNums={SETS} />);

    await waitFor(() =>
      expect(screen.getByText(/has no renderer/i)).toBeTruthy(),
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(transport.getPackage).not.toHaveBeenCalled();
  });

  test("treats an unreachable service as no offer rather than as an error", async () => {
    transport.health.mockRejectedValue(
      new ApiError("network", "unreachable", 0),
    );
    render(<DownloadPack design={DESIGN} setNums={SETS} />);

    await waitFor(() =>
      expect(screen.getByText(/has no renderer/i)).toBeTruthy(),
    );
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  test("keeps the button inert until the capability probe answers", async () => {
    transport.health.mockReturnValue(deferred<HealthResponse>().promise);
    render(<DownloadPack design={DESIGN} setNums={SETS} />);

    expect(downloadButton().hasAttribute("disabled")).toBe(true);
  });

  test("says what it is doing while the service renders every step", async () => {
    transport.health.mockResolvedValue(health(["designPackage"]));
    const pending = deferred<Blob>();
    transport.getPackage.mockReturnValue(pending.promise);

    const user = userEvent.setup();
    render(<DownloadPack design={DESIGN} setNums={SETS} />);
    await waitFor(() => expect(downloadButton().hasAttribute("disabled")).toBe(false));
    await user.click(downloadButton());

    expect(screen.getByRole("button", { name: /drawing every step/i })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/takes a few seconds/i);

    pending.settle(zip(1024));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toMatch(/^Saved /),
    );
  });

  test("saves the pack under the engine's own filename and reports its size", async () => {
    transport.health.mockResolvedValue(health(["designImage", "designPackage"]));
    transport.getPackage.mockResolvedValue(zip(463 * 1024));

    const user = userEvent.setup();
    render(<DownloadPack design={DESIGN} setNums={SETS} />);
    await waitFor(() => expect(downloadButton().hasAttribute("disabled")).toBe(false));
    await user.click(downloadButton());

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        `Saved mocforge_${DESIGN.archetype}${
          DESIGN.variant ? `-${DESIGN.variant}` : ""
        }_10696-1.zip (463 kB).`,
      ),
    );
    expect(transport.getPackage).toHaveBeenCalledWith(DESIGN);
    // Re-saving must not cost a second round trip for bytes already in hand.
    await user.click(screen.getByRole("button", { name: /save the pack again/i }));
    expect(transport.getPackage).toHaveBeenCalledTimes(1);
  });

  test("reports a failure in the service's own terms, announced", async () => {
    transport.health.mockResolvedValue(health(["designPackage"]));
    transport.getPackage.mockRejectedValue(
      new ApiError("render_failed", "this design could not be drawn", 500),
    );

    const user = userEvent.setup();
    render(<DownloadPack design={DESIGN} setNums={SETS} />);
    await waitFor(() => expect(downloadButton().hasAttribute("disabled")).toBe(false));
    await user.click(downloadButton());

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert.textContent).toMatch(/could not be drawn/i);
    // The affordance stays usable: a failed render may be a transient fault.
    expect(downloadButton().hasAttribute("disabled")).toBe(false);
  });
});
