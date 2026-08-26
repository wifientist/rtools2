/**
 * Minimal .xlsx writer — enough to emit a multi-sheet workbook, nothing more.
 *
 * Hand-rolled rather than pulling in SheetJS: the only build on npm is 0.18.5,
 * which carries a known prototype-pollution advisory in its *parser*. We only
 * ever write files, so the risk is notional, but a flagged dependency is not
 * worth it for ~150 lines. An .xlsx is a ZIP of XML parts; strings are written
 * inline so there is no shared-string table to maintain.
 */

export type Cell = string | number | null | undefined;

export interface Sheet {
    name: string;
    rows: Cell[][];
}

// --- ZIP (stored, no compression) ---------------------------------------

let crcTable: Uint32Array | null = null;
function crc32(bytes: Uint8Array): number {
    if (!crcTable) {
        crcTable = new Uint32Array(256);
        for (let i = 0; i < 256; i++) {
            let c = i;
            for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
            crcTable[i] = c >>> 0;
        }
    }
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
        crc = crcTable[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
}

interface Entry { name: string; data: Uint8Array; crc: number; offset: number }

function u16(n: number) { return [n & 0xff, (n >>> 8) & 0xff]; }
function u32(n: number) {
    return [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];
}

function zip(files: { name: string; text: string }[]): Blob {
    const enc = new TextEncoder();
    const chunks: number[][] = [];
    const entries: Entry[] = [];
    let offset = 0;

    for (const f of files) {
        const data = enc.encode(f.text);
        const nameBytes = enc.encode(f.name);
        const crc = crc32(data);
        const header = [
            ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0),
            ...u16(0), ...u16(0),                 // no timestamp — keeps output stable
            ...u32(crc), ...u32(data.length), ...u32(data.length),
            ...u16(nameBytes.length), ...u16(0),
        ];
        chunks.push(header, [...nameBytes], [...data]);
        entries.push({ name: f.name, data, crc, offset });
        offset += header.length + nameBytes.length + data.length;
    }

    const dirStart = offset;
    let dirSize = 0;
    for (const e of entries) {
        const nameBytes = enc.encode(e.name);
        const rec = [
            ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0),
            ...u16(0), ...u16(0),
            ...u32(e.crc), ...u32(e.data.length), ...u32(e.data.length),
            ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
            ...u32(0), ...u32(e.offset),
        ];
        chunks.push(rec, [...nameBytes]);
        dirSize += rec.length + nameBytes.length;
    }
    chunks.push([
        ...u32(0x06054b50), ...u16(0), ...u16(0),
        ...u16(entries.length), ...u16(entries.length),
        ...u32(dirSize), ...u32(dirStart), ...u16(0),
    ]);

    const total = chunks.reduce((s, c) => s + c.length, 0);
    const out = new Uint8Array(total);
    let p = 0;
    for (const c of chunks) { out.set(c, p); p += c.length; }
    return new Blob([out], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
}

// --- XLSX parts ----------------------------------------------------------

const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
     // Control characters are illegal in XML 1.0 and corrupt the whole file.
     .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");

function colRef(i: number): string {
    let s = "";
    for (let n = i + 1; n > 0; ) {
        const r = (n - 1) % 26;
        s = String.fromCharCode(65 + r) + s;
        n = Math.floor((n - 1) / 26);
    }
    return s;
}

/** Excel rejects these in sheet names, and silently truncates past 31 chars. */
function safeName(name: string, taken: Set<string>): string {
    let base = (name.replace(/[\[\]:*?/\\]/g, " ").trim() || "Sheet").slice(0, 31);
    let out = base, n = 2;
    while (taken.has(out.toLowerCase())) {
        const suffix = ` (${n++})`;
        out = base.slice(0, 31 - suffix.length) + suffix;
    }
    taken.add(out.toLowerCase());
    return out;
}

function sheetXml(rows: Cell[][]): string {
    const body = rows.map((row, r) => {
        const cells = row.map((v, c) => {
            if (v === null || v === undefined || v === "") return "";
            const ref = `${colRef(c)}${r + 1}`;
            if (typeof v === "number" && Number.isFinite(v)) {
                return `<c r="${ref}"><v>${v}</v></c>`;
            }
            return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${esc(
                String(v),
            )}</t></is></c>`;
        }).join("");
        return `<row r="${r + 1}">${cells}</row>`;
    }).join("");
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
        `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${body}</sheetData></worksheet>`;
}

export function buildWorkbook(sheets: Sheet[]): Blob {
    const taken = new Set<string>();
    const named = sheets.map((s) => ({ ...s, name: safeName(s.name, taken) }));

    const files = [
        {
            name: "[Content_Types].xml",
            text: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
                `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
                `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
                `<Default Extension="xml" ContentType="application/xml"/>` +
                `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
                named.map((_, i) =>
                    `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
                ).join("") +
                `</Types>`,
        },
        {
            name: "_rels/.rels",
            text: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
                `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
                `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
                `</Relationships>`,
        },
        {
            name: "xl/workbook.xml",
            text: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
                `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ` +
                `xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>` +
                named.map((s, i) =>
                    `<sheet name="${esc(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`,
                ).join("") +
                `</sheets></workbook>`,
        },
        {
            name: "xl/_rels/workbook.xml.rels",
            text: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
                `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
                named.map((_, i) =>
                    `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`,
                ).join("") +
                `</Relationships>`,
        },
        ...named.map((s, i) => ({
            name: `xl/worksheets/sheet${i + 1}.xml`,
            text: sheetXml(s.rows),
        })),
    ];
    return zip(files);
}

export function downloadBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
