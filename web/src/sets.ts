// A copy of the set with the value taken out when it was in, put in when it was not: what a checkbox's click does to
// the set of ticked rows
export function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}
