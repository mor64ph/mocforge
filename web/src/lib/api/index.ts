import type { MocForgeApi } from "./client";
import { createHttpApi } from "./http";
import { createMockApi } from "./mock";

export type ApiMode = "mock" | "live";

const setting = (process.env.NEXT_PUBLIC_MOCFORGE_API ?? "mock").trim();

/**
 * The single switch between the mock layer and the real service.
 *
 * `NEXT_PUBLIC_MOCFORGE_API` unset or `mock` uses the in-process mock; any
 * http(s) origin uses the real transport. Nothing else in the client branches
 * on this.
 */
export const API_MODE: ApiMode = /^https?:\/\//i.test(setting) ? "live" : "mock";
export const API_ORIGIN: string | null = API_MODE === "live" ? setting : null;

let instance: MocForgeApi | null = null;

/** The transport, created once so the mock keeps the design ids it issued. */
export function api(): MocForgeApi {
  if (!instance) {
    instance =
      API_ORIGIN === null ? createMockApi() : createHttpApi(API_ORIGIN);
  }
  return instance;
}

export type { MocForgeApi } from "./client";
