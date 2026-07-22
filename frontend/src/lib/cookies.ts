export interface CookieImportItem {
  name?: string;
  cookie: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function cookieToHeaderString(value: unknown): string {
  if (typeof value === "string") {
    const text = value.trim();
    return text.toLowerCase().startsWith("cookie:") ? text.slice(7).trim() : text;
  }
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item.trim();
      if (!isRecord(item)) return "";
      const name = String(item.name || "").trim();
      return name ? `${name}=${String(item.value ?? "").trim()}` : "";
    }).filter(Boolean).join("; ");
  }
  if (isRecord(value)) {
    if (Array.isArray(value.cookies)) return cookieToHeaderString(value.cookies);
    if (value.cookie !== undefined) return cookieToHeaderString(value.cookie);
    if (value.name !== undefined && value.value !== undefined) {
      return cookieToHeaderString([value]);
    }
  }
  return "";
}

function namedItem(value: unknown, index?: number): CookieImportItem {
  if (typeof value === "string") {
    const cookie = cookieToHeaderString(value);
    if (!cookie) throw new Error(index === undefined ? "Cookie 内容为空" : `第 ${index + 1} 项 Cookie 为空`);
    return { cookie };
  }
  if (!isRecord(value)) throw new Error(`第 ${(index ?? 0) + 1} 项格式无效`);
  const cookie = cookieToHeaderString(value.cookie ?? value.cookies ?? value);
  if (!cookie) throw new Error(index === undefined ? "Cookie 内容为空" : `第 ${index + 1} 项缺少 Cookie`);
  const name = String(value.name || value.email || "").trim();
  return { cookie, ...(name ? { name } : {}) };
}

export function parseCookieValue(value: unknown): CookieImportItem[] {
  if (Array.isArray(value)) {
    const isBrowserCookieArray = value.length > 0 && value.every(
      (item) => isRecord(item) && "name" in item && "value" in item
    );
    if (isBrowserCookieArray) {
      const cookie = cookieToHeaderString(value);
      return cookie ? [{ cookie }] : [];
    }
    return value.map((item, index) => namedItem(item, index));
  }
  if (isRecord(value) && Array.isArray(value.items)) return parseCookieValue(value.items);
  return [namedItem(value)];
}

export function parseCookieText(text: string): CookieImportItem[] {
  const input = text.trim();
  if (!input) return [];
  try {
    return parseCookieValue(JSON.parse(input));
  } catch (error) {
    if (error instanceof SyntaxError) return parseCookieValue(input);
    throw error;
  }
}

export async function parseCookieFiles(files: File[]): Promise<CookieImportItem[]> {
  const result: CookieImportItem[] = [];
  for (const file of files) {
    const items = parseCookieText(await file.text());
    const fallbackName = file.name.replace(/\.(json|txt)$/i, "").trim();
    for (const item of items) {
      result.push({ ...item, name: item.name || fallbackName || undefined });
    }
  }
  return result;
}
