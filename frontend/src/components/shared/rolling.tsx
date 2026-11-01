"use client";

import NumberFlow from "@number-flow/react";

// One number with optional words around it: "1,000.00", "+5.8 pts", "-0.19%", "in 29m".
const ONE_NUMBER = /^([^\d+\-−]*?)([+\-−]?)(\d[\d,]*(?:\.\d+)?)([^\d]*)$/;

/**
 * A figure that rolls digit by digit to its new value, the way iOS updates a price. It takes the text
 * the page already formatted and keeps it exactly: same decimals, grouping, sign and units. Anything that
 * is not a single number (a date, a range, a dash) is shown as it is.
 */
export function Rolling({ text }: { text: string | number }) {
  const s = String(text);
  const m = ONE_NUMBER.exec(s);
  if (!m) return <>{s}</>;
  const [, prefix, sign, digits, suffix] = m;
  const decimals = digits.includes(".") ? digits.split(".")[1].length : 0;
  const value = Number(digits.replace(/,/g, "")) * (sign === "-" || sign === "−" ? -1 : 1);
  return (
    <NumberFlow
      value={value}
      prefix={prefix || undefined}
      suffix={suffix || undefined}
      format={{
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
        useGrouping: digits.includes(","),
        signDisplay: sign === "+" ? "exceptZero" : "auto",
      }}
      className="num"
    />
  );
}
