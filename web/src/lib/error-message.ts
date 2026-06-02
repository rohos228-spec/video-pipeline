import { ApiError, formatApiError } from "@/lib/api";

/** Человекочитаемое сообщение для toast / alert из любой ошибки API или unknown. */
export function errorMessageFromUnknown(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message || formatApiError(err.detail);
  }
  if (err instanceof Error) {
    return err.message || String(err);
  }
  if (typeof err === "string") {
    return err;
  }
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}
