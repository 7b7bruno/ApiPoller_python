#!/usr/bin/env python3
import cups
import sys
from PIL import Image

def prepare_borderless(photo_path):
    """Resize image to slightly overflow print area"""
    img = Image.open(photo_path)
    
    # CP1500 postcard: 102x153mm at 300dpi = 1204x1807 pixels
    # Add 2% bleed on all sides
    target_w, target_h = 1228, 1843  # ~2% larger
    
    # Determine orientation
    if img.width > img.height:
        target_w, target_h = target_h, target_w
    
    # Resize to cover with bleed
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h
    
    if img_ratio > target_ratio:
        # Image wider - fit height
        new_h = target_h
        new_w = int(target_h * img_ratio)
    else:
        # Image taller - fit width
        new_w = target_w
        new_h = int(target_w / img_ratio)
    
    img = img.resize((new_w, new_h), Image.LANCZOS)
    
    # Center crop to target with bleed
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    
    # Save prepared image
    output_path = '/tmp/print_prepared.jpg'
    img.save(output_path, 'JPEG', quality=95)
    return output_path

def print_photo(photo_path):
    conn = cups.Connection()
    printer_name = 'Canon_SELPHY_CP1500'
    
    # Prepare image with bleed
    prepared = prepare_borderless(photo_path)
    
    options = {
        'media': 'custom_max_102x153mm',
        'print-scaling': 'fill',
    }
    
    try:
        job_id = conn.printFile(printer_name, prepared, 'Photo Print', options)
        print(f"✓ Job {job_id} submitted")
        return job_id
    except Exception as e:
        print(f"✗ Print failed: {e}")
        return None

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python print_photo.py <photo.jpg>")
        sys.exit(1)
    
    print_photo(sys.argv[1])