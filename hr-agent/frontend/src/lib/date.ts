const IST = "Asia/Kolkata";

export function formatIST(
  date: string | Date,
  opts?: Intl.DateTimeFormatOptions,
): string {
  const d = typeof date === "string" ? new Date(date) : date;
  return d.toLocaleString("en-IN", { timeZone: IST, ...opts });
}

export function formatISTDate(
  date: string | Date,
  opts?: Intl.DateTimeFormatOptions,
): string {
  const d = typeof date === "string" ? new Date(date) : date;
  return d.toLocaleDateString("en-IN", { timeZone: IST, ...opts });
}

export function formatISTShort(date: string | Date): string {
  return formatIST(date, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
