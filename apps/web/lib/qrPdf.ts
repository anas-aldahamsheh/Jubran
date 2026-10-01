/**
 * Printable table QR cards as a real PDF download, without third-party libraries.
 *
 * Each A4 page is drawn on a canvas (so Arabic text is shaped by the browser),
 * then embedded as a lossless image in a minimal PDF 1.4 document.
 */
import { encodeQr } from "@/lib/qr";

export interface TableQrPrintItem {
  tableNumber: string;
  url: string;
}

const A4_WIDTH_PT = 595.28;
const A4_HEIGHT_PT = 841.89;
const RENDER_DPI = 200;
const PAGE_WIDTH_PX = Math.round((210 / 25.4) * RENDER_DPI);
const PAGE_HEIGHT_PX = Math.round((297 / 25.4) * RENDER_DPI);

const BRAND_NAVY = "#233064";
const BRAND_ACCENT = "#c2673b";
const TEXT_DARK = "#17203d";
const TEXT_MUTED = "#5b6278";
const FONT_STACK = `"Cairo", "Segoe UI", Tahoma, "Noto Sans Arabic", Arial, sans-serif`;

async function loadImage(src: string): Promise<HTMLImageElement | null> {
  try {
    const image = new Image();
    image.src = src;
    await image.decode();
    return image;
  } catch {
    return null;
  }
}

function drawCenteredText(ctx: CanvasRenderingContext2D, text: string, y: number, font: string,
                          color: string, direction: CanvasDirection = "rtl"): void {
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.direction = direction;
  ctx.fillText(text, PAGE_WIDTH_PX / 2, y);
}

function drawRoundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function renderCardPage(item: TableQrPrintItem, logo: HTMLImageElement | null): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = PAGE_WIDTH_PX;
  canvas.height = PAGE_HEIGHT_PX;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas is not supported");

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, PAGE_WIDTH_PX, PAGE_HEIGHT_PX);

  // Card frame.
  const margin = 110;
  ctx.lineWidth = 6;
  ctx.strokeStyle = BRAND_NAVY;
  drawRoundedRect(ctx, margin, margin, PAGE_WIDTH_PX - margin * 2, PAGE_HEIGHT_PX - margin * 2, 60);
  ctx.stroke();

  let y = margin + 150;
  if (logo) {
    const logoWidth = 640;
    const logoHeight = Math.round((logo.naturalHeight / logo.naturalWidth) * logoWidth);
    ctx.drawImage(logo, (PAGE_WIDTH_PX - logoWidth) / 2, y - 40, logoWidth, logoHeight);
    y += logoHeight + 40;
  } else {
    drawCenteredText(ctx, "جبران", y + 80, `800 120px ${FONT_STACK}`, BRAND_NAVY);
    y += 180;
  }

  // Accent divider.
  ctx.fillStyle = BRAND_ACCENT;
  ctx.fillRect(PAGE_WIDTH_PX / 2 - 220, y, 440, 5);
  y += 150;

  drawCenteredText(ctx, `طاولة ${item.tableNumber}`, y, `800 128px ${FONT_STACK}`, BRAND_NAVY);
  y += 90;
  drawCenteredText(ctx, `Table ${item.tableNumber}`, y, `600 60px ${FONT_STACK}`, TEXT_MUTED, "ltr");
  y += 70;

  // QR code with a whole number of pixels per module for sharp printing.
  const qr = encodeQr(item.url, "M");
  const quietZone = 4;
  const totalModules = qr.size + quietZone * 2;
  const modulePx = Math.floor(1000 / totalModules);
  const qrPx = modulePx * totalModules;
  const qrX = Math.round((PAGE_WIDTH_PX - qrPx) / 2);
  const qrY = y;

  ctx.lineWidth = 4;
  ctx.strokeStyle = "#e2ddd3";
  drawRoundedRect(ctx, qrX - 30, qrY - 30, qrPx + 60, qrPx + 60, 40);
  ctx.stroke();

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(qrX, qrY, qrPx, qrPx);
  ctx.fillStyle = "#000000";
  for (let row = 0; row < qr.size; row++) {
    for (let col = 0; col < qr.size; col++) {
      if (qr.modules[row][col]) {
        ctx.fillRect(qrX + (col + quietZone) * modulePx, qrY + (row + quietZone) * modulePx, modulePx, modulePx);
      }
    }
  }
  y = qrY + qrPx + 150;

  drawCenteredText(ctx, "امسح الرمز بكاميرا هاتفك لعرض القائمة والطلب", y, `700 58px ${FONT_STACK}`, TEXT_DARK);
  y += 80;
  drawCenteredText(ctx, "Scan with your phone camera to view the menu and order", y, `500 42px ${FONT_STACK}`, TEXT_MUTED, "ltr");

  return canvas;
}

async function deflate(bytes: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

interface PdfImage {
  data: Uint8Array<ArrayBuffer>;
  width: number;
  height: number;
  filter: "FlateDecode" | "DCTDecode";
}

async function canvasToPdfImage(canvas: HTMLCanvasElement): Promise<PdfImage> {
  const { width, height } = canvas;
  if (typeof CompressionStream !== "undefined") {
    const ctx = canvas.getContext("2d");
    if (ctx) {
      const rgba = ctx.getImageData(0, 0, width, height).data;
      const rgb = new Uint8Array(width * height * 3);
      for (let src = 0, dst = 0; src < rgba.length; src += 4, dst += 3) {
        rgb[dst] = rgba[src];
        rgb[dst + 1] = rgba[src + 1];
        rgb[dst + 2] = rgba[src + 2];
      }
      return { data: await deflate(rgb), width, height, filter: "FlateDecode" };
    }
  }
  // Older browsers: high-quality JPEG is still reliably scannable.
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((result) => (result ? resolve(result) : reject(new Error("Image export failed"))), "image/jpeg", 0.95);
  });
  return { data: new Uint8Array(await blob.arrayBuffer()), width, height, filter: "DCTDecode" };
}

function assemblePdf(images: PdfImage[]): Blob {
  const encoder = new TextEncoder();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  const offsets: number[] = [];
  let length = 0;
  const push = (chunk: Uint8Array<ArrayBuffer>) => {
    chunks.push(chunk);
    length += chunk.length;
  };
  const pushText = (text: string) => push(encoder.encode(text) as Uint8Array<ArrayBuffer>);
  const beginObject = (id: number) => {
    offsets[id] = length;
    pushText(`${id} 0 obj\n`);
  };

  // Object layout: 1 catalog, 2 page tree, then (page, content, image) per page.
  const pageIds = images.map((_, index) => 3 + index * 3);
  pushText("%PDF-1.4\n");
  push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a])); // Binary marker comment.

  beginObject(1);
  pushText("<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
  beginObject(2);
  pushText(`<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${images.length} >>\nendobj\n`);

  images.forEach((image, index) => {
    const pageId = pageIds[index];
    const contentId = pageId + 1;
    const imageId = pageId + 2;
    const content = `q ${A4_WIDTH_PT} 0 0 ${A4_HEIGHT_PT} 0 0 cm /Im0 Do Q\n`;

    beginObject(pageId);
    pushText(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${A4_WIDTH_PT} ${A4_HEIGHT_PT}] `
      + `/Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>\nendobj\n`);

    beginObject(contentId);
    pushText(`<< /Length ${content.length} >>\nstream\n${content}endstream\nendobj\n`);

    beginObject(imageId);
    pushText(`<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} `
      + `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /${image.filter} /Length ${image.data.length} >>\nstream\n`);
    push(image.data);
    pushText("\nendstream\nendobj\n");
  });

  const objectCount = 2 + images.length * 3;
  const xrefOffset = length;
  let xref = `xref\n0 ${objectCount + 1}\n0000000000 65535 f \n`;
  for (let id = 1; id <= objectCount; id++) {
    xref += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  pushText(xref);
  pushText(`trailer\n<< /Size ${objectCount + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`);

  return new Blob(chunks, { type: "application/pdf" });
}

/** Build a PDF with one printable A4 card per table. */
export async function buildTableQrPdf(items: TableQrPrintItem[]): Promise<Blob> {
  if (items.length === 0) throw new Error("No QR codes to print");
  if (document.fonts?.ready) await document.fonts.ready;
  const logo = await loadImage("/brand/jubran-logo-print.png");
  const images: PdfImage[] = [];
  for (const item of items) {
    // Pages are processed one at a time to keep memory use low.
    images.push(await canvasToPdfImage(renderCardPage(item, logo)));
  }
  return assemblePdf(images);
}

/** Build the PDF and trigger a browser download. */
export async function downloadTableQrPdf(items: TableQrPrintItem[], fileName: string): Promise<void> {
  const blob = await buildTableQrPdf(items);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
