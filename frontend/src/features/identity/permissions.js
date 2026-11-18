// Presentation helpers only. They decide what to SHOW; the backend decides what is ALLOWED
// and re-checks every permission on every request.

export const can = (user, permission) => Boolean(user) && user.permissions.includes(permission);
export const isOwner = (user) => Boolean(user) && user.roles.includes('owner');
export const hasSpecial = (user) => Boolean(user) && (user.tiers.includes('special') || isOwner(user));

/** Access levels used by the route table and navigation: public < user < special < owner. */
export function satisfies(user, access) {
  switch (access) {
    case 'public': return true;
    case 'user': return Boolean(user);
    case 'special': return hasSpecial(user);
    case 'owner': return isOwner(user);
    default: return false; // unknown levels fail closed
  }
}
