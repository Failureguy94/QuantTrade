import { afterEach, describe, expect, it, vi } from "vitest";

import * as navigation from "@/lib/navigation";

import {
  ApiError,
  describeDetail,
  errorMessage,
  sessionRequest,
  toApiError,
  unwrap,
  unwrapPage,
} from "./client";

function response(status: number, headers: Record<string, string> = {}): Response {
  return new Response(null, { status, headers });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("describeDetail", () => {
  it("passes a plain message through", () => {
    expect(describeDetail("Portfolio not found")).toBe("Portfolio not found");
  });

  it("turns field problems into one sentence", () => {
    const detail = [
      { loc: ["body", "password"], msg: "String should have at least 8 characters" },
      { loc: ["body"], msg: "Value error, start must be before end" },
    ];
    expect(describeDetail(detail)).toBe(
      "password: String should have at least 8 characters; start must be before end",
    );
  });

  it("reads a message nested in an object", () => {
    expect(describeDetail({ message: "Data failed validation", failures: [] })).toBe(
      "Data failed validation",
    );
  });

  it("falls back when there is nothing usable", () => {
    expect(describeDetail(undefined)).toBe("Something went wrong");
  });
});

describe("toApiError", () => {
  it("keeps the status, code and retry time", () => {
    const error = toApiError(response(429, { "retry-after": "42" }), {
      detail: "Too many requests",
      code: "rate_limited",
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "rate_limited", retryAfter: 42 });
    expect(errorMessage(error)).toBe("Too many requests (try again in 42s)");
  });

  it("describes long waits in minutes", () => {
    const error = toApiError(response(429, { "retry-after": "840" }), { detail: "Locked" });
    expect(errorMessage(error)).toBe("Locked (try again in 14 min)");
  });

  it("copes with a body that is not the API's error shape", () => {
    const error = toApiError(response(502), null);
    expect(error).toMatchObject({ status: 502, code: "error", retryAfter: null });
  });
});

describe("unwrap", () => {
  it("returns the data of a successful response", async () => {
    await expect(unwrap(Promise.resolve({ data: { id: 1 }, response: response(200) }))).resolves.toEqual({
      id: 1,
    });
  });

  it("throws the API's error", async () => {
    const failed = Promise.resolve({
      error: { detail: "Strategy not found", code: "not_found" },
      response: response(404),
    });
    await expect(unwrap(failed)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "Strategy not found",
    });
  });

  it("sends a lost session to sign in, remembering where the user was", async () => {
    const leave = vi.spyOn(navigation, "leaveTo").mockImplementation(() => {});
    window.history.pushState({}, "", "/portfolios/abc?tab=orders");
    const failed = Promise.resolve({ error: { detail: "Expired" }, response: response(401) });
    await expect(unwrap(failed)).rejects.toBeInstanceOf(ApiError);
    expect(leave).toHaveBeenCalledWith("/login?next=%2Fportfolios%2Fabc%3Ftab%3Dorders");
  });

  it("does not redirect from the sign-in page itself", async () => {
    const leave = vi.spyOn(navigation, "leaveTo").mockImplementation(() => {});
    window.history.pushState({}, "", "/login");
    const failed = Promise.resolve({ error: { detail: "Nope" }, response: response(401) });
    await expect(unwrap(failed)).rejects.toBeInstanceOf(ApiError);
    expect(leave).not.toHaveBeenCalled();
  });
});

describe("unwrapPage", () => {
  it("reads the total from the header", async () => {
    const page = await unwrapPage(
      Promise.resolve({ data: [1, 2], response: response(200, { "x-total-count": "57" }) }),
    );
    expect(page).toEqual({ items: [1, 2], total: 57 });
  });

  it("falls back to the number of items when there is no header", async () => {
    const page = await unwrapPage(Promise.resolve({ data: [1, 2, 3], response: response(200) }));
    expect(page.total).toBe(3);
  });

  it("throws on failure", async () => {
    const failed = Promise.resolve({ error: { detail: "Boom" }, response: response(500) });
    await expect(unwrapPage(failed)).rejects.toMatchObject({ status: 500, message: "Boom" });
  });
});

describe("sessionRequest", () => {
  it("posts JSON to this app's session routes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await sessionRequest("login", { email: "a@b.c", password: "x" });
    expect(fetchMock).toHaveBeenCalledWith("/api/session/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: "a@b.c", password: "x" }),
    });
  });

  it("throws the API's error, even when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({ detail: "Incorrect email or password", code: "invalid_credentials" }, { status: 401 }),
      ),
    );
    await expect(sessionRequest("login", {})).rejects.toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 502 })));
    await expect(sessionRequest("login", {})).rejects.toMatchObject({ status: 502 });
  });
});
