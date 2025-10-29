#!/usr/bin/env python3
import cups
import sys
import time
from PIL import Image

def track_job_status(conn, job_id, printer_name):
    """Track print job status until completion"""
    print(f"Tracking job {job_id}...")

    # IPP job states
    job_states = {
        3: 'pending',
        4: 'pending-held',
        5: 'processing',
        6: 'processing-stopped',
        7: 'canceled',
        8: 'aborted',
        9: 'completed'
    }

    last_state = None

    while True:
        try:
            jobs = conn.getJobs(which_jobs='all', my_jobs=False)

            if job_id not in jobs:
                # Job no longer in queue - likely completed or removed
                print(f"✓ Job {job_id} completed successfully!")
                break

            job_info = jobs[job_id]
            current_state = job_info.get('job-state')
            state_name = job_states.get(current_state, f'unknown({current_state})')

            # Print status change
            if current_state != last_state:
                print(f"Job {job_id} status: {state_name}")
                last_state = current_state

            # Check for completion or error states
            if current_state == 9:  # completed
                print(f"✓ Job {job_id} completed successfully!")
                break
            elif current_state in [7, 8]:  # canceled or aborted
                print(f"✗ Job {job_id} {state_name}")
                break

            time.sleep(10)  # Poll every second

        except Exception as e:
            print(f"Error tracking job: {e}")
            break

def print_photo(photo_path, track_status=True):
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

        if track_status:
            track_job_status(conn, job_id, printer_name)

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