#!/usr/bin/env python3
import cups
import sys
from PIL import Image

def print_photo(photo_path):
    """Print photo to Canon SELPHY CP1500 with automatic orientation"""
    conn = cups.Connection()
    printer_name = 'Canon_SELPHY_CP1500'
    
    # Detect orientation
    img = Image.open(photo_path)
    width, height = img.size
    is_landscape = width > height
    
    options = {
        'media': 'custom_max_102x153mm',
        'print-scaling': 'fill',
        'orientation-requested': '4' if is_landscape else '3'  # 3=portrait, 4=landscape
    }
    
    print(f"Image size: {width}x{height} ({'landscape' if is_landscape else 'portrait'})")
    
    try:
        job_id = conn.printFile(printer_name, photo_path, 
                               f'Photo Print', options)
        print(f"✓ Job {job_id} submitted: {photo_path}")
        return job_id
    except Exception as e:
        print(f"✗ Print failed: {e}")
        return None

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python print_photo.py <path_to_photo.jpg>")
        sys.exit(1)
    
    photo_path = sys.argv[1]
    print_photo(photo_path)