import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Optimistic redirects based on whether a session cookie exists. This is a
 * convenience, not the access check: every API call is authorised by the API.
 */

const SIGNED_OUT_ONLY = ["/login", "/register", "/forgot-password", "/reset-password"];
const PUBLIC = ["/", "/verify-email", ...SIGNED_OUT_ONLY];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = request.cookies.has("qt_refresh");

  if (signedIn && SIGNED_OUT_ONLY.includes(pathname)) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }
  if (!signedIn && !PUBLIC.includes(pathname)) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Pages only: not the API routes, Next's own assets or files with an extension.
  matcher: ["/((?!api/|_next/|.*\\..*).*)"],
};
