import { execSync } from "node:child_process";

// How many workers the page's tests run on. The tests barely wait (323 browser tests summed 284 s and ran in 2.4 minutes
// on two workers), so the wall time halves with each doubling. Four workers when the Mac has 2 GB of swap free; two while a
// model job holds the memory. TEST_WORKERS sets it.
export function pickTestWorkers(): number {
  if (process.env.TEST_WORKERS) return Number(process.env.TEST_WORKERS);
  try {
    const free = /free = ([0-9.,]+)M/.exec(execSync("sysctl -n vm.swapusage").toString())?.[1] ?? "0";
    return Number.parseFloat(free.replace(",", ".")) >= 2048 ? 4 : 2;
  } catch {
    return 2;
  }
}
