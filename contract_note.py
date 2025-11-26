import fitz  # PyMuPDF
import json
import os  
import glob
from shapely.geometry import box
from shapely.ops import unary_union
from PIL import Image
import argparse

try:
    import pytesseract
    from pytesseract import Output
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    print("ocr processing")

def clean_previous_outputs(output_dir):
    csv_files = glob.glob(os.path.join(output_dir, '*.csv'))
    for file in csv_files:
        os.remove(file)

    combined_png_files = glob.glob(os.path.join(output_dir, 'combined.png'))
    for file in combined_png_files:
        os.remove(file)

def merge_blocks_to_rectangles(blocks, expand_px=25):
    polygons = []
    text_data = []
    for b in blocks:
        x0, y0, x1, y1, text = b[:5]
        if not text.strip():
            continue
        poly = box(x0 - expand_px, y0 - expand_px, x1 + expand_px, y1 + expand_px)
        polygons.append(poly)
        text_data.append((x0, y0, x1, y1, text.strip()))
    if not polygons:
        return []
    merged_shapes = unary_union(polygons)
    merged_blocks = []
    if merged_shapes.geom_type == "Polygon":
        shapes = [merged_shapes]
    elif merged_shapes.geom_type == "MultiPolygon":
        shapes = list(merged_shapes.geoms)
    else:
        shapes = []
    for shape in shapes:
        minx, miny, maxx, maxy = shape.bounds
        region_texts = []
        for (x0, y0, x1, y1, text) in text_data:
            if shape.intersects(box(x0, y0, x1, y1)):
                region_texts.append(text)
        merged_blocks.append((minx, miny, maxx, maxy, " ".join(region_texts)))
    return merged_blocks

def full_page_rectangle(blocks):
    if not blocks:
        return []
    x0 = min(b[0] for b in blocks)
    y0 = min(b[1] for b in blocks)
    x1 = max(b[2] for b in blocks)
    y1 = max(b[3] for b in blocks)
    text = " ".join(b[4].strip() for b in blocks)
    return [(x0, y0, x1, y1, text)]

def find_first_table_block(blocks):
    for idx, b in enumerate(blocks):
        if "Contract Note No:" in str(b[4]):
            return idx
    return -1

def split_page1_special(blocks):
    idx = find_first_table_block(blocks)
    if idx == -1:
        return full_page_rectangle(blocks)
    head_blocks = blocks[:idx]
    table_blocks = blocks[idx:]
    rects = []
    for blks in (head_blocks, table_blocks):
        if not blks:
            continue
        x0 = min(b[0] for b in blks)
        y0 = min(b[1] for b in blks)
        x1 = max(b[2] for b in blks)
        y1 = max(b[3] for b in blks)
        text = " ".join(b[4].strip() for b in blks)
        rects.append((x0, y0, x1, y1, text))
    return rects

def extend_last_rect_to_barcode(blocks, barcode_y=None):
    if not blocks:
        return []
    rects = full_page_rectangle(blocks)
    if barcode_y:
        rects[-1] = (
            rects[-1][0], rects[-1][1], rects[-1][2], barcode_y, rects[-1][4]
        )
    return rects

def save_cropped_images_as_pdf(cropped_image_paths, output_pdf_path):
    """Saves all cropped images as pages in a single PDF"""
    if not cropped_image_paths:
        print("No cropped imgs to save")
        return
    images = [Image.open(img_path).convert('RGB') for img_path in cropped_image_paths]
    images[0].save(output_pdf_path, save_all=True, append_images=images[1:])
    print(f"Saved all cropped images as PDF: {output_pdf_path}")

def extract_text_from_image(image_path):
    """Extract text from image using Tesseract OCR"""
    if not TESSERACT_AVAILABLE:
        return ""
    try:
        text = pytesseract.image_to_string(image_path)
        return text.strip()
    except:
        return ""

def extract_coords_combined(pdf_path, output_dir="out", visualize=True):
    os.makedirs(output_dir, exist_ok=True)
    clean_previous_outputs(output_dir)

    doc = fitz.open(pdf_path)
    all_rectangles = []
    all_cropped_images = []

    for page_index, page in enumerate(doc):
        print(f"Processing page {page_index + 1}...")
        raw_blocks = page.get_text("blocks")

        # check if the page is scanned or digital 
        page_text = page.get_text()
        is_digital = len(page_text.strip()) > 0

        if is_digital:
            print(f"Page {page_index + 1}: Digital PDF (text-based)")
            if page_index == 0:
                merged_blocks = split_page1_special(raw_blocks)
            elif page_index in [1, 2, 4]:
                merged_blocks = full_page_rectangle(raw_blocks)
            elif page_index == 3:
                qr_y_candidates = [b[3] for b in raw_blocks if "zerodha" in b[4].lower() or "date:" in b[4].lower() or "complaints@" in b[4].lower()]
                qr_y = max(qr_y_candidates) + 80 if qr_y_candidates else max(b[3] for b in raw_blocks)
                merged_blocks = extend_last_rect_to_barcode(raw_blocks, barcode_y=qr_y)
            else:
                merged_blocks = merge_blocks_to_rectangles(raw_blocks, expand_px=25)
        else:
            print(f"Page {page_index + 1}: Scanned PDF (image-based)")
            merged_blocks = full_page_rectangle(raw_blocks) if raw_blocks else [(0, 0, page.rect.width, page.rect.height, "")]

        if visualize:
            rect_color = (1, 0, 0)
            text_color = (0, 0, 1)
            for i, b in enumerate(merged_blocks, start=1):
                x0, y0, x1, y1, text = b
                rect = fitz.Rect(x0, y0, x1, y1)
                page.draw_rect(rect, color=rect_color, width=0.8)
                page.insert_text((x0 - 10, y0 - 6), str(i), fontsize=8, color=text_color)
            vis_path = os.path.join(output_dir, f"page_{page_index+1}_boxes.png")
            page.get_pixmap(dpi=150).save(vis_path)
            print(f"Saved visualization: {vis_path}")

        for b in merged_blocks:
            x0, y0, x1, y1, text = b

            if not is_digital and TESSERACT_AVAILABLE:
                # Do ocr when its scannned (skip if its Digital)
                clip_rect = fitz.Rect(x0, y0, x1, y1)
                pix = page.get_pixmap(clip=clip_rect, dpi=150)
                temp_img_path = os.path.join(output_dir, f"temp_page_{page_index+1}rect_{len(all_rectangles)}.png")
                pix.save(temp_img_path)
                ocr_text = extract_text_from_image(temp_img_path)
                os.remove(temp_img_path)

                if ocr_text:
                    text = ocr_text

            rect_dict = {
                "x": round(x0, 2),
                "y": round(y0, 2),
                "width": round(x1 - x0, 2),
                "height": round(y1 - y0, 2),
                "text": text.strip()
            }
            all_rectangles.append(rect_dict)

            clip_rect = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(clip=clip_rect, dpi=150)
            img_path = os.path.join(output_dir, f"page_{page_index+1}rect{len(all_rectangles)}.png")
            pix.save(img_path)
            all_cropped_images.append(img_path)

    grouped_rectangles = []
    for i in range(0, len(all_rectangles), 6):
        line_rects = all_rectangles[i:i+6]
        line_dict = {str(idx+1): r for idx, r in enumerate(line_rects)}
        grouped_rectangles.append(line_dict)

    json_path = os.path.join(output_dir, "all_rectangles_6_per_line.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(grouped_rectangles, f, indent=2)

    print(f"Saved grouped JSON: {json_path}")

    pdf_output_path = os.path.join(output_dir, "all_cropped_images.pdf")
    save_cropped_images_as_pdf(all_cropped_images, pdf_output_path)

    return grouped_rectangles

def find_sender_receiver(all_rectangles):
    keywords_sender = ["sender", "broker", "zerodha", "from"]
    keywords_receiver = ["client", "receiver", "to", "customer"]
    results = {"sender": [], "receiver": []}
    for idx, rect in enumerate(all_rectangles):
        
        text_lower = " ".join(rect["text"].lower().split())
        if any(k in text_lower for k in keywords_sender):
            results["sender"].append((idx + 1, rect))
        elif any(k in text_lower for k in keywords_receiver):
            results["receiver"].append((idx + 1, rect))
    return results
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract and group text coordinates from PDF(s)")
    parser.add_argument(
        "pdfs", nargs='+', help="Path(s) to PDF file(s). You can specify one or more PDFs."
    )
    parser.add_argument(
        "--out", default="out", help="Output folder. Will be created if it doesn't exist."
    )
    parser.add_argument(
        "--no-vis", action="store_true", help="Disable visualization (drawing boxes on PDF pages)"
    )
    args = parser.parse_args()

    for pdf_path in args.pdfs:
        if not os.path.exists(pdf_path):
            print(f"[ERROR] PDF not found: {pdf_path}")
            continue

        print(f"\nProcessing PDF: {pdf_path}")
        rectangles_grouped = extract_coords_combined(pdf_path, args.out, visualize=not args.no_vis)
        addresses = find_sender_receiver([r for line in rectangles_grouped for r in line.values()])

        print("\nSender blocks found:")
        for s in addresses["sender"]:
            print(s)
        print("\nReceiver blocks found:")
        for r in addresses["receiver"]:
            print(r)

    