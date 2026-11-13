/**
 * Leave the page with a full load instead of a client-side transition.
 *
 * Used only when the session changes (sign in, sign out, account deleted, session
 * lost). A full load makes every request after it carry the new cookies, and drops
 * everything the previous user's session had cached in memory.
 */
export function leaveTo(path: string): void {
  window.location.assign(path);
}
